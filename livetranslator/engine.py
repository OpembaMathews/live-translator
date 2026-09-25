"""The engine: what the app does, independent of any window.
"""

import os
import re
from . import settings
from .audio import LevelTap, LoopbackSource, chunk_rms, list_input_devices, list_loopback_devices
from .config import CAPTURE_MODES, DEFAULT_CAPTURE, DEFAULT_INPUT, DEFAULT_RESPONSE, DEFAULT_SENSITIVITY, DEFAULT_WHISPER, LANGUAGES, LANG_NAMES, LOOPBACK_THRESHOLD, MAX_GAIN, MAX_PENDING, PIVOT_LANG, RESPONSE_PRESETS, SENSITIVITY_FLOOR, SENSITIVITY_PRESETS, STREAM_CEILING, STREAM_MAX_BUFFER, STREAM_MIN_FIRST, STREAM_QUIET_FLUSH, STREAM_SENTENCE_END, STREAM_SILENCE_PEAK, STREAM_STEP, STREAM_TARGET_RMS, STREAM_TRIM_AT, STREAM_VOICE_FLOOR, STREAM_VOICE_OVER_NOISE, STT_ENGINE, WAVE_FULL_SCALE, WAVE_SCALE_FLOOR, WAVE_THRESHOLD_SPAN, WHISPER_DOWNLOAD_MB, WHISPER_MODELS
from .log import log
from .session import SessionLog
from .speech import GeminiSTT, GoogleSTT, NoSpeech, WhisperSTT, WrongLanguage
from .text import has_cjk, other_lang
from .translate import build_cloud_engine, build_engine

import queue
import threading
import time

import speech_recognition as sr


