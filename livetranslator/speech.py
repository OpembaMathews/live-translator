"""Speech to text: local Whisper, and the optional Google and Gemini services.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
import speech_recognition as sr
from .config import LANGUAGES, LANG_TO_WHISPER, MAX_GAIN, PIVOT_LANG, QUIET_PEAK, SPEECH_TAGS, TARGET_PEAK, WHISPER_MIN_LANG_PROB, WHISPER_MIN_LOGPROB, WHISPER_MODEL, WHISPER_NO_SPEECH_MAX, WHISPER_TO_LANG
from .log import log
from .text import has_cjk, script_matches
from .translate import GEMINI_MODEL, GEMINI_URL, _http_json


class WrongLanguage(sr.UnknownValueError):
    """Speech was heard, but not in the language the user pinned.

    Forcing Whisper to a language nobody is speaking yields text in the wrong
    script, which the guards discard. Discarding it silently made the app look
    completely dead, so this carries enough detail to say what happened.
    """

    def __init__(self, expected, sample=""):
        super().__init__()
        self.expected = expected
        self.sample = sample


class NoSpeech(sr.UnknownValueError):
    """The chunk contained no speech at all - room noise, music, silence.

    Distinct from speech that was heard but could not be made out: telling
    someone to "move closer" when the mic only picked up a fan is wrong, and
    at high sensitivity that warning fired dozens of times in a row.
    """


class GoogleSTT:
    """Google's free endpoint. Auto-detect needs two calls because its
    confidence scores do not separate the languages reliably."""

    name = "Online (Google)"

    def __init__(self, recognizer):
        self.recognizer = recognizer

    def _attempt(self, audio, lang):
        try:
            result = self.recognizer.recognize_google(
                audio, language=SPEECH_TAGS[lang], show_all=True
            )
        except sr.RequestError:
            raise
        except Exception:
            return ""
        if not result:
            return ""
        return ((result.get("alternative") or [{}])[0].get("transcript") or "").strip()

    def transcribe(self, audio, lang=None):
        if lang:
            text = self._attempt(audio, lang)
            if not text:
                raise sr.UnknownValueError()
            return lang, text

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {c: pool.submit(self._attempt, audio, c) for c in LANGUAGES}
            results = {c: j.result() for c, j in jobs.items()}

        # Han characters, not confidence, are what identify Chinese here
        for code, text in results.items():
            if LANGUAGES[code]["script"] == "han" and has_cjk(text):
                return code, text
        for code in (PIVOT_LANG, *[c for c in LANGUAGES if c != PIVOT_LANG]):
            if results.get(code):
                return code, results[code]
        raise sr.UnknownValueError()


class GeminiSTT:
    """Speech to text through Gemini, for a user who wants to run fully on the
    cloud and skip the local Whisper download entirely.

    Gemini takes audio inline. We ask it for a language tag on the first line
    and the transcript after, so auto-detect still works. Claude has no audio
    input, which is why this is Gemini only.
    """

    name = "Gemini speech"

    def __init__(self, api_key):
        self.api_key = api_key

    def transcribe(self, audio, lang=None):
        import base64

        wav = audio.get_wav_data(convert_rate=16000, convert_width=2)
        if lang:
            hint = f"The audio is in {LANGUAGES[lang]['name']}."
            want = "Reply with only the transcript."
        else:
            names = ", ".join(v["name"] for v in LANGUAGES.values())
            hint = f"The audio is in one of: {names}."
            codes = "/".join(LANGUAGES)
            want = (f"First line: the language code ({codes}). "
                    f"Second line onward: the transcript.")

        url = f"{GEMINI_URL}/{GEMINI_MODEL}:generateContent"
        data = _http_json(url, {
            "contents": [{"parts": [
                {"text": f"Transcribe this speech. {hint} {want}"},
                {"inline_data": {"mime_type": "audio/wav",
                                 "data": base64.b64encode(wav).decode("ascii")}},
            ]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 400},
        }, {
            "content-type": "application/json",
            "x-goog-api-key": self.api_key,
        })
        cands = data.get("candidates", [])
        if not cands:
            raise sr.UnknownValueError()
        out = "".join(
            part.get("text", "")
            for part in cands[0].get("content", {}).get("parts", [])
        ).strip()
        if not out:
            raise sr.UnknownValueError()

        if lang:
            return lang, out
        first, _, rest = out.partition("\n")
        code = first.strip().lower()[:2]
        detected = code if code in LANGUAGES else PIVOT_LANG
        return detected, (rest.strip() or out)

    def warm_up(self):
        pass


def ensure_av_or_placeholder():
    """Let faster-whisper import even when PyAV cannot load.

    faster-whisper imports PyAV at the top of the package, but only calls it
    to decode audio files. This app always hands Whisper a numpy array, so
    PyAV is never actually used. Smart App Control has been seen blocking one
    of PyAV's DLLs, which took the whole speech engine down with it; this
    swaps in a placeholder so the import succeeds, and the placeholder fails
    loudly if anything ever does try to decode a file.
    """
    if "av" in sys.modules:
        return
    try:
        import av  # noqa: F401
    except (ImportError, OSError) as e:
        import types

        log(f"PyAV unavailable ({type(e).__name__}: {e}); "
            "not needed for live audio, continuing without it")

        def _unavailable(*_a, **_k):
            raise RuntimeError("PyAV is blocked on this PC, so audio files "
                               "cannot be decoded; live capture is unaffected")

        stub = types.ModuleType("av")
        stub.open = _unavailable
        stub.__getattr__ = lambda name: _unavailable
        sys.modules["av"] = stub


class WhisperSTT:
    """faster-whisper, running on the CPU. Detects the language itself, so a
    single pass covers auto mode - but that detection costs about 0.3s, which
    is why a pinned language is noticeably quicker."""

    name = "Local (Whisper)"

    def __init__(self, model_size=WHISPER_MODEL, notify=None):
        self.name = f"Local (Whisper {model_size})"
        ensure_av_or_placeholder()
        from faster_whisper import WhisperModel

        options = dict(device="cpu", compute_type="int8",
                       cpu_threads=max(4, (os.cpu_count() or 4)))
        try:
            # Already downloaded: load without touching the network at all
            self.model = WhisperModel(model_size, local_files_only=True, **options)
            self.downloaded = False
        except Exception:
            # First run on this machine. This is the only step that needs
            # internet; afterwards everything runs offline.
            if notify:
                notify(model_size)
            self.model = WhisperModel(model_size, **options)
            self.downloaded = True

    @staticmethod
    def _samples(audio):
        """AudioData -> float32 mono at 16kHz, lifted if it was recorded quietly."""
        import numpy as np

        raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size:
            peak = float(np.abs(samples).max())
            if 0.0 < peak < QUIET_PEAK:
                samples = samples * min(MAX_GAIN, TARGET_PEAK / peak)
        return samples

    def transcribe(self, audio, lang=None):
        segments, info = self.model.transcribe(
            self._samples(audio),
            beam_size=1,
            language=LANG_TO_WHISPER.get(lang) if lang else None,
            condition_on_previous_text=False,
            # Trims silence either side of the phrase
            vad_filter=True,
            # Without this, audio in the wrong pinned language sends the model
            # into a repetition loop that took over 12s to finish
            repetition_penalty=1.2,
        )

        # Drop segments the model flags as silence or as low-confidence
        kept = []
        for seg in segments:
            if getattr(seg, "no_speech_prob", 0.0) > WHISPER_NO_SPEECH_MAX:
                continue
            if getattr(seg, "avg_logprob", 0.0) < WHISPER_MIN_LOGPROB:
                continue
            kept.append(seg.text)
        text = "".join(kept).strip()
        if not text:
            # VAD stripped everything, or every segment looked invented
            raise NoSpeech()

        if lang:
            detected = lang
        else:
            code = WHISPER_TO_LANG.get(info.language)
            if code is None or info.language_probability < WHISPER_MIN_LANG_PROB:
                # A language this app does not translate. Falling back to
                # English here is what produced Arabic captions fed through
                # the English model.
                raise NoSpeech()
            detected = code

        # The label and the script have to agree, or the model has drifted.
        # When the caller pinned a language, a mismatch almost always means
        # they are speaking the other one, which is worth saying out loud.
        mismatch = not script_matches(detected, text)
        if mismatch:
            if lang:
                raise WrongLanguage(lang, text[:40])
            raise NoSpeech()
        return detected, text

    def stream_words(self, samples, lang=None):
        """Words with end times, for the streaming path.

        Takes samples rather than AudioData because the streaming loop owns a
        rolling buffer and re-reads it; returns the language so the caller can
        pin it for the rest of the utterance and skip detection next time.
        """
        segments, info = self.model.transcribe(
            samples,
            beam_size=1,
            language=LANG_TO_WHISPER.get(lang) if lang else None,
            condition_on_previous_text=False,
            word_timestamps=True,
            vad_filter=True,
        )
        words = []
        for seg in segments:
            if seg.no_speech_prob > WHISPER_NO_SPEECH_MAX:
                continue
            for w in (seg.words or []):
                text = w.word.strip()
                if text:
                    words.append((text, w.end))
        code = lang
        if code is None:
            code = WHISPER_TO_LANG.get(info.language)
            if code is None or info.language_probability < WHISPER_MIN_LANG_PROB:
                code = None
        return code, words

    def warm_up(self):
        import numpy as np

        silence = np.zeros(16000, dtype=np.float32)
        list(self.model.transcribe(silence, beam_size=1, language="en")[0])