class LiveTranslator:
    """The application without a window: capture, recognition, translation,
    transcripts and settings.

    A front end subclasses it and overrides the hooks at the bottom
    (build_ui, on_ui, redraw, show_pair, show_stream, run, close). The
    defaults here only keep state, so the engine also runs headless, which is
    how it is tested.
    """

    def __init__(self, input_mode=DEFAULT_INPUT):
        self.init_state(input_mode)
        self.build_ui()
        self.start_engine()

    def init_state(self, input_mode=DEFAULT_INPUT):
        """Everything the engine keeps track of."""
        self.recognizer = sr.Recognizer()
        self.input_mode = input_mode          # "auto", or a fixed language code
        # "auto" means the opposite language; otherwise always show this one.
        # With three languages "the other one" is ambiguous, so the target
        # became something the user can state outright.
        self.target_mode = "auto"
        self.last_direction = None

        # Audio input selection. device_index None means "whatever Windows
        # considers the default". device_gen is bumped on every change so the
        # capture thread knows to reopen the stream.
        self.device_index = None
        self.device_kind = "mic"
        self.device_label = "System default"
        self.device_gen = 0

        self.engine = None
        self.closing = False
        self._text = ""
        # Status and warnings live on their own small line so a transient
        # notice never wipes the translation the audience is still reading.
        self._notice = "Starting translator"
        self._has_translation = False
        # Starts busy: the engine and speech model take several seconds to load
        self._busy = True
        self.opacity = 1.0
        self.paused = False
        self.sensitivity = DEFAULT_SENSITIVITY
        self.response = DEFAULT_RESPONSE
        self.capture_mode = DEFAULT_CAPTURE
        self._stream_done = {}     # sentence -> translation, for this utterance
        self.session = SessionLog()
        self.jobs = queue.Queue(maxsize=MAX_PENDING)
        self.stt_kind = STT_ENGINE
        self.whisper_choice = DEFAULT_WHISPER
        self._stt_cache = {}

        # Optional cloud translation. Loaded from settings.json - a user with a
        # Claude or Gemini key adds it once and it takes over from the local
        # models. Everything still works with this off.
        cfg = settings.load()
        self.ai_provider = cfg["ai_provider"]
        self.ai_key = cfg["ai_key"]
        self.ai_covers_speech = cfg["ai_covers_speech"]
        self.ai_engine = None          # built lazily, replaced on config change
        self.ai_error = None
        self._ai_speech_warned = False

        # Input meter. _level_raw is written by the audio thread and read by
        # the animation tick; a plain float assignment is atomic in CPython,
        # so no lock is needed for a value that is only ever displayed.
        self._level_raw = 0.0
        self._level = 0.0
        self._level_scale = WAVE_FULL_SCALE
        self._listening = False

    def start_engine(self):
        """Model loading can download a file, so keep it off the UI thread."""
        threading.Thread(
            target=self.start_listening, daemon=True
        ).start()

    def select_device(self, index, label, kind="mic"):
        """Safe to call from any thread - the capture thread uses it to fall back."""
        self.device_index = index
        self.device_kind = kind
        self.device_label = label
        self.device_gen += 1          # tells the capture thread to reopen
        self.show_status(f"Switching to {label}...")

    def set_level(self, rms):
        """Called from the audio thread for every chunk captured."""
        self._level_raw = rms

    def update_text(self, text):
        self._text = text
        if not self.closing:
            self.redraw()

    def resolve_target(self, source_lang):
        """What to display for speech in `source_lang`.

        Returns None when the source already is the requested language - then
        the caption is just the transcript, which is still useful.
        """
        target = other_lang(source_lang) if self.target_mode == "auto"             else self.target_mode
        return None if target == source_lang else target

    def set_target_mode(self, mode):
        self.target_mode = mode
        log(f"target mode -> {mode}")
        self.redraw()

    def set_input_mode(self, mode):
        self.input_mode = mode
        self.last_direction = None
        log(f"input mode -> {mode}")
        # No status text: the chip under the meter already states the direction
        self.redraw()

    def set_notice(self, text):
        if self._notice != text:
            self._notice = text
            if not self.closing:
                self.redraw()

    def show_pair(self, heard, translated):
        """What was heard, and what it became.

        This panel has room for one line, so it shows the translation and the
        transcript only reaches the log. A front end with two lines overrides
        this to show both.
        """
        self.show_translation(translated)

    def show_status(self, text, busy=None):
        """Status and warnings. Before the first translation there is nothing
        worth protecting, so they take the caption; afterwards they drop to
        the small line and leave the last translation on screen.

        A message ending in "..." means work is in progress, so the caption
        area shows a sweeping line rather than spelling out "Loading".
        """
        if busy is None:
            busy = text.rstrip().endswith("...")

        if self._has_translation:
            self.on_ui(lambda: self.set_notice(text))
        else:
            def apply():
                self._busy = busy
                self._notice = text if busy else ""
                self.update_text("" if busy else text)
            self.on_ui(apply)

    def toggle_source(self):
        """Swap between hearing the room and hearing this machine.

        This is the switch people actually flip during use - presenting versus
        watching something - so it belongs on the panel rather than three
        levels into a menu.
        """
        if self.device_kind == "loopback":
            target = next((d for d in list_input_devices()), None)
            kind = "mic"
        else:
            target = next((d for d in list_loopback_devices()), None)
            kind = "loopback"
        if target is None:
            self.show_status("No other audio source available")
            return
        self.select_device(target["index"], target["label"], kind)

    def toggle_listening(self):
        """Stop/start capture. Pausing releases the microphone entirely rather
        than just ignoring audio, so nothing is being recorded while stopped."""
        self.paused = not self.paused
        self.device_gen += 1          # breaks the capture loop out of listen()
        log("paused" if self.paused else "resumed")
        if self.paused:
            self._listening = False
            self._level_raw = 0.0
            self.show_status("Stopped - press play to listen")
        else:
            self.show_status("Starting...")

    def set_response(self, name):
        self.response = name
        self.device_gen += 1          # reopen so the new timings take effect
        pause, chunk, window, overlap = RESPONSE_PRESETS[name]
        log(f"response -> {name} (pause={pause}s mic chunk={chunk}s "
            f"media window={window}s overlap={overlap}s)")
        self.show_status(f"Response: {name}")

    def set_sensitivity(self, name):
        self.sensitivity = name
        self.device_gen += 1          # forces a recalibration on the new setting
        log(f"sensitivity -> {name}")
        self.show_status(f"Sensitivity: {name}")

    def post(self, text):
        """Hand text back to the UI thread, ignoring a closed window."""
        self.on_ui(lambda: self.update_text(text))

    def cloud_active(self):
        return self.ai_provider != "off" and bool(self.ai_key)

    def get_ai_engine(self):
        """Build the cloud engine on demand; cache until the config changes."""
        if not self.cloud_active():
            return None
        if self.ai_engine is None:
            try:
                self.ai_engine = build_cloud_engine(self.ai_provider, self.ai_key)
                self.ai_engine.warm_up()
                self.ai_error = None
                log(f"cloud translation ready: {self.ai_engine.name}")
            except Exception as e:
                self.ai_error = str(e)
                log(f"cloud translation unavailable: {type(e).__name__}: {e}")
                return None
        return self.ai_engine

    def current_translator(self):
        """Cloud if the user enabled it and it works, otherwise the local one."""
        return self.get_ai_engine() or self.engine

    def apply_ai_settings(self, provider, key, covers_speech):
        """Called from the settings dialog. Persists and takes effect at once."""
        settings.save(provider=provider, key=key, covers_speech=covers_speech)
        self.ai_provider = provider
        self.ai_key = key
        self.ai_covers_speech = covers_speech
        self.ai_engine = None          # force a rebuild with the new settings
        self.ai_error = None
        self._ai_speech_warned = False
        self._stt_cache = {k: v for k, v in self._stt_cache.items()
                           if not (isinstance(k, tuple) and k[0] == "gemini_stt")}
        self.device_gen += 1           # reopen the speech path if it changed
        if provider == "off":
            self.show_status("AI translation off - using local models")
        else:
            self.show_status(f"Checking {provider.title()} key...")
            threading.Thread(target=self._verify_ai, daemon=True).start()

    def _verify_ai(self):
        engine = self.get_ai_engine()
        if engine is not None:
            self.show_status(f"AI translation on: {engine.name}")
        else:
            self.show_status(f"AI key rejected: {self.ai_error or 'check the key'}")

    def start_listening(self):
        log(f"--- start: langs={sorted(LANGUAGES)} "
            f"input={self.input_mode} ---")
        try:
            self.engine = build_engine()
            warm = getattr(self.engine, "warm_up", None)
            if warm:
                t0 = time.time()
                warm()
                log(f"engine warmed in {time.time() - t0:.1f}s")
            log("engine ready")
            t0 = time.time()
            self.get_stt()          # load the speech model before listening
            log(f"speech model loaded in {time.time() - t0:.1f}s")
        except Exception as e:
            log(f"engine failed: {type(e).__name__}: {e}")
            self.show_status(f"Translator failed to load: {e}")
            return

        threading.Thread(target=self.recognition_worker, daemon=True).start()
        self.listen_loop()

    def listen_loop(self):
        """Outer loop owns the audio stream; it reopens when the device changes."""
        while not self.closing:
            if self.paused:
                self._listening = False
                time.sleep(0.15)      # mic is closed while stopped
                continue

            gen = self.device_gen
            try:
                if self.device_kind == "loopback":
                    mic = LoopbackSource(self.device_index)
                else:
                    mic = sr.Microphone(device_index=self.device_index)
            except Exception as e:
                self.show_status(f"Cannot open {self.device_label}: {e}")
                if self.device_index is None:
                    return  # even the default failed; nothing left to try
                self.select_device(None, "System default")
                continue

            try:
                with mic as source:
                    if self.device_kind == "loopback":
                        # Calibrating here is actively harmful: whatever is
                        # playing gets treated as room noise and the threshold
                        # ends up so high that quiet passages are dropped.
                        # Loopback silence is digital silence, so fix it low.
                        self.recognizer.energy_threshold = LOOPBACK_THRESHOLD
                        self.recognizer.dynamic_energy_threshold = False
                    else:
                        self.recognizer.dynamic_energy_threshold = True
                        self.recognizer.adjust_for_ambient_noise(source, duration=1)
                        # Scale the calibrated threshold down so a presenter
                        # across the room still crosses it
                        factor = SENSITIVITY_PRESETS.get(self.sensitivity, 1.0)
                        self.recognizer.energy_threshold = max(
                            SENSITIVITY_FLOOR, self.recognizer.energy_threshold * factor
                        )
                    pause, _chunk, _win, _ov = RESPONSE_PRESETS[self.response]
                    self.recognizer.pause_threshold = pause
                    # Calibration tells us what this room's speech threshold is,
                    # so the meter reads the same in a quiet office or a hall
                    self._level_scale = max(
                        WAVE_SCALE_FLOOR,
                        self.recognizer.energy_threshold * WAVE_THRESHOLD_SPAN,
                    )
                    # Meter every chunk on its way to the recogniser
                    source.stream = LevelTap(
                        source.stream, source.SAMPLE_WIDTH, self.set_level
                    )
                    self._listening = True
                    log(f"listening on {self.device_label} "
                        f"(threshold={self.recognizer.energy_threshold:.0f}, "
                        f"meter scale={self._level_scale:.0f})")
                    self.show_status(f"Listening - {self.device_label}")
                    # Plain text: the note is escaped on the way into the
                    # page, so an entity here would show as its own source.
                    self.session.describe(
                        f"{self.device_label} · "
                        f"{self.get_stt().name} · {self.capture_mode}")
                    # Streaming re-reads a buffer, so it needs a recogniser
                    # that can be called repeatedly and cheaply; the cloud ones
                    # charge per call and answer too slowly for that.
                    if (self.capture_mode == "Streaming"
                            and hasattr(self.get_stt(), "stream_words")):
                        self.stream_loop(source, gen)
                    # Media is continuous; a microphone has pauses to cut on
                    elif self.device_kind == "loopback":
                        self.capture_continuous(source, gen)
                    else:
                        self.capture(source, gen)
            except Exception as e:
                if self.closing:
                    return
                self.show_status(f"Audio error on {self.device_label}: {e}")
                time.sleep(1.5)
                # Only fall back if the failure was on a device we chose and
                # the user has not already picked something else meanwhile
                if self.device_index is not None and gen == self.device_gen:
                    self.select_device(None, "System default")
            finally:
                self._listening = False
                self._level_raw = 0.0

    def get_stt(self):
        """Backends are cached: building Whisper costs ~1.3s, so switching
        back and forth should not pay that every time."""
        key = (self.stt_kind, self.whisper_choice)
        if key not in self._stt_cache:
            if self.stt_kind == "whisper":
                backend = WhisperSTT(
                    WHISPER_MODELS[self.whisper_choice],
                    notify=self._announce_download,
                )
                backend.warm_up()
                if getattr(backend, "downloaded", False):
                    log("speech model downloaded; the app is now fully offline")
            else:
                backend = GoogleSTT(self.recognizer)
            self._stt_cache[key] = backend
            log(f"speech backend ready: {backend.name}")
        return self._stt_cache[key]

    def transcribe(self, audio):
        """Return (language, text). A pinned language is passed through, which
        lets Whisper skip its own detection pass and saves roughly 0.3s."""
        lang = None if self.input_mode == "auto" else self.input_mode
        if (self.ai_provider == "gemini" and self.ai_covers_speech
                and self.cloud_active()):
            try:
                return self._gemini_stt().transcribe(audio, lang)
            except Exception as e:
                log(f"  gemini speech failed, using local: {e}")
                if not self._ai_speech_warned:
                    self._ai_speech_warned = True
                    self.show_status("Gemini speech not working - using local. "
                                     "Check the key in the menu.")
        return self.get_stt().transcribe(audio, lang)

    def _gemini_stt(self):
        key = ("gemini_stt", self.ai_key[:8])
        if key not in self._stt_cache:
            self._stt_cache[key] = GeminiSTT(self.ai_key)
            log("gemini speech ready")
        return self._stt_cache[key]

    def set_whisper_model(self, choice):
        self.whisper_choice = choice
        self.stt_kind = "whisper"
        log(f"whisper model -> {choice}")
        self.show_status(f"Loading {choice}...")
        threading.Thread(target=self._prepare_stt, daemon=True).start()

    def _announce_download(self, model_size):
        """First run only: the speech model is fetched once, then cached."""
        size = WHISPER_DOWNLOAD_MB.get(model_size, "")
        detail = f" ({size} MB)" if size else ""
        log(f"downloading speech model {model_size}{detail} - one time only")
        # The trailing "..." puts the sweeping loader on screen
        self.show_status(f"First run: downloading speech model{detail}...")

    def set_stt(self, kind):
        self.stt_kind = kind
        log(f"speech engine -> {kind}")
        self.show_status(
            "Loading local speech model..." if kind == "whisper" else "Using Google speech"
        )
        # Building happens on the worker thread, off the UI
        threading.Thread(target=self._prepare_stt, daemon=True).start()

    def _prepare_stt(self):
        try:
            backend = self.get_stt()
            self.show_status(f"Speech: {backend.name}")
        except Exception as e:
            log(f"speech backend failed: {type(e).__name__}: {e}")
            self.stt_kind = "google"
            self.show_status(f"Local speech unavailable: {e}")

    def capture(self, source, gen):
        """Inner loop: transcribe until closed or the device selection changes."""
        waits = 0
        _pause, chunk_limit, _win, _ov = RESPONSE_PRESETS[self.response]
        while not self.closing and gen == self.device_gen and not self.paused:
            try:
                # A short timeout lets us notice a device switch during silence
                audio = self.recognizer.listen(
                    source, timeout=2, phrase_time_limit=chunk_limit
                )
            except sr.WaitTimeoutError:
                waits += 1
                if waits % 15 == 0:
                    # Nothing has crossed the speech threshold for ~30s. If the
                    # meter is moving, the threshold is the thing to suspect.
                    log(f"no phrase for {waits * 2}s "
                        f"(threshold={self.recognizer.energy_threshold:.0f}, "
                        f"last level={self._level_raw:.0f})")
                continue

            if self.paused or self.closing:
                break  # stopped mid-phrase; drop the audio already in hand

            waits = 0
            seconds = len(audio.frame_data) / (audio.sample_rate * audio.sample_width)
            log(f"phrase captured: {seconds:.1f}s")

            # Hand off and go straight back to listening. Doing recognition
            # here would leave the microphone deaf for the ~1s it takes, so
            # anything said in that window used to be lost outright.
            if self.jobs.full():
                try:
                    self.jobs.get_nowait()      # drop the stalest phrase
                    log("  (behind - dropped an older phrase)")
                except queue.Empty:
                    pass
            self.jobs.put((audio, time.time()))

    def capture_continuous(self, source, gen):
        """Fixed overlapping windows, for media rather than conversation.

        Energy-gated phrase detection assumes pauses between utterances. A
        podcast has none, so it cut mid-word and the tail was lost. Here the
        stream is read without interruption and each window carries the last
        overlap seconds of the previous one, so a word split by one
        boundary is intact in the next window.
        """
        rate, width = source.SAMPLE_RATE, source.SAMPLE_WIDTH
        _p, _c, window, overlap = RESPONSE_PRESETS[self.response]
        need = int(rate * window) * width
        keep = int(rate * overlap) * width
        log(f"  continuous capture: {window}s windows, {overlap}s overlap")
        tail = b""

        while not self.closing and gen == self.device_gen and not self.paused:
            buf = bytearray()
            while (len(buf) < need and not self.closing
                   and gen == self.device_gen and not self.paused):
                try:
                    buf += source.stream.read(source.CHUNK)
                except Exception as e:
                    log(f"  capture read failed: {type(e).__name__}: {e}")
                    return
            if not buf:
                return

            payload = tail + bytes(buf)
            tail = payload[-keep:] if keep else b""

            if self.jobs.full():
                try:
                    self.jobs.get_nowait()
                    log("  (behind - dropped an older window)")
                except queue.Empty:
                    pass
            self.jobs.put((sr.AudioData(payload, rate, width), time.time()))

    def stream_loop(self, source, gen):
        """Rolling buffer, re-read every step, committing what two reads agree.

        Recognition runs here rather than on the worker thread: there is no
        queue to fall behind on, because the buffer itself is the backlog and
        it is trimmed as words are committed. On this machine a read costs
        about 0.7s against a 0.9s step.
        """
        stt = self.get_stt()
        rate, width = source.SAMPLE_RATE, source.SAMPLE_WIDTH
        need = int(rate * STREAM_STEP) * width

        buf = b""              # audio not yet committed
        base = 0.0             # seconds of speech already trimmed off the front
        prev = []              # words from the previous read
        said = []              # committed (word, end) for this utterance
        # How many of the current buffer's words are already committed. Not
        # len(said): trimming restarts the buffer's word numbering while said
        # keeps the whole utterance, and conflating the two dropped a
        # sentence every time the buffer was cut.
        done = 0
        lang = None
        # An utterance ends on silence in the audio, not on the recogniser
        # going quiet. Ending it when no new word was confirmed cut speech
        # into fragments: a quiet mic makes consecutive reads disagree, so
        # nothing commits for a second even though the speaker is still going.
        noise = None
        last_voice = time.time()
        log(f"  streaming captions: {STREAM_STEP}s steps")

        while not self.closing and gen == self.device_gen and not self.paused:
            chunk = bytearray()
            while (len(chunk) < need and not self.closing
                   and gen == self.device_gen and not self.paused):
                try:
                    chunk += source.stream.read(source.CHUNK)
                except Exception as e:
                    log(f"  stream read failed: {type(e).__name__}: {e}")
                    return
            if not chunk:
                return
            buf += bytes(chunk)

            level = chunk_rms(bytes(chunk), width) / 32768.0
            noise = level if noise is None else min(noise, level)
            if level > max(STREAM_VOICE_FLOOR, noise * STREAM_VOICE_OVER_NOISE):
                last_voice = time.time()

            held = len(buf) / (rate * width)      # seconds of source audio
            if not said and held < STREAM_MIN_FIRST:
                # The first read of a phrase decides the language and sets the
                # prefix everything else agrees against; under a couple of
                # seconds there is not enough for the model to be right.
                continue
            samples = self._stream_samples(buf, rate, width)
            if samples.size and float(abs(samples).max()) < STREAM_SILENCE_PEAK:
                # Whisper hallucinates on digital silence - a loopback with
                # nothing playing produced "reverse." out of nothing.
                if said and time.time() - last_voice >= STREAM_QUIET_FLUSH:
                    self.finish_stream(said, lang)
                    buf, base, prev, said, lang = b"", 0.0, [], [], None
                    done, last_voice = 0, time.time()
                else:
                    buf, prev, done = b"", [], 0
                    base += held
                continue
            try:
                found, words = stt.stream_words(samples, lang)
            except Exception as e:
                log(f"  stream recognise failed: {type(e).__name__}: {e}")
                buf, prev = b"", []
                continue
            if found and lang is None:
                lang = found

            # LocalAgreement: the longest prefix this read shares with the last
            agree = 0
            while (agree < min(len(prev), len(words))
                   and prev[agree][0] == words[agree][0]):
                agree += 1
            prev = words

            # Trimming cuts on a word end time, which Whisper places a shade
            # early, so the tail of that word survives into the next buffer and
            # was transcribed a second time ("Please stop me at points. Please
            # stop me at any point"). Anything not past the last commit is
            # audio we have already read.
            spoken = said[-1][1] if said else -1.0
            fresh = [(w, e) for w, e in words[done:agree] if e + base > spoken]
            if words[done:agree]:
                done = agree
            if fresh:
                said.extend((w, e + base) for w, e in fresh)
                self.emit_stream(said, words[agree:], lang)

            # An utterance ends when nothing new has been confirmed for a
            # while: commit whatever is left, translate it properly, and start
            # the next one clean. Continuous speech confirms words about once
            # a second, so this only fires on a real stop.
            ended = (said and time.time() - last_voice >= STREAM_QUIET_FLUSH)
            if ended or held >= STREAM_MAX_BUFFER:
                # The tail goes in unconfirmed, past the same guard: without it
                # a half-heard "Please stop me at points." was kept and then
                # repeated in full by the final read.
                spoken = said[-1][1] if said else -1.0
                said.extend((w, e + base) for w, e in words[agree:]
                            if e + base > spoken)
                if said:
                    self.finish_stream(said, lang)
                buf, base, prev, said, lang = b"", 0.0, [], [], None
                done, last_voice = 0, time.time()
                continue

            # Trim the buffer back to the last committed sentence, so the cost
            # of a read stays flat however long someone talks.
            cut = 0.0
            for w, end in said:
                if w and w[-1] in STREAM_SENTENCE_END:
                    cut = end
            if held > STREAM_TRIM_AT and said:
                # No sentence has ended and the buffer is getting expensive to
                # re-read; cut at the last committed word instead.
                cut = max(cut, said[-1][1])
            if held - (cut - base) > STREAM_STEP and cut > base:
                drop = int((cut - base) * rate) * width
                buf, base, prev, done = buf[drop:], cut, [], 0

    @staticmethod
    def _stream_samples(raw, rate, width):
        """Bytes -> 16kHz float32, lifted if the input is quiet.

        Resampling is the whole point: a microphone commonly runs at 44.1kHz,
        and handing those samples to Whisper as if they were 16kHz stretches
        speech to nearly three times its length. It hears a drone and returns
        nothing, which is exactly what happened. The phrase path never hit
        this because AudioData resamples on the way out.
        """
        import numpy as np

        data = sr.AudioData(raw, rate, width).get_raw_data(
            convert_rate=16000, convert_width=2)
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size:
            rms = float(np.sqrt(np.mean(samples ** 2)))
            peak = float(np.abs(samples).max())
            if rms > 0.0 and rms < STREAM_TARGET_RMS:
                gain = min(MAX_GAIN, STREAM_TARGET_RMS / rms)
                if peak > 0.0:
                    gain = min(gain, STREAM_CEILING / peak)   # no clipping
                samples = samples * gain
        return samples

    def stream_lang(self, heard, lang):
        """The language to translate from when detection would not commit.

        Whisper only reports a language it is confident about, and on the
        short buffers streaming works with it often is not. The utterance was
        then shown untranslated, which looks like the app ignoring you. The
        script of what came back settles it well enough: Han characters mean
        Chinese, anything else means the pivot language.
        """
        if lang:
            return lang
        if self.input_mode != "auto":
            return self.input_mode
        for code, meta in LANGUAGES.items():
            if meta["script"] == "han" and has_cjk(heard):
                return code
        return PIVOT_LANG

    def stream_translate(self, heard, lang):
        """Translate sentence by sentence, reusing finished ones.

        Re-translating the whole prefix each step made earlier sentences
        rewrite themselves under the reader ("I want to talk" became "I want
        to say" became "I want to chat"). Splitting means a finished sentence
        is translated once and then left alone, and only the sentence being
        spoken can still change.
        """
        lang = self.stream_lang(heard, lang)
        target = self.resolve_target(lang)
        self.last_direction = (lang, target or lang)
        if not target:
            return heard
        parts = re.findall(r"[^.!?。！？]*[.!?。！？]|[^.!?。！？]+", heard)
        out = []
        for part in parts:
            chunk = part.strip()
            if not chunk:
                continue
            if chunk in self._stream_done:
                out.append(self._stream_done[chunk])
                continue
            try:
                done = self.current_translator().translate(chunk, lang, target)
            except Exception as e:
                log(f"  stream translate failed: {type(e).__name__}: {e}")
                return ""
            if chunk[-1] in STREAM_SENTENCE_END:
                self._stream_done[chunk] = done      # settled; never redo it
            out.append(done)
        return " ".join(x for x in out if x)

    def emit_stream(self, said, pending, lang):
        """A partial line while the speaker is still going."""
        heard = " ".join(w for w, _ in said)
        tail = " ".join(w for w, _ in pending)
        translated = self.stream_translate(heard, lang)
        self.on_ui(lambda h=heard, t=translated, p=tail:
                   self.show_stream(h, t, p))

    def finish_stream(self, said, lang):
        heard = " ".join(w for w, _ in said)
        log(f"  stream utterance [{lang}]: {heard!r}")
        translated = self.stream_translate(heard, lang)
        log(f"  stream shown: {translated!r}")
        source = self.stream_lang(heard, lang)
        self.session.add(heard, translated, source,
                         self.resolve_target(source))
        self._stream_done.clear()      # next utterance starts with no history
        self.on_ui(lambda h=heard, t=translated or heard: self.show_pair(h, t))

    def show_stream(self, heard, translated, pending=""):
        """Partial captions. This panel has one line and no place for the
        unconfirmed tail, so it shows the translation so far; a front end with
        two lines overrides this."""
        self.show_translation(translated or heard)

    def save_transcript(self, open_it=True):
        """Write the readable copy now, and show it. Safe mid-session."""
        path = self.session.save()
        if not path:
            self.show_status("Nothing transcribed yet")
            return None
        log(f"transcript saved: {path}")
        if open_it:
            try:
                os.startfile(path)      # noqa: S606 - the user asked for it
            except Exception as e:
                log(f"could not open the transcript: {e}")
        return path

    def set_capture_mode(self, name):
        if name not in CAPTURE_MODES:
            return
        self.capture_mode = name
        self.device_gen += 1          # restart capture in the new mode
        log(f"capture mode -> {name}")

    def recognition_worker(self):
        """Consumes captured phrases: transcribe, translate, display."""
        unclear = 0
        quiet = 0
        while not self.closing:
            try:
                audio, captured_at = self.jobs.get(timeout=0.3)
            except queue.Empty:
                continue
            if self.closing:
                return

            try:
                source_lang, text = self.transcribe(audio)
            except WrongLanguage as wrong:
                other = LANG_NAMES[other_lang(wrong.expected)]
                log(f"  -> not {wrong.expected}; sounded like {other}: "
                    f"{wrong.sample!r}")
                self.show_status(
                    f"That sounded like {other}. Switch Speaking to Auto detect")
                continue
            except NoSpeech:
                quiet += 1
                if quiet in (6, 30) and self.device_kind == "loopback":
                    self.show_status("No speech on this output - is playback"
                                     " going to a different device?")
                elif quiet == 12:
                    log(f"  -> {quiet} chunks with no speech in them")
                continue
            except sr.UnknownValueError:
                unclear += 1
                log(f"  -> speech not understood (x{unclear})")
                # Silently ignoring this is what made the app look dead: audio
                # was arriving and being sent, it just came back unrecognised.
                if unclear >= 2:
                    self.show_status("Not clear enough - move closer"
                                     " or pick another mic")
                continue
            except sr.RequestError as e:
                log(f"  -> speech API unreachable: {e}")
                self.show_status("Speech service unreachable - check internet")
                continue
            except Exception as e:
                log(f"  -> recognition failed: {type(e).__name__}: {e}")
                continue

            unclear = quiet = 0
            target_lang = self.resolve_target(source_lang)
            self.last_direction = (source_lang, target_lang or source_lang)
            heard_at = time.time()
            log(f"  heard [{source_lang}] +{heard_at - captured_at:.2f}s: {text!r}")
            try:
                # Same language in and out: show the transcript untranslated
                if target_lang is None:
                    translated = text
                else:
                    engine = self.current_translator()
                    try:
                        translated = engine.translate(
                            text, source_lang, target_lang)
                    except Exception as cloud_err:
                        if engine is self.engine:
                            raise
                        # Cloud call failed mid-session - drop to local so
                        # captions keep flowing, and say so once
                        log(f"  cloud translate failed, using local: {cloud_err}")
                        self.ai_error = str(cloud_err)
                        self.ai_engine = None
                        self.show_status("AI translation dropped - back to local")
                        translated = self.engine.translate(
                            text, source_lang, target_lang)
            except Exception as e:
                log(f"  -> translation failed: {type(e).__name__}: {e}")
                self.show_status(f"Translation failed: {e}")
                continue

            log(f"  translated +{time.time() - heard_at:.2f}s "
                f"(total {time.time() - captured_at:.2f}s): {translated!r}")
            self.session.add(text, translated, source_lang, target_lang)
            self.on_ui(lambda h=text, t=translated: self.show_pair(h, t))

    def build_ui(self):
        """Create the window. The engine has none."""

    def on_ui(self, fn):
        """Run fn on the UI thread. Without a UI there is no thread to hop to."""
        if not self.closing:
            fn()

    def redraw(self):
        """Repaint after a state change."""

    def show_translation(self, text):
        self._has_translation = True
        self._notice = ""
        self._busy = False
        self.update_text(text)

    def set_opacity(self, value):
        self.opacity = max(0.35, min(1.0, value))

    def run(self):
        raise NotImplementedError("the front end provides the event loop")

    def close(self):
        """Stop capture and keep the transcript."""
        self.closing = True
        saved = self.session.save()
        if saved:
            log(f"transcript saved: {saved}")


def apply_startup_options(app, argv):
    """--device <text> picks an input by name; --input auto|en|zt sets language."""
    if "--input" in argv:
        mode = argv[argv.index("--input") + 1]
        if mode == "auto" or mode in LANGUAGES:
            app.input_mode = mode
            log(f"startup: input mode {mode}")

    if "--target" in argv:
        mode = argv[argv.index("--target") + 1]
        if mode == "auto" or mode in LANGUAGES:
            app.target_mode = mode
            log(f"startup: target {mode}")

    if "--device" in argv:
        wanted = argv[argv.index("--device") + 1].lower()
        options = list(list_input_devices()) + list(list_loopback_devices())
        match = next((d for d in options if wanted in d["label"].lower()), None)
        if match:
            app.select_device(match["index"], match["label"],
                              match.get("kind", "mic"))
            log(f"startup: device {match['label']}")
        else:
            log(f"startup: no device matching {wanted!r}; "
                f"have {[d['label'] for d in options]}")
