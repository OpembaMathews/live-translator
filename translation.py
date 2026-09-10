import math
import os
import pathlib
import sys
import json as _json
import queue
import re
import urllib.error
import urllib.request
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor

import speech_recognition as sr

import appconfig

try:
    import audioop  # removed from the stdlib in Python 3.13
except ImportError:  # pragma: no cover - depends on interpreter version
    audioop = None
    import numpy as _np


def chunk_rms(data, sample_width):
    """Loudness of one audio chunk, 0..32767 for 16-bit samples."""
    if audioop is not None:
        return audioop.rms(data, sample_width)
    dtype = _np.int16 if sample_width == 2 else _np.uint8
    samples = _np.frombuffer(data, dtype=dtype).astype(_np.float64)
    if samples.size == 0:
        return 0.0
    return float(_np.sqrt(_np.mean(samples ** 2)))


class LevelTap:
    """Wraps an audio stream and reports the loudness of every chunk read.

    speech_recognition owns the stream while listening, so this sits in the
    middle rather than opening a second stream on the same device.
    """

    def __init__(self, stream, sample_width, report):
        self._stream = stream
        self._width = sample_width
        self._report = report

    def read(self, size, *args, **kwargs):
        data = self._stream.read(size, *args, **kwargs)
        try:
            self._report(chunk_rms(data, self._width))
        except Exception:
            pass  # never let metering break capture
        return data

    def close(self):
        return self._stream.close()

    def __getattr__(self, name):
        return getattr(self._stream, name)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Which translation backend to use:
#   "lean"   - the same Argos models driven straight through CTranslate2.
#              Identical output, no torch/stanza/spacy, ~760MB smaller.
#   "argos"  - offline via argostranslate (pulls in torch through stanza)
#   "google" - googletrans, unofficial scrape of Google Translate (flaky)
#   "deepl"  - DeepL API, best quality, needs DEEPL_API_KEY env var
ENGINE = "lean"

# The two languages this app switches between. Whichever one is spoken, the
# other is what gets shown.
# Every language the app handles. English is the pivot: Argos routes any pair
# through it, so only en<->X packs are needed rather than one per combination.
LANGUAGES = {
    "en": {"name": "English",   "short": "EN",   "whisper": "en",
           "speech": "en-US",   "script": "latin"},
    "zt": {"name": "中文", "short": "中文", "whisper": "zh",
           "speech": "zh-TW",   "script": "han"},
    "sw": {"name": "Kiswahili", "short": "SW",   "whisper": "sw",
           # Google wants a region; bare "sw" is not a valid recogniser tag
           "speech": "sw-KE",   "script": "latin"},
}
PIVOT_LANG = "en"
# Which language "Auto" pairs English with, when English is being spoken
DEFAULT_SECONDARY = "zt"

LANG_A, LANG_B = "en", "zt"      # kept: the pair the bundled packs cover
LANG_NAMES = {c: v["name"] for c, v in LANGUAGES.items()}
LANG_SHORT = {c: v["short"] for c, v in LANGUAGES.items()}
SPEECH_TAGS = {c: v["speech"] for c, v in LANGUAGES.items()}

# "auto" detects per phrase; otherwise force one side
DEFAULT_INPUT = "auto"

# Speech-to-text backend:
#   "whisper" - faster-whisper running locally. No network, better punctuation,
#               and language detection built in. Reuses the ctranslate2 that
#               Argos already pulls in, so it is a light addition.
#   "google"  - the free Google endpoint. Needs internet.
STT_ENGINE = "whisper"
# On short English phrases "tiny" is fine and quickest. On continuous Chinese
# it degrades badly - it drifts into simplified characters and mishears words
# ("人工智慧" became "人工智会") - so "base" is the default. Measured on 4s
# chunks: tiny 0.35s vs base 0.69s with a pinned language.
WHISPER_MODELS = {"Fast (tiny)": "tiny", "Accurate (base)": "base"}
DEFAULT_WHISPER = "Accurate (base)"
# Rounded download sizes, shown once on first run so the wait is explained
WHISPER_DOWNLOAD_MB = {"tiny": "75", "base": "145"}
WHISPER_MODEL = WHISPER_MODELS[DEFAULT_WHISPER]
# Whisper reports plain "zh"; Argos wants our traditional-Chinese code
WHISPER_TO_LANG = {v["whisper"]: c for c, v in LANGUAGES.items()}
WHISPER_TO_LANG["yue"] = "zt"          # Cantonese also reads as traditional
LANG_TO_WHISPER = {c: v["whisper"] for c, v in LANGUAGES.items()}

# Whisper invents speech when fed noise, and it does so in whatever language it
# happens to guess - Arabic, Russian and Korean all turned up in testing. These
# thresholds throw away segments the model itself is unsure about, and any
# result in a language this app does not handle.
# A quiet microphone yields samples so small that Whisper's voice detector
# treats real speech as silence. Measured on this laptop: normal speech peaked
# at 3.7% of full scale. Lift quiet audio before recognition rather than
# relying on the Windows input level being set correctly.
QUIET_PEAK = 0.30          # below this the clip is considered under-recorded
TARGET_PEAK = 0.50         # what we lift it to
MAX_GAIN = 15.0            # cap, so near-silence is not amplified into noise

WHISPER_MIN_LANG_PROB = 0.55   # below this the detection is a coin toss
WHISPER_NO_SPEECH_MAX = 0.60   # segment is probably silence
WHISPER_MIN_LOGPROB = -1.0     # segment is probably invented
LATIN_PATTERN = re.compile(r"[A-Za-z]")

# Han characters. Google's zh-TW model happily returns Latin text when you
# speak English at it, so the presence of Han glyphs - not the confidence
# score - is what actually identifies Chinese speech.
CJK_PATTERN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


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


def has_cjk(text):
    return bool(CJK_PATTERN.search(text or ""))


def translation_pairs():
    """Direct routes to verify at startup: every language to and from the pivot.

    Other combinations (Chinese to Kiswahili, say) are reached by pivoting, so
    they need no pack of their own.
    """
    return ([(c, PIVOT_LANG) for c in LANGUAGES if c != PIVOT_LANG]
            + [(PIVOT_LANG, c) for c in LANGUAGES if c != PIVOT_LANG])


def other_lang(code):
    """The language Auto shows for a given source.

    English is the hub, so anything else becomes English, and English becomes
    whichever second language is configured. That keeps the old two-language
    behaviour intact while leaving room for a third.
    """
    return DEFAULT_SECONDARY if code == PIVOT_LANG else PIVOT_LANG


def script_of(text):
    return "han" if has_cjk(text) else "latin"


def script_matches(code, text):
    """Whether transcribed text is written in the script that language uses.

    English and Kiswahili share the Latin script, so this only catches gross
    mismatches - it cannot tell those two apart. Language detection does that.
    """
    expected = LANGUAGES.get(code, {}).get("script")
    if expected == "han":
        return has_cjk(text)
    if expected == "latin":
        return bool(LATIN_PATTERN.search(text))
    return True

# Launched from a shortcut we run under pythonw.exe, which has no console, so
# print() goes nowhere. Everything interesting is appended here instead.
# In a frozen build __file__ points inside the bundle, so the log would be
# buried in _internal. Sit next to the executable instead.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(APP_DIR, "translator.log")
LOG_LOCK = threading.Lock()


# Windows consoles default to cp1252, which cannot represent Chinese. Any
# log line carrying a translation would raise UnicodeEncodeError and take the
# process down, so make the streams tolerant before anything writes to them.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass  # frozen windowed builds have no streams to reconfigure


def log(message):
    stamp = time.strftime("%H:%M:%S")
    line = f"{stamp}  {message}"
    try:
        print(line)
    except (OSError, ValueError, UnicodeEncodeError):
        pass  # no console, or one that cannot render the text
    try:
        with LOG_LOCK, open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # logging must never take the app down

# Appearance
PANEL_BG = "#0D1B2A"      # deep navy
TEXT_COLOR = "#FAFCFF"    # near-white; the caption is the point of the panel
CAPTION_SHADOW = "#04090F"  # sits behind the caption to lift it off the panel
CAPTION_WEIGHT = "bold"
RECORD_COLOR = "#E8564F"   # record dot; red reads as "capture" everywhere
RECORD_HOT = "#FF7B74"

# Border that lights up with the input level. Segments are lit by a wave
# travelling around the perimeter, scaled by how loud the input is, so the
# whole frame reacts to speech while staying invisible in silence.
BORDER_SEGMENTS = 72
BORDER_INSET = 2.5
# Measured from a screenshot: the compositor rounds this window with a ~9
# physical px radius, which is 7.2 logical px at 125% scaling. A border inset
# by d stays parallel to that edge only if its own radius is (7.2 - d).
WINDOW_CORNER_RADIUS = 7.2
BORDER_RADIUS = WINDOW_CORNER_RADIUS - BORDER_INSET
BORDER_CORNER_POINTS = 7   # corners are ~1% of the perimeter; sample them anyway
BORDER_WIDTH = 2.0
BORDER_SPEED = 0.42        # laps per second
BORDER_IDLE = 0.10         # faint shimmer while listening but silent

# Indeterminate loader shown instead of the word "Loading". The bar sweeps a
# track; there is no percentage to show because model loading gives no progress.
LOADER_TRACK = "#1B3149"   # the groove
LOADER_FILL = "#63D2FF"    # the travelling segment, matching the meter
LOADER_WIDTH_FRAC = 0.46   # track width as a fraction of the caption area
LOADER_SEG_FRAC = 0.32     # travelling segment, as a fraction of the track
LOADER_THICKNESS = 3
LOADER_SPEED = 0.55        # sweeps per second
ACCENT = "#5C7A99"        # muted steel blue for the controls
ACCENT_HOT = "#9EC1E8"    # brighter blue when hovering a control
CONTROL_BG = "#16293D"    # subtle plate behind the control cluster
CONTROL_HOT = "#22405E"   # same plate, hovered
NOTICE_COLOR = "#6E8CAB"  # status line, deliberately quieter than the caption
NOTICE_STRIP = 17         # height reserved at the bottom for the status line

# Latin text uses the modern Windows UI face; CJK needs a font that actually
# carries the glyphs, or Tk silently falls back to something ugly.
LATIN_FONTS = ("Segoe UI Variable Display", "Segoe UI", "Calibri", "Arial")
CJK_FONTS = ("Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans TC", "PMingLiU")
CJK_LANGS = ("zt", "zh", "ja", "ko")

# Named window sizes: (width, height, font size)
SIZE_PRESETS = {
    "Small": (620, 78, 18),
    "Medium": (900, 112, 26),
    "Large": (1240, 162, 36),
}
DEFAULT_PRESET = "Medium"

MIN_WIDTH, MIN_HEIGHT = 320, 60
EDGE_PAD = 18      # inner text padding
GRIP_ZONE = 20     # size of the bottom-right resize hot corner
CONTROL_SIZE = 30  # square hit area for each on-panel button
CONTROL_GAP = 3

# How hard to listen. A presenter standing away from the laptop is much
# quieter at the mic than someone leaning into it, so the speech threshold
# has to come down or phrases never trigger.
SENSITIVITY_PRESETS = {
    "Normal": 1.0,
    "Far (presentation)": 0.5,
    "Very far": 0.3,
}
DEFAULT_SENSITIVITY = "Far (presentation)"
SENSITIVITY_FLOOR = 35.0   # below this we would trigger on room tone
# Loopback has no room noise at all, so a fixed low threshold beats calibration
LOOPBACK_THRESHOLD = 120.0

# How long to wait for you to stop talking, and how long a single chunk may
# run. Shorter means captions appear sooner but sentences get chopped, which
# costs some translation quality; longer reads better but lags.
#                     (silence before cutting, max chunk seconds)
# (silence before cutting, max mic chunk, media window, media overlap)
# Media latency is window + ~0.7s of processing, so the window IS the delay.
# Measured coverage against a reference: 2.5s->89%, 3.0s->90%, 5.0s->94%, so
# most of the accuracy survives a much shorter window.
RESPONSE_PRESETS = {
    "Fast": (0.45, 3.0, 2.5, 0.7),
    "Balanced": (0.65, 4.0, 3.0, 0.8),
    "Accurate": (0.90, 5.0, 5.0, 1.2),
}
DEFAULT_RESPONSE = "Balanced"
# Media (a podcast, a video) is continuous speech with few natural pauses, so
# waiting for silence chops words in half at the cut. Reading fixed windows
# that overlap means every moment of audio appears whole in some window.
# Measured on a real clip: 42% word coverage without overlap, 67% with.
# Cap on phrases waiting to be transcribed. Falling behind is worse than
# dropping audio for live captions, so the oldest is discarded first.
MAX_PENDING = 3

# Live input meter ("radio waves" radiating from a dot on the left)
WAVE_ZONE = 78     # horizontal space reserved for the meter
WAVE_RINGS = 4
WAVE_FPS = 25
WAVE_IDLE = "#26405C"   # ring colour when nothing is being heard
WAVE_PEAK = "#63D2FF"   # ring colour at full volume
# Fallback full-scale RMS. In practice the meter rescales itself against the
# recogniser's ambient-noise calibration, since a quiet room and a loud hall
# differ by more than an order of magnitude.
WAVE_FULL_SCALE = 900.0
WAVE_SCALE_FLOOR = 400.0      # never get so twitchy that room tone lights it
WAVE_THRESHOLD_SPAN = 6.0     # full rings at ~6x the speech-detection threshold


# ---------------------------------------------------------------------------
# Speech to text
# ---------------------------------------------------------------------------
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
            tag_line = ""
        else:
            names = ", ".join(v["name"] for v in LANGUAGES.values())
            hint = f"The audio is in one of: {names}."
            codes = "/".join(LANGUAGES)
            want = (f"First line: the language code ({codes}). "
                    f"Second line onward: the transcript.")
            tag_line = "tag"

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


class WhisperSTT:
    """faster-whisper, running on the CPU. Detects the language itself, so a
    single pass covers auto mode - but that detection costs about 0.3s, which
    is why a pinned language is noticeably quicker."""

    name = "Local (Whisper)"

    def __init__(self, model_size=WHISPER_MODEL, notify=None):
        self.name = f"Local (Whisper {model_size})"
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

    def warm_up(self):
        import numpy as np

        silence = np.zeros(16000, dtype=np.float32)
        list(self.model.transcribe(silence, beam_size=1, language="en")[0])


# ---------------------------------------------------------------------------
# Translation engines
# ---------------------------------------------------------------------------
class Translator:
    """Base interface. Subclasses implement translate()."""

    def translate(self, text, source, target):
        raise NotImplementedError


class ArgosEngine(Translator):
    """Offline neural translation. Downloads a language pack on first use."""

    def __init__(self, pairs):
        import argostranslate.package
        import argostranslate.translate

        self._translate = argostranslate.translate.translate
        self._pairs = list(pairs)
        for source, target in self._pairs:
            self._ensure_package(argostranslate.package, source, target)

    @staticmethod
    def _ensure_package(package, source, target):
        installed = package.get_installed_packages()
        if any(p.from_code == source and p.to_code == target for p in installed):
            return

        print(f"Downloading Argos language pack {source}->{target} (one time)...")
        package.update_package_index()
        match = next(
            (
                p
                for p in package.get_available_packages()
                if p.from_code == source and p.to_code == target
            ),
            None,
        )
        if match is None:
            raise RuntimeError(f"No Argos package available for {source}->{target}")
        package.install_from_path(match.download())
        print("Language pack ready.")

    def translate(self, text, source, target):
        return self._translate(text, source, target)

    def warm_up(self):
        """Argos loads each model lazily, which cost ~4s on the first real
        phrase. Pay that during startup instead, for every direction, while
        the panel still says it is loading."""
        for source, target in self._pairs:
            self._translate("warm up", source, target)


class LeanEngine(Translator):
    """Runs Argos's models directly on CTranslate2, skipping argostranslate.

    argostranslate imports stanza for sentence splitting, stanza requires
    torch, and torch alone is 526MB - about two thirds of a frozen build,
    for a feature we do not use. The language packs themselves are plain
    CTranslate2 models with a SentencePiece vocabulary, so they load without
    any of that. Dropping this chain removes roughly 760MB of dependencies.
    """

    SPLIT = re.compile(r"(?<=[.!?。！？；;])\s*")
    MARKER = "▁"          # SentencePiece word-boundary marker

    def __init__(self, pairs):
        self._packages = self._discover()
        self._loaded = {}
        self._pairs = list(pairs)
        for source, target in self._pairs:
            if not self._route(source, target):
                raise RuntimeError(
                    f"No CTranslate2 package for {source}->{target}. "
                    f"Have: {sorted(self._packages)}"
                )

    @staticmethod
    def _roots():
        """Where translation packs may live, most specific first.

        A frozen build ships its own packs so it works on a machine that has
        never seen argostranslate; a source checkout falls back to whatever
        argostranslate has installed.
        """
        roots = []
        if getattr(sys, "frozen", False):
            bundled = pathlib.Path(
                getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
            roots.append(bundled / "argos")
        roots.append(pathlib.Path.home() / ".local" / "share"
                     / "argos-translate" / "packages")
        return [r for r in roots if r.exists()]

    @classmethod
    def _discover(cls):
        """Map (from, to) -> package directory by reading each metadata.json."""
        import json

        found = {}
        for root in cls._roots():
            for pkg in root.iterdir():
                meta = pkg / "metadata.json"
                if not meta.is_file():
                    continue
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                key = (data.get("from_code"), data.get("to_code"))
                # First root wins, so a bundled pack beats an installed one
                if None not in key and key not in found:
                    found[key] = pkg
        return found

    def _route(self, source, target):
        """Direct pair if we have it, otherwise pivot through English."""
        if source == target:
            return []
        if (source, target) in self._packages:
            return [(source, target)]
        if (source, "en") in self._packages and ("en", target) in self._packages:
            return [(source, "en"), ("en", target)]
        return None

    def _model(self, pair):
        if pair not in self._loaded:
            import ctranslate2
            import sentencepiece

            pkg = self._packages[pair]
            self._loaded[pair] = (
                ctranslate2.Translator(str(pkg / "model"), device="cpu",
                                       compute_type="int8"),
                sentencepiece.SentencePieceProcessor(
                    str(pkg / "sentencepiece.model")),
            )
        return self._loaded[pair]

    def _hop(self, text, pair):
        translator, sp = self._model(pair)
        chunks = [c for c in self.SPLIT.split(text) if c.strip()] or [text]
        results = translator.translate_batch(
            [sp.encode(c, out_type=str) for c in chunks], beam_size=4,
        )
        out = []
        for res in results:
            # decode() leaves the boundary marker on these vocabularies, so
            # join the pieces the way SentencePiece itself would
            joined = "".join(res.hypotheses[0]).replace(self.MARKER, " ")
            out.append(joined.strip())
        return " ".join(x for x in out if x)

    def translate(self, text, source, target):
        route = self._route(source, target)
        if route is None:
            raise RuntimeError(f"No route for {source}->{target}")
        for pair in route:
            text = self._hop(text, pair)
        return text

    def warm_up(self):
        for source, target in self._pairs:
            self.translate("warm up", source, target)


class GoogleTransEngine(Translator):
    """Unofficial googletrans library. Requires internet; breaks often."""

    def __init__(self):
        from googletrans import Translator as GTranslator

        self._client = GTranslator()

    def translate(self, text, source, target):
        return self._client.translate(text, src=source, dest=target).text


class DeepLEngine(Translator):
    """Official DeepL API. Free tier: 500k chars/month. Needs DEEPL_API_KEY."""

    def __init__(self):
        import deepl

        key = os.environ.get("DEEPL_API_KEY")
        if not key:
            raise RuntimeError("DEEPL_API_KEY environment variable is not set")
        self._client = deepl.Translator(key)

    def translate(self, text, source, target):
        # DeepL wants uppercase target codes, and some are region-qualified
        return self._client.translate_text(text, target_lang=target.upper()).text


# ---------------------------------------------------------------------------
# Optional cloud translation - only used when a user has added an API key
# ---------------------------------------------------------------------------
CLAUDE_MODEL = "claude-haiku-4-5-20251001"   # fast and cheap, ample for translation
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
LLM_TIMEOUT = 20


def _http_json(url, payload, headers):
    body = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        return _json.loads(resp.read().decode("utf-8"))


class LLMEngine(Translator):
    """Translation through a large language model the user pays for directly.

    No package to install and no model to download - the whole appeal for
    someone who already has a Claude or Gemini subscription. One HTTP call per
    phrase. Quality is well above the offline models, especially for languages
    like Kiswahili where the small local models struggle.
    """

    name = "cloud"

    def __init__(self, api_key):
        if not api_key:
            raise RuntimeError("no API key configured")
        self.api_key = api_key

    @staticmethod
    def _prompt(text, source, target):
        s = LANGUAGES.get(source, {}).get("name", source)
        t = LANGUAGES.get(target, {}).get("name", target)
        return (
            f"Translate this {s} text into {t}. It is a live caption, so keep "
            f"it natural and concise. Reply with only the translation, no "
            f"notes or quotation marks.\n\n{text}"
        )

    def translate(self, text, source, target):
        out = self._call(self._prompt(text, source, target)).strip()
        # Models sometimes wrap the answer even when told not to
        if len(out) > 1 and out[0] in "\"'“「" and out[-1] in "\"'”」":
            out = out[1:-1].strip()
        return out or text

    def _call(self, prompt):
        raise NotImplementedError

    def warm_up(self):
        # One tiny call confirms the key works before the first real phrase
        self._call("Reply with the single word: ok")


class ClaudeEngine(LLMEngine):
    name = "Claude"

    def _call(self, prompt):
        data = _http_json(CLAUDE_URL, {
            "model": CLAUDE_MODEL,
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}],
        }, {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        })
        return "".join(
            block.get("text", "") for block in data.get("content", [])
        )


class GeminiEngine(LLMEngine):
    name = "Gemini"

    def _call(self, prompt):
        url = f"{GEMINI_URL}/{GEMINI_MODEL}:generateContent"
        data = _http_json(url, {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 400, "temperature": 0.3},
        }, {
            "content-type": "application/json",
            "x-goog-api-key": self.api_key,
        })
        cands = data.get("candidates", [])
        if not cands:
            raise RuntimeError(data.get("promptFeedback", "no response"))
        return "".join(
            part.get("text", "")
            for part in cands[0].get("content", {}).get("parts", [])
        )


def build_cloud_engine(provider, api_key):
    if provider == "claude":
        return ClaudeEngine(api_key)
    if provider == "gemini":
        return GeminiEngine(api_key)
    raise ValueError(f"unknown cloud provider {provider!r}")


def build_engine(name, pairs):
    if name == "lean":
        return LeanEngine(pairs)
    if name == "argos":
        return ArgosEngine(pairs)
    if name == "google":
        return GoogleTransEngine()
    if name == "deepl":
        return DeepLEngine()
    raise ValueError(f"Unknown ENGINE {name!r}; expected 'argos', 'google' or 'deepl'")


# ---------------------------------------------------------------------------
# Audio input devices
# ---------------------------------------------------------------------------
# Windows exposes the same physical microphone once per host API, so a raw
# device list is mostly duplicates. Rank the APIs and keep one entry per device.
# WDM-KS is deliberately absent: PyAudio consistently fails to open those
# endpoints (it raises during cleanup with a confusing NoneType error), so
# listing them would only offer devices that cannot work.
_API_RANK = {
    "MME": 0,
    "Windows DirectSound": 1,
    "Windows WASAPI": 2,
}
_SKIP_DEVICES = ("primary sound capture", "microsoft sound mapper", "pc speaker")
# Devices that capture what the PC is playing rather than a microphone
_LOOPBACK_HINTS = ("stereo mix", "loopback", "what u hear", "wave out")


class _MonoStream:
    """Loopback is stereo at 48kHz; speech_recognition wants mono. Downmix on
    the way through so the rest of the pipeline is unchanged."""

    def __init__(self, stream, channels):
        self._stream = stream
        self._channels = channels

    def read(self, frames, *args, **kwargs):
        kwargs.setdefault("exception_on_overflow", False)
        data = self._stream.read(frames, *args, **kwargs)
        if self._channels == 1:
            return data
        import numpy as np

        samples = np.frombuffer(data, dtype=np.int16)
        usable = (len(samples) // self._channels) * self._channels
        mixed = samples[:usable].reshape(-1, self._channels).mean(axis=1)
        return mixed.astype(np.int16).tobytes()

    def close(self):
        return self._stream.close()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class LoopbackSource(sr.AudioSource):
    """Captures what the PC is playing, via WASAPI loopback.

    Plain PyAudio cannot open loopback endpoints at all, which is why the
    Stereo Mix entries failed. PyAudioWPatch exposes them properly and needs
    no changes to Windows sound settings.
    """

    def __init__(self, device_index=None, chunk=1024):
        import pyaudiowpatch as pyaudio

        self._pyaudio = pyaudio
        self._audio = None
        self._raw_stream = None
        self.stream = None
        self.CHUNK = chunk
        self.SAMPLE_WIDTH = pyaudio.get_sample_size(pyaudio.paInt16)

        handle = pyaudio.PyAudio()
        try:
            info = (
                handle.get_device_info_by_index(device_index)
                if device_index is not None
                else handle.get_default_wasapi_loopback()
            )
            self.device_index = info["index"]
            self.channels = int(info["maxInputChannels"]) or 2
            self.SAMPLE_RATE = int(info["defaultSampleRate"])
        finally:
            handle.terminate()

    def __enter__(self):
        self._audio = self._pyaudio.PyAudio()
        try:
            self._raw_stream = self._audio.open(
                format=self._pyaudio.paInt16,
                channels=self.channels,
                rate=self.SAMPLE_RATE,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.CHUNK,
            )
        except Exception:
            self._audio.terminate()
            self._audio = None
            raise
        self.stream = _MonoStream(self._raw_stream, self.channels)
        return self

    def __exit__(self, *_exc):
        try:
            if self._raw_stream is not None:
                self._raw_stream.stop_stream()
                self._raw_stream.close()
        finally:
            self._raw_stream = None
            self.stream = None
            if self._audio is not None:
                self._audio.terminate()
                self._audio = None


def list_loopback_devices():
    """Outputs we can eavesdrop on - for translating a podcast or a call."""
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return []

    handle = pyaudio.PyAudio()
    devices = []
    try:
        # Index None follows whatever Windows is currently playing through,
        # which is almost always what you want - picking the wrong endpoint
        # (speakers while sound goes to headphones) just captures silence.
        try:
            current = handle.get_default_wasapi_loopback()["name"]
            current = current.replace("[Loopback]", "").strip()
            current = current.split("(")[0].strip() or current
        except Exception:
            current = None

        if current:
            devices.append({
                "index": None,
                "kind": "loopback",
                "label": f"System audio ({current})",
                "loopback": True,
            })

        for info in handle.get_loopback_device_info_generator():
            name = info["name"].replace("[Loopback]", "").strip()
            short = name.split("(")[0].strip() or name
            devices.append({
                "index": info["index"],
                "kind": "loopback",
                "label": f"System audio - {short}",
                "loopback": True,
            })
    except Exception:
        return []
    finally:
        handle.terminate()
    return devices


def list_input_devices():
    """Return [{'index', 'label', 'loopback'}] of usable audio inputs."""
    import pyaudio

    pa = pyaudio.PyAudio()
    best = {}
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info["maxInputChannels"] < 1:
                continue

            name = info["name"].strip()
            low = name.lower()
            if "@system32" in low or low.startswith("input ()"):
                continue
            if any(skip in low for skip in _SKIP_DEVICES):
                continue

            api = pa.get_host_api_info_by_index(info["hostApi"])["name"]
            if api not in _API_RANK:
                continue
            base = name.split("(")[0].strip() or name
            rank = _API_RANK[api]
            # MME truncates names at 31 chars, so key on the part before "("
            if base.lower() not in best or rank < best[base.lower()][0]:
                best[base.lower()] = (rank, i, base, low)
    finally:
        pa.terminate()

    devices = []
    for _rank, index, base, low in sorted(best.values(), key=lambda t: t[1]):
        loopback = any(hint in low for hint in _LOOPBACK_HINTS)
        # "Microphone Array" is what Realtek calls the built-in laptop mic,
        # which is not obvious from the name
        friendly = base
        low_base = base.lower()
        if "microphone array" in low_base:
            friendly = "Internal microphone"
        elif low_base == "microphone":
            friendly = "Internal microphone (mono)"

        devices.append({
            "index": index,
            "kind": "mic",
            "label": f"System audio ({friendly})" if loopback else friendly,
            "loopback": loopback,
        })
    return devices


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
def apply_dwm_rounding(window):
    """Ask the Windows 11 compositor to round this window's corners.

    Returns True if it took. Anything older than Windows 11 simply ignores the
    attribute, and the panel stays square rather than breaking.
    """
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        value = ctypes.c_int(DWMWCP_ROUND)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value), ctypes.sizeof(value),
        )
        return result == 0
    except Exception:
        return False


def pick_font(target_lang):
    """First installed face that can render the target language."""
    from tkinter import font as tkfont

    installed = set(tkfont.families())
    wanted = CJK_FONTS if target_lang in CJK_LANGS else LATIN_FONTS
    for name in wanted:
        if name in installed:
            return name
    return "Segoe UI" if "Segoe UI" in installed else "Arial"


def blend(color_a, color_b, t):
    """Mix two #rrggbb colours. Tk canvas has no alpha, so we fake it."""
    t = max(0.0, min(1.0, t))
    a = [int(color_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)
    )


def rounded_path(x1, y1, x2, y2, r, count, corner_points=BORDER_CORNER_POINTS):
    """Points around a rounded rectangle, as (x, y, t).

    t is the normalised distance travelled along the path, which is what the
    animation uses. Spacing points evenly by arc length would put barely one
    point on each corner - they are only about 1% of the perimeter - so the
    curve collapsed into a visible diagonal. Corners get a fixed budget of
    points instead, and t keeps the travelling light moving at a steady speed
    even though the points are much denser there.
    """
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    span_w = max(0.0, (x2 - x1) - 2 * r)
    span_h = max(0.0, (y2 - y1) - 2 * r)
    if span_w + span_h <= 0:
        return []

    straight = 2 * span_w + 2 * span_h
    budget = max(4, count - 4 * corner_points)

    def edge(ax, ay, bx, by, n):
        return [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(n)]

    def arc(cx, cy, start_a):
        return [
            (cx + r * math.cos(start_a + (math.pi / 2) * i / corner_points),
             cy + r * math.sin(start_a + (math.pi / 2) * i / corner_points))
            for i in range(corner_points)
        ]

    n_top = max(2, int(budget * span_w / straight)) if span_w else 1
    n_side = max(2, int(budget * span_h / straight)) if span_h else 1

    pts = []
    pts += edge(x1 + r, y1, x2 - r, y1, n_top)          # top
    pts += arc(x2 - r, y1 + r, -math.pi / 2)            # top-right
    pts += edge(x2, y1 + r, x2, y2 - r, n_side)         # right
    pts += arc(x2 - r, y2 - r, 0.0)                     # bottom-right
    pts += edge(x2 - r, y2, x1 + r, y2, n_top)          # bottom
    pts += arc(x1 + r, y2 - r, math.pi / 2)             # bottom-left
    pts += edge(x1, y2 - r, x1, y1 + r, n_side)         # left
    pts += arc(x1 + r, y1 + r, math.pi)                 # top-left

    # Cumulative distance so the light travels at a constant rate
    dists, total = [0.0], 0.0
    for i in range(1, len(pts) + 1):
        ax, ay = pts[i - 1]
        bx, by = pts[i % len(pts)]
        total += math.hypot(bx - ax, by - ay)
        dists.append(total)
    if total <= 0:
        return []
    return [(x, y, dists[i] / total) for i, (x, y) in enumerate(pts)]


def draw_rounded_rect_outline(canvas, x1, y1, x2, y2, radius, colour, width=1):
    """Just the border of a rounded rectangle."""
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    canvas.create_line(x1 + r, y1, x2 - r, y1, fill=colour, width=width)
    canvas.create_line(x1 + r, y2, x2 - r, y2, fill=colour, width=width)
    canvas.create_line(x1, y1 + r, x1, y2 - r, fill=colour, width=width)
    canvas.create_line(x2, y1 + r, x2, y2 - r, fill=colour, width=width)
    d = r * 2
    canvas.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90,
                      style=tk.ARC, outline=colour, width=width)
    canvas.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90,
                      style=tk.ARC, outline=colour, width=width)
    canvas.create_arc(x1, y2 - d, x1 + d, y2, start=180, extent=90,
                      style=tk.ARC, outline=colour, width=width)
    canvas.create_arc(x2 - d, y2 - d, x2, y2, start=270, extent=90,
                      style=tk.ARC, outline=colour, width=width)


def draw_rounded_rect(canvas, x1, y1, x2, y2, radius, fill):
    """Rounded rectangle from true circular quadrants plus two overlapping bars.

    A smoothed polygon would be quicker to write, but its bezier corners visibly
    flatten; pieslice arcs give an exact quarter circle, which reads much cleaner
    against the transparent backdrop.
    """
    r = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    if r == 0:
        canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=fill)
        return

    d = r * 2
    for (cx, cy, start) in (
        (x1, y1, 90),        # top-left
        (x2 - d, y1, 0),     # top-right
        (x1, y2 - d, 180),   # bottom-left
        (x2 - d, y2 - d, 270),  # bottom-right
    ):
        canvas.create_arc(
            cx, cy, cx + d, cy + d,
            start=start, extent=90, style=tk.PIESLICE,
            fill=fill, outline=fill,
        )

    # Two bars fill the middle, leaving only the four rounded corners exposed
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill)
    canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill)


class AISettingsDialog:
    """A small form for the optional cloud-translation key.

    This is the one place a real text field is worth a window of its own -
    typing an API key onto a canvas would be miserable. Dark styling to match.
    """

    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title("AI translation")
        self.top.configure(bg=PANEL_BG)
        self.top.resizable(False, False)
        self.top.transient(app.root)
        self.top.attributes("-topmost", True)

        pad = {"padx": 18, "bg": PANEL_BG, "fg": TEXT_COLOR}
        tk.Label(self.top, text="Optional: translate with your own AI key",
                 font=(app.latin_font, 12, "bold"), **pad).pack(anchor="w", pady=(16, 2))
        tk.Label(self.top, wraplength=380, justify="left", fg=NOTICE_COLOR,
                 bg=PANEL_BG, padx=18, font=(app.latin_font, 9),
                 text=("Nothing to install. A Claude or Gemini key gives much "
                       "better translation, especially for Kiswahili. Leave it "
                       "off to keep everything local.")
                 ).pack(anchor="w", pady=(0, 12))

        self.provider = tk.StringVar(value=app.ai_provider)
        row = tk.Frame(self.top, bg=PANEL_BG)
        row.pack(anchor="w", padx=18)
        for value, text in (("off", "Off"), ("claude", "Claude"), ("gemini", "Gemini")):
            tk.Radiobutton(
                row, text=text, value=value, variable=self.provider,
                command=self._sync, bg=PANEL_BG, fg=TEXT_COLOR,
                selectcolor=CONTROL_BG, activebackground=PANEL_BG,
                activeforeground=ACCENT_HOT, font=(app.latin_font, 10),
            ).pack(side="left", padx=(0, 14))

        self.key_entry = tk.Entry(
            self.top, show="•", width=44, bg=CONTROL_BG, fg=TEXT_COLOR,
            disabledbackground=PANEL_BG, disabledforeground=NOTICE_COLOR,
            insertbackground=TEXT_COLOR, relief="flat", font=(app.latin_font, 10),
        )
        self.key_entry.pack(padx=18, pady=(12, 2), ipady=5)
        if app.ai_key:
            self.key_entry.insert(0, app.ai_key)
        self.hint = tk.Label(self.top, bg=PANEL_BG, fg=NOTICE_COLOR,
                             font=(app.latin_font, 9), padx=18, anchor="w")
        self.hint.pack(anchor="w")

        self.speech = tk.BooleanVar(value=app.ai_covers_speech)
        self.speech_cb = tk.Checkbutton(
            self.top, text="Also use Gemini for speech (skip the local model)",
            variable=self.speech, bg=PANEL_BG, fg=TEXT_COLOR,
            selectcolor=CONTROL_BG, activebackground=PANEL_BG,
            activeforeground=ACCENT_HOT, font=(app.latin_font, 9),
        )
        self.speech_cb.pack(anchor="w", padx=14, pady=(8, 0))

        self.status = tk.Label(self.top, bg=PANEL_BG, fg=ACCENT_HOT,
                               font=(app.latin_font, 9), padx=18, anchor="w")
        self.status.pack(anchor="w", pady=(6, 0))

        btns = tk.Frame(self.top, bg=PANEL_BG)
        btns.pack(anchor="e", padx=18, pady=16)
        tk.Button(btns, text="Cancel", command=self.top.destroy,
                  relief="flat", bg=CONTROL_BG, fg=TEXT_COLOR,
                  activebackground=CONTROL_HOT, font=(app.latin_font, 10),
                  padx=14, pady=4).pack(side="right", padx=(8, 0))
        tk.Button(btns, text="Save", command=self._save,
                  relief="flat", bg=WAVE_PEAK, fg=PANEL_BG,
                  activebackground=ACCENT_HOT, font=(app.latin_font, 10, "bold"),
                  padx=16, pady=4).pack(side="right")

        self._sync()
        self.top.update_idletasks()
        apply_dwm_rounding(self.top)
        self.top.grab_set()
        self.key_entry.focus_set()

    def _sync(self):
        prov = self.provider.get()
        on = prov != "off"
        self.key_entry.configure(state="normal" if on else "disabled")
        self.speech_cb.configure(state="normal" if prov == "gemini" else "disabled")
        self.hint.configure(text={
            "claude": "Get a key at console.anthropic.com  (starts with sk-ant-)",
            "gemini": "Get a key at aistudio.google.com/apikey",
            "off": "Translation stays fully local.",
        }[prov])

    def _save(self):
        prov = self.provider.get()
        key = self.key_entry.get().strip() if prov != "off" else ""
        covers = self.speech.get() and prov == "gemini"
        if prov != "off" and not key:
            self.status.configure(text="Enter a key, or choose Off.")
            return
        self.status.configure(text="Saving…")
        self.app.apply_ai_settings(prov, key, covers)
        self.top.destroy()


class SettingsPanel:
    """Settings popover drawn on a canvas in the app's own style.

    A tk.Menu would be less code, but Windows renders it with system colours
    and fonts, which looked like a different program bolted onto the panel.
    """

    ROW_H = 28
    HEAD_H = 26
    SLIDER_H = 36
    COL_W = 196
    PAD = 14
    DOT_X = 12

    def __init__(self, app, anchor_x, anchor_y, mode="full"):
        self.app = app
        self.mode = mode
        self.hover = None
        self.hits = []
        self.slider = None          # (x1, x2, y_centre) once drawn
        self.dragging = False

        self.columns = (self.build_language_columns() if mode == "language"
                        else self.build_columns())
        def row_h(kind):
            return {"header": self.HEAD_H, "slider": self.SLIDER_H}.get(
                kind, self.ROW_H)
        heights = [sum(row_h(r[0]) for r in col) for col in self.columns]
        self.w = len(self.columns) * self.COL_W + self.PAD * 2
        self.h = max(heights) + self.PAD * 2

        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.config(bg=PANEL_BG)

        # Keep the popover fully on screen, preferring above the button
        screen_w = self.top.winfo_screenwidth()
        screen_h = self.top.winfo_screenheight()
        x = max(6, min(anchor_x - self.w + 40, screen_w - self.w - 6))
        y = anchor_y - self.h - 10
        if y < 6:
            y = min(anchor_y + 40, screen_h - self.h - 6)
        self.top.geometry(f"{self.w}x{self.h}+{int(x)}+{int(y)}")

        self.canvas = tk.Canvas(
            self.top, bg=PANEL_BG, highlightthickness=0, bd=0,
            width=self.w, height=self.h,
        )
        self.canvas.pack(fill="both", expand=True)
        self.top.update_idletasks()
        apply_dwm_rounding(self.top)

        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<ButtonPress-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "dragging", False))
        self.top.bind("<Escape>", lambda _e: self.close())
        self.top.bind("<FocusOut>", lambda _e: self.close())

        self.draw()
        self.top.focus_force()

    # -- content -----------------------------------------------------------
    def build_language_columns(self):
        """The compact popover behind the direction chip.

        Language is the setting that changes most, so it gets its own small
        surface instead of being two sections inside a 33-item menu.
        """
        app = self.app
        speaking = [("header", "Speaking", None, False)]
        speaking.append((
            "item", "Auto detect",
            (lambda: app.set_input_mode("auto")), app.input_mode == "auto",
        ))
        for code in LANGUAGES:
            speaking.append((
                "item", LANG_NAMES[code],
                (lambda c=code: app.set_input_mode(c)), app.input_mode == code,
            ))

        showing = [("header", "Show me", None, False)]
        showing.append((
            "item", "The other language",
            (lambda: app.set_target_mode("auto")), app.target_mode == "auto",
        ))
        for code in LANGUAGES:
            showing.append((
                "item", LANG_NAMES[code],
                (lambda c=code: app.set_target_mode(c)), app.target_mode == code,
            ))
        return [speaking, showing]

    def build_columns(self):
        app = self.app
        audio = [("header", "Audio input", None, False)]
        try:
            devices = [{"index": None, "kind": "mic",
                        "label": "System default", "loopback": False}]
            devices += list_input_devices()
            loopbacks = list_loopback_devices()
            for dev in devices:
                audio.append((
                    "item", dev["label"],
                    (lambda d=dev: app.select_device(
                        d["index"], d["label"], d.get("kind", "mic"))),
                    app.device_label == dev["label"],
                ))
            if loopbacks:
                audio.append(("header", "Listen to playback", None, False))
                for dev in loopbacks:
                    audio.append((
                        "item", dev["label"],
                        (lambda d=dev: app.select_device(
                            d["index"], d["label"], "loopback")),
                        app.device_label == dev["label"],
                    ))
        except Exception as e:
            audio.append(("note", f"device list failed: {e}", None, False))
        audio.append(("item", "Refresh devices", self.reopen, False))

        audio.append(("header", "Speech engine", None, False))
        for choice in WHISPER_MODELS:
            audio.append((
                "item", choice,
                (lambda c=choice: app.set_whisper_model(c)),
                app.stt_kind == "whisper" and app.whisper_choice == choice,
            ))
        audio.append((
            "item", "Online (Google)",
            (lambda: app.set_stt("google")), app.stt_kind == "google",
        ))

        # Language lives on the panel now, behind the direction chip
        listen = [("header", "Sensitivity", None, False)]
        for name in SENSITIVITY_PRESETS:
            listen.append((
                "item", name,
                (lambda n=name: app.set_sensitivity(n)),
                app.sensitivity == name,
            ))
        listen.append(("header", "Size", None, False))
        for name in SIZE_PRESETS:
            listen.append(("item", name, (lambda n=name: app.apply_preset(n)), False))
        listen.append(("item", "Full width", app.apply_full_width, False))

        listen.append(("note", "Languages: click the chip on the panel",
                       None, False))

        misc = [("header", "Response", None, False)]
        for name in RESPONSE_PRESETS:
            misc.append((
                "item", name,
                (lambda n=name: app.set_response(n)), app.response == name,
            ))
        misc.append(("header", "Opacity", None, False))
        misc.append(("slider", "opacity", None, False))

        misc.append(("header", "Translation", None, False))
        if app.cloud_active():
            state = app.ai_engine.name if app.ai_engine else app.ai_provider.title()
            misc.append(("item", f"AI: {state}",
                         (lambda: app.open_ai_dialog()), True))
        else:
            misc.append(("item", "Use my AI key…",
                         (lambda: app.open_ai_dialog()), False))

        misc.append(("header", "App", None, False))
        misc.append(("item", "Quit", app.close, False))
        return [audio, listen, misc]

    # -- painting ----------------------------------------------------------
    def draw(self):
        c = self.canvas
        c.delete("all")
        c.create_rectangle(0, 0, self.w, self.h, fill=PANEL_BG, outline=PANEL_BG)
        self.hits = []

        for ci, col in enumerate(self.columns):
            x0 = self.PAD + ci * self.COL_W
            y = self.PAD
            for kind, label, action, selected in col:
                if kind == "header":
                    c.create_text(
                        x0 + self.DOT_X - 4, y + self.HEAD_H / 2,
                        text=label.upper(), anchor="w",
                        font=(self.app.font_family, 8),
                        fill=ACCENT,
                    )
                    c.create_line(
                        x0 + self.DOT_X - 4, y + self.HEAD_H - 4,
                        x0 + self.COL_W - 16, y + self.HEAD_H - 4,
                        fill=CONTROL_BG,
                    )
                    y += self.HEAD_H
                    continue

                if kind == "note":
                    c.create_text(
                        x0 + self.DOT_X - 4, y + self.ROW_H / 2,
                        text=label, anchor="w", width=self.COL_W - 24,
                        font=(self.app.font_family, 8), fill=NOTICE_COLOR,
                    )
                    y += self.ROW_H
                    continue

                if kind == "slider":
                    self.draw_slider(c, x0, y)
                    y += self.SLIDER_H
                    continue

                index = len(self.hits)
                hot = self.hover == index
                if hot:
                    draw_rounded_rect(
                        c, x0, y + 1, x0 + self.COL_W - 12, y + self.ROW_H - 1,
                        7, CONTROL_BG,
                    )
                if selected:
                    cy = y + self.ROW_H / 2
                    c.create_oval(
                        x0 + self.DOT_X - 7, cy - 3, x0 + self.DOT_X - 1, cy + 3,
                        fill=WAVE_PEAK, outline="",
                    )
                c.create_text(
                    x0 + self.DOT_X + 8, y + self.ROW_H / 2,
                    text=self.clip(label), anchor="w",
                    font=(self.app.font_family, 10),
                    fill=TEXT_COLOR if (hot or selected) else "#B9C9DA",
                )
                self.hits.append((x0, y, x0 + self.COL_W - 12, y + self.ROW_H, action))
                y += self.ROW_H

    def draw_slider(self, c, x0, y):
        """A horizontal track with a draggable knob, for opacity."""
        left = x0 + self.DOT_X + 4
        right = x0 + self.COL_W - 18
        cy = y + self.SLIDER_H / 2 + 3
        frac = (self.app.opacity - 0.35) / 0.65

        c.create_line(left, cy, right, cy, fill=CONTROL_BG, width=4,
                      capstyle=tk.ROUND)
        knob_x = left + frac * (right - left)
        c.create_line(left, cy, knob_x, cy, fill=ACCENT, width=4,
                      capstyle=tk.ROUND)
        c.create_oval(knob_x - 6, cy - 6, knob_x + 6, cy + 6,
                      fill=WAVE_PEAK, outline="")
        c.create_text(left, y + 9, anchor="w", text="Opacity",
                      font=(self.app.font_family, 8), fill=ACCENT)
        c.create_text(right, y + 9, anchor="e",
                      text=f"{int(self.app.opacity * 100)}%",
                      font=(self.app.font_family, 8), fill=NOTICE_COLOR)
        self.slider = (left, right, cy)

    def slider_hit(self, x, y):
        if not self.slider:
            return False
        left, right, cy = self.slider
        return left - 10 <= x <= right + 10 and abs(y - cy) <= 14

    def set_from_slider(self, x):
        left, right, _cy = self.slider
        frac = max(0.0, min(1.0, (x - left) / max(1, right - left)))
        self.app.set_opacity(0.35 + frac * 0.65)
        self.draw()

    @staticmethod
    def clip(label, limit=21):
        """Keep a label inside its column.

        Long device names ran past the column and printed over the next one's
        heading, which hid the "Show me" header entirely.
        """
        return label if len(label) <= limit else label[:limit - 1] + "…"

    # -- interaction -------------------------------------------------------
    def hit(self, x, y):
        for i, (x1, y1, x2, y2, _action) in enumerate(self.hits):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return i
        return None

    def on_motion(self, event):
        index = self.hit(event.x, event.y)
        over_slider = self.slider_hit(event.x, event.y)
        if index != self.hover:
            self.hover = index
            self.draw()
        cursor = "hand2" if (index is not None or over_slider) else ""
        self.canvas.config(cursor=cursor)

    def on_click(self, event):
        # With a grab in place, clicks outside arrive with out-of-range coords
        if not (0 <= event.x <= self.w and 0 <= event.y <= self.h):
            self.close()
            return
        if self.slider_hit(event.x, event.y):
            self.dragging = True
            self.set_from_slider(event.x)
            return                       # the menu stays open while adjusting
        index = self.hit(event.x, event.y)
        if index is None:
            return
        action = self.hits[index][4]
        self.close()
        if action:
            action()

    def on_drag(self, event):
        if self.dragging and self.slider:
            self.set_from_slider(event.x)

    def reopen(self):
        """Rebuild in place so a newly plugged-in device shows up. The anchor
        is reversed out of the current position so it lands where it was."""
        app = self.app
        anchor_x = self.top.winfo_x() + self.w - 40
        anchor_y = self.top.winfo_y() + self.h + 10
        self.close()
        app.panel = SettingsPanel(app, anchor_x, anchor_y)

    def close(self):
        if self.app.panel is self:
            self.app.panel = None
        try:
            self.top.grab_release()
            self.top.destroy()
        except tk.TclError:
            pass


CARD_BG = "#12212F"      # flat panel, one step off the window
CARD_EDGE = "#2A3E52"     # thin solid border
SECTION_A = "#5FB8D8"     # audio
SECTION_B = "#7E8FC4"     # translation / appearance
SECTION_C = "#A386C4"     # performance
PILL_ON = "#2E7FA8"       # solid selected fill
PILL_TXT_ON = "#EAF4FA"


class SettingsWindow:
    """The full settings surface: three cards of controls over a glass backdrop.

    Everything is canvas-drawn so it matches the widget's own look rather than
    borrowing Windows' grey dialog chrome. A small toolkit of draw helpers
    (pills, dropdowns, radios, slider) keeps each card declarative.
    """

    W, H = 716, 560
    PAD = 18
    GAP = 12
    HEADER_H = 62

    def __init__(self, app):
        self.app = app
        self.hits = []              # (x1, y1, x2, y2, callback)
        self.hover = None
        self.slider = None
        self.dragging = False
        self.dd_open = None         # (rect, options, callback) while a list is open
        self._scale = 1.0

        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.config(bg=PANEL_BG)

        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        self._scale = min(1.0, (sw - 40) / self.W, (sh - 40) / self.H)
        w = int(self.W * self._scale)
        h = int(self.H * self._scale)
        self.top.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        self.canvas = tk.Canvas(self.top, bg=PANEL_BG, highlightthickness=0,
                                bd=0, width=w, height=h)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.scale_factor = self._scale
        self.top.update_idletasks()
        apply_dwm_rounding(self.top)

        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<ButtonPress-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>",
                         lambda _e: setattr(self, "dragging", False))
        self.top.bind("<Escape>", lambda _e: self.close())
        self.canvas.bind("<ButtonPress-3>", lambda _e: self.close())

        self.draw()
        self.top.focus_force()
        self.top.after(120, self._grab)

    def _grab(self):
        try:
            self.top.grab_set()
        except tk.TclError:
            pass

    # -- geometry helpers --------------------------------------------------
    def s(self, v):
        return v * self._scale

    def font(self, size, weight="normal", cjk=False):
        family = self.app.cjk_font if cjk else self.app.latin_font
        return (family, max(7, int(size * self._scale)), weight)

    # -- toolkit ---------------------------------------------------------
    def card(self, x, y, w, h, title, colour):
        c = self.canvas
        draw_rounded_rect(c, x, y, x + w, y + h, self.s(9), CARD_BG)
        draw_rounded_rect_outline(c, x, y, x + w, y + h, self.s(9), CARD_EDGE)
        ix, iy = x + self.s(16), y + self.s(18)
        c.create_rectangle(ix - self.s(3), iy - self.s(3), ix + self.s(3),
                           iy + self.s(3), fill=colour, outline="")
        c.create_text(ix + self.s(14), iy, anchor="w", text=title.upper(),
                      font=self.font(9, "bold"), fill=colour)
        c.create_line(ix - self.s(3), iy + self.s(14), x + w - self.s(14),
                      iy + self.s(14), fill=CARD_EDGE)
        return y + self.s(46)

    def field_label(self, x, y, text):
        self.canvas.create_text(x, y, anchor="w", text=text,
                                font=self.font(9.5), fill="#9FB4CB")
        return y + self.s(20)

    def _trunc(self, text, px_w):
        approx = max(4, int(px_w / max(1.0, self.s(9) * 0.56)))
        return text if len(text) <= approx else text[:approx - 1] + "…"

    _PILL_SHORT = {"Far (presentation)": "Far", "Very far": "Far+",
                   "Accurate (base)": "Accurate", "Fast (tiny)": "Fast"}

    def pills(self, x, y, w, options, current, callback):
        c = self.canvas
        n = len(options)
        gap = self.s(5)
        pw = (w - gap * (n - 1)) / n
        ph = self.s(28)
        for i, opt in enumerate(options):
            px = x + i * (pw + gap)
            on = opt == current
            hot = self.hover == ("pill", id(callback), i)
            fill = PILL_ON if on else (CONTROL_HOT if hot else CONTROL_BG)
            draw_rounded_rect(c, px, y, px + pw, y + ph, self.s(6), fill)
            c.create_text(px + pw / 2, y + ph / 2,
                          text=self._PILL_SHORT.get(opt, opt),
                          font=self.font(7.5, "bold" if on else "normal"),
                          fill=PILL_TXT_ON if on else TEXT_COLOR)
            self.hits.append((px, y, px + pw, y + ph,
                              (lambda o=opt: callback(o)),
                              ("pill", id(callback), i)))
        return y + ph + self.s(9)

    def dropdown(self, x, y, w, value, options, callback):
        c = self.canvas
        dh = self.s(31)
        hot = self.hover == ("dd", id(callback))
        draw_rounded_rect(c, x, y, x + w, y + dh, self.s(8),
                          CONTROL_HOT if hot else CONTROL_BG)
        c.create_text(x + self.s(11), y + dh / 2, anchor="w",
                      text=self._trunc(value, w - self.s(54)),
                      font=self.font(9), fill=TEXT_COLOR)
        ax = x + w - self.s(16)
        ay = y + dh / 2
        c.create_line(ax - self.s(4), ay - self.s(2), ax, ay + self.s(3),
                      fill=ACCENT_HOT, width=1.6)
        c.create_line(ax, ay + self.s(3), ax + self.s(4), ay - self.s(2),
                      fill=ACCENT_HOT, width=1.6)
        self.hits.append((x, y, x + w, y + dh,
                          (lambda: self._toggle_dropdown((x, y, w, dh), options,
                                                         callback)),
                          ("dd", id(callback))))
        return y + dh + self.s(12)

    def radio(self, x, y, text, selected, callback, cjk=False):
        c = self.canvas
        rh = self.s(28)
        cy = y + rh / 2
        r = self.s(7)
        hot = self.hover == ("radio", id(callback), text)
        c.create_oval(x, cy - r, x + 2 * r, cy + r, width=1.6,
                      outline=PILL_ON if selected else ACCENT)
        if selected:
            c.create_oval(x + r - self.s(3.5), cy - self.s(3.5),
                          x + r + self.s(3.5), cy + self.s(3.5),
                          fill=PILL_ON, outline="")
        c.create_text(x + 2 * r + self.s(10), cy, anchor="w", text=text,
                      font=self.font(9.5, cjk=cjk),
                      fill=TEXT_COLOR if (selected or hot) else "#B9C9DA")
        self.hits.append((x, y, x + self.s(220), y + rh, callback,
                          ("radio", id(callback), text)))
        return y + rh

    def opacity_slider(self, x, y, w):
        c = self.canvas
        cy = y + self.s(14)
        frac = (self.app.opacity - 0.35) / 0.65
        c.create_line(x, cy, x + w, cy, fill=CONTROL_BG, width=self.s(4),
                      capstyle=tk.ROUND)
        kx = x + frac * w
        c.create_line(x, cy, kx, cy, fill=PILL_ON, width=self.s(4),
                      capstyle=tk.ROUND)
        c.create_oval(kx - self.s(7), cy - self.s(7), kx + self.s(7),
                      cy + self.s(7), fill=WAVE_PEAK, outline="")
        c.create_text(x + w + self.s(14), cy, anchor="w",
                      text=f"{int(self.app.opacity * 100)}%",
                      font=self.font(9), fill=NOTICE_COLOR)
        self.slider = (x, x + w, cy)
        return y + self.s(34)

    def link_row(self, x, y, w, text, callback):
        c = self.canvas
        rh = self.s(30)
        hot = self.hover == ("link", id(callback))
        if hot:
            draw_rounded_rect(c, x - self.s(6), y, x + w, y + rh, self.s(8),
                              CONTROL_BG)
        c.create_text(x, y + rh / 2, anchor="w", text=text,
                      font=self.font(9.5), fill=ACCENT_HOT)
        c.create_text(x + w - self.s(10), y + rh / 2, anchor="e", text="›",
                      font=self.font(12), fill=ACCENT_HOT)
        self.hits.append((x - self.s(6), y, x + w, y + rh, callback,
                          ("link", id(callback))))
        return y + rh

    def divider(self, x, y, w):
        self.canvas.create_line(x, y, x + w, y, fill=CARD_EDGE)
        return y + self.s(14)

    # -- content -------------------------------------------------------
    def draw(self):
        c = self.canvas
        c.delete("all")
        self.hits = []
        self.slider = None
        c.create_rectangle(0, 0, self.s(self.W), self.s(self.H),
                           fill=PANEL_BG, outline=PANEL_BG)

        self._header()

        col_w = (self.s(self.W) - self.s(self.PAD) * 2
                 - self.s(self.GAP) * 2) / 3
        top = self.s(self.HEADER_H) + self.s(10)
        x0 = self.s(self.PAD)
        x1 = x0 + col_w + self.s(self.GAP)
        x2 = x1 + col_w + self.s(self.GAP)
        body_h = self.s(self.H) - top - self.s(self.PAD)

        t_h = self.s(238)
        self._audio_card(x0, top, col_w, body_h)
        self._translation_card(x1, top, col_w, t_h)
        self._appearance_card(x1, top + t_h + self.s(14),
                              col_w, body_h - t_h - self.s(14))
        self._performance_card(x2, top, col_w, body_h)

        if self.dd_open:
            self._draw_dropdown_list()

    def _header(self):
        c = self.canvas
        ix, iy = self.s(self.PAD + 7), self.s(27)
        # small drawn microphone, one solid colour
        draw_rounded_rect(c, ix - self.s(3.5), iy - self.s(8),
                          ix + self.s(3.5), iy + self.s(1), self.s(3.5), SECTION_A)
        c.create_arc(ix - self.s(7), iy - self.s(4), ix + self.s(7),
                     iy + self.s(6), start=200, extent=140, style=tk.ARC,
                     outline=SECTION_A, width=1.6)
        c.create_line(ix, iy + self.s(5), ix, iy + self.s(9),
                      fill=SECTION_A, width=1.6)
        c.create_text(ix + self.s(19), self.s(21), anchor="w",
                      text="Translator Settings",
                      font=self.font(12.5, "bold"), fill=TEXT_COLOR)
        c.create_text(ix + self.s(19), self.s(38), anchor="w",
                      text="Listening, translation and widget behaviour",
                      font=self.font(8), fill=NOTICE_COLOR)
        cx = self.s(self.W) - self.s(20)
        cy = self.s(21)
        hot = self.hover == ("close", 0)
        col = TEXT_COLOR if hot else ACCENT_HOT
        for pa, pb in (((-5, -5), (5, 5)), ((5, -5), (-5, 5))):
            c.create_line(cx + self.s(pa[0]), cy + self.s(pa[1]),
                          cx + self.s(pb[0]), cy + self.s(pb[1]),
                          fill=col, width=1.6)
        self.hits.append((cx - self.s(12), cy - self.s(12), cx + self.s(12),
                          cy + self.s(12), self.close, ("close", 0)))

    def _audio_card(self, x, y, w, h):
        app = self.app
        cy = self.card(x, y, w, h, "Audio", SECTION_A)
        ix = x + self.s(22)
        iw = w - self.s(44)

        cy = self.field_label(ix, cy, "Audio input")
        mics = [{"index": None, "kind": "mic", "label": "System default"}]
        mics += [d for d in _safe(list_input_devices)]
        cur = next((d["label"] for d in mics if d["label"] == app.device_label),
                   app.device_label)
        cy = self.dropdown(
            ix, cy, iw, cur,
            [(d["label"], (lambda dd=d: app.select_device(
                dd["index"], dd["label"], dd.get("kind", "mic"))))
             for d in mics],
            None)
        for d in mics[1:]:
            on = app.device_label == d["label"] and app.device_kind == "mic"
            cy = self.radio(ix, cy, d["label"], on,
                            (lambda dd=d: app.select_device(
                                dd["index"], dd["label"], "mic")))
        cy += self.s(4)

        cy = self.field_label(ix, cy + self.s(4), "Sensitivity")
        cy = self.pills(ix, cy, iw, list(SENSITIVITY_PRESETS), app.sensitivity,
                        app.set_sensitivity)

        cy = self.divider(ix, cy + self.s(4), iw)
        loop = _safe(list_loopback_devices)
        cy = self.field_label(ix, cy, "Listen to playback")
        if loop:
            lcur = next((d["label"] for d in loop
                         if d["label"] == app.device_label), loop[0]["label"])
            cy = self.dropdown(
                ix, cy, iw, lcur,
                [(d["label"], (lambda dd=d: app.select_device(
                    dd["index"], dd["label"], "loopback"))) for d in loop],
                None)
            self.canvas.create_text(
                ix, cy - self.s(4), anchor="nw", width=iw,
                text="Capture what this PC plays - for a video or call.",
                font=self.font(8.5), fill=NOTICE_COLOR)
            cy += self.s(28)
        else:
            self.canvas.create_text(
                ix, cy, anchor="nw", width=iw,
                text="No playback device. Enable Stereo Mix in Sound settings.",
                font=self.font(8.5), fill=NOTICE_COLOR)
            cy += self.s(30)

        self.link_row(ix, cy, iw, "Refresh devices", self.reopen)

    def _translation_card(self, x, y, w, h):
        app = self.app
        cy = self.card(x, y, w, h, "Translation", SECTION_B)
        ix = x + self.s(22)
        iw = w - self.s(44)

        src = ("Auto detect" if app.input_mode == "auto"
               else LANG_NAMES[app.input_mode])
        cy = self.field_label(ix, cy, "Source language")
        cy = self.dropdown(
            ix, cy, iw, src,
            [("Auto detect", lambda: app.set_input_mode("auto"))]
            + [(LANG_NAMES[c], (lambda cc=c: app.set_input_mode(cc)))
               for c in LANGUAGES],
            None)

        tgt = ("The other language" if app.target_mode == "auto"
               else LANG_NAMES[app.target_mode])
        cy = self.field_label(ix, cy + self.s(2), "Target language")
        cy = self.dropdown(
            ix, cy, iw, tgt,
            [("The other language", lambda: app.set_target_mode("auto"))]
            + [(LANG_NAMES[c], (lambda cc=c: app.set_target_mode(cc)))
               for c in LANGUAGES],
            None)

        eng = (f"AI: {app.ai_provider.title()}" if app.cloud_active()
               else "Local (offline)")
        cy = self.field_label(ix, cy + self.s(2), "Translation engine")
        self.dropdown(
            ix, cy, iw, eng,
            [("Local (offline)", lambda: app.apply_ai_settings("off", "", False)),
             ("AI: use my key…", app.open_ai_dialog)],
            None)

    def _appearance_card(self, x, y, w, h):
        app = self.app
        cy = self.card(x, y, w, h, "Appearance", SECTION_B)
        ix = x + self.s(22)
        iw = w - self.s(44)

        cy = self.field_label(ix, cy - self.s(2), "Widget size")
        names = list(SIZE_PRESETS) + ["Full"]
        cur = "Full" if getattr(app, "_full_width", False) else app._size_name
        cy = self.pills(ix, cy, iw, names, cur,
                        lambda n: (app.apply_full_width() if n == "Full"
                                   else app.apply_preset(n)))

        cy = self.field_label(ix, cy - self.s(2), "Opacity")
        cy = self.opacity_slider(ix, cy, iw - self.s(46))

        cy = self.field_label(ix, cy, "Border glow")
        self.pills(ix, cy, iw, ["On", "Off"],
                   "On" if app.border_glow else "Off",
                   lambda v: app.set_border_glow(v == "On"))

    def _performance_card(self, x, y, w, h):
        app = self.app
        cy = self.card(x, y, w, h, "Performance", SECTION_C)
        ix = x + self.s(22)
        iw = w - self.s(44)

        cy = self.field_label(ix, cy, "Response")
        cy = self.pills(ix, cy, iw, list(RESPONSE_PRESETS), app.response,
                        app.set_response)

        cy = self.field_label(ix, cy + self.s(10), "Speech recognition")
        for choice in WHISPER_MODELS:
            on = app.stt_kind == "whisper" and app.whisper_choice == choice
            cy = self.radio(ix, cy, choice, on,
                            (lambda c=choice: app.set_whisper_model(c)))
        cy = self.radio(ix, cy, "Online (Google)", app.stt_kind == "google",
                        lambda: app.set_stt("google"))

        # No Quit button here - it sat right where you reach to close the
        # window and took the whole app down. Close from the panel's X, or
        # right-click the widget.
        self.canvas.create_text(
            ix, y + h - self.s(16), anchor="w",
            text="Close: Esc  ·  Quit the app: right-click the widget",
            font=self.font(7.5), fill=NOTICE_COLOR)

    # -- dropdown overlay --------------------------------------------------
    def _toggle_dropdown(self, rect, options, _cb):
        if self.dd_open and self.dd_open[0] == rect:
            self.dd_open = None
        else:
            self.dd_open = (rect, options, _cb)
        self.draw()

    def _draw_dropdown_list(self):
        c = self.canvas
        (x, y, w, dh), options, _cb = self.dd_open
        rh = self.s(30)
        lh = rh * len(options) + self.s(8)
        ly = y + dh + self.s(4)
        if ly + lh > self.s(self.H):
            ly = y - lh - self.s(4)
        draw_rounded_rect(c, x, ly, x + w, ly + lh, self.s(10), CONTROL_BG)
        c.create_rectangle(x, ly, x + w, ly + lh, outline=CARD_EDGE)
        for i, (label, action) in enumerate(options):
            ry = ly + self.s(4) + i * rh
            hot = self.hover == ("ddopt", i)
            if hot:
                draw_rounded_rect(c, x + self.s(4), ry, x + w - self.s(4),
                                  ry + rh, self.s(7), CONTROL_HOT)
            c.create_text(x + self.s(14), ry + rh / 2, anchor="w", text=label,
                          font=self.font(9.5),
                          fill=TEXT_COLOR if hot else "#B9C9DA",
                          width=w - self.s(24))
            self.hits.append((x, ry, x + w, ry + rh,
                              (lambda a=action: self._pick(a)), ("ddopt", i)))

    def _pick(self, action):
        self.dd_open = None
        if action:
            action()
        self.app.root.after(60, self._refresh)

    def _refresh(self):
        if self.app.settings_win is self:
            self.draw()

    # -- interaction -----------------------------------------------------
    def _hit(self, x, y):
        for entry in reversed(self.hits):
            x1, y1, x2, y2 = entry[:4]
            if x1 <= x <= x2 and y1 <= y <= y2:
                return entry
        return None

    def on_motion(self, event):
        entry = self._hit(event.x, event.y)
        tag = entry[5] if entry and len(entry) > 5 else None
        if tag != self.hover:
            self.hover = tag
            self.draw()
        over = entry is not None or self._slider_hit(event.x, event.y)
        self.canvas.config(cursor="hand2" if over else "")

    def on_click(self, event):
        if not (0 <= event.x <= self.s(self.W) and 0 <= event.y <= self.s(self.H)):
            self.close()
            return
        if self._slider_hit(event.x, event.y):
            self.dragging = True
            self._set_slider(event.x)
            return
        entry = self._hit(event.x, event.y)
        if entry is None:
            if self.dd_open:
                self.dd_open = None
                self.draw()
            return
        cb = entry[4]
        if cb:
            cb()

    def on_drag(self, event):
        if self.dragging and self.slider:
            self._set_slider(event.x)

    def _slider_hit(self, x, y):
        if not self.slider:
            return False
        left, right, cy = self.slider
        return left - self.s(10) <= x <= right + self.s(10) \
            and abs(y - cy) <= self.s(14)

    def _set_slider(self, x):
        left, right, _cy = self.slider
        frac = max(0.0, min(1.0, (x - left) / max(1, right - left)))
        self.app.set_opacity(0.35 + frac * 0.65)
        self.draw()

    def reopen(self):
        self.close()
        self.app.open_settings()

    def close(self):
        if getattr(self.app, "settings_win", None) is self:
            self.app.settings_win = None
        try:
            self.top.grab_release()
            self.top.destroy()
        except tk.TclError:
            pass


def _safe(fn):
    try:
        return list(fn())
    except Exception:
        return []


class FloatingTranslator:
    def __init__(self, engine_name=ENGINE, input_mode=DEFAULT_INPUT):
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
        self._drag_mode = None
        self._hover = None
        self._text = ""
        # Status and warnings live on their own small line so a transient
        # notice never wipes the translation the audience is still reading.
        self._notice = "Starting translator"
        self._has_translation = False
        # Starts busy: the engine and speech model take several seconds to load
        self._busy = True
        self._loader = None        # (x1, x2, y) of the track, set while drawing
        self._loader_item = None
        self._controls = {}
        self._chip_box = None
        self.opacity = 1.0
        self._size_name = DEFAULT_PRESET
        self._full_width = False
        self.border_glow = True
        self.settings_win = None
        self.paused = False
        self.sensitivity = DEFAULT_SENSITIVITY
        self.response = DEFAULT_RESPONSE
        self.jobs = queue.Queue(maxsize=MAX_PENDING)
        self.stt_kind = STT_ENGINE
        self.whisper_choice = DEFAULT_WHISPER
        self._stt_cache = {}

        # Optional cloud translation. Loaded from settings.json - a user with a
        # Claude or Gemini key adds it once and it takes over from the local
        # models. Everything still works with this off.
        cfg = appconfig.load()
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
        self._wave_items = []
        self._border_items = []
        self._wave_job = None
        self._phase = 0.0
        self._listening = False
        self._font_size = SIZE_PRESETS[DEFAULT_PRESET][2]

        self.root = tk.Tk()
        # Needs the root window to exist before it can query installed fonts
        # Both scripts appear, so keep a face for each and choose per message
        self.cjk_font = pick_font("zt")
        self.latin_font = pick_font("en")
        self.font_family = self.cjk_font
        self.root.title("Live Translator")
        self.root.attributes("-topmost", True)   # stay above PowerPoint
        self.root.overrideredirect(True)         # no OS title bar

        self.root.config(bg=PANEL_BG)
        self.canvas = tk.Canvas(
            self.root, bg=PANEL_BG, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)

        # Let the Windows compositor round the corners. Drawing them ourselves
        # meant no antialiasing (a colour key cannot blend), which is what made
        # the panel look pixelated. DWM also gives us a drop shadow for free.
        self.root.update_idletasks()
        self.rounded = apply_dwm_rounding(self.root)

        self.apply_preset(DEFAULT_PRESET, keep_position=False)

        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag_mode", None))
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<ButtonPress-3>", self.show_menu)
        self.root.bind("<Escape>", self._on_escape)

        self.panel = None          # settings popover, created on demand
        self.animate()

        # Model loading can download a file, so keep it off the UI thread
        threading.Thread(
            target=self.start_listening, args=(engine_name,), daemon=True
        ).start()

    # -- window geometry ---------------------------------------------------
    def select_device(self, index, label, kind="mic"):
        """Safe to call from any thread - the capture thread uses it to fall back."""
        self.device_index = index
        self.device_kind = kind
        self.device_label = label
        self.device_gen += 1          # tells the capture thread to reopen
        self.show_status(f"Switching to {label}...")

    def apply_preset(self, name, keep_position=True):
        width, height, font_size = SIZE_PRESETS[name]
        self._font_size = font_size
        self._size_name = name
        self._full_width = False
        self.set_geometry(width, height, keep_position)

    def apply_full_width(self):
        width = self.root.winfo_screenwidth() - 80
        self._font_size = SIZE_PRESETS["Large"][2]
        self._full_width = True
        self.set_geometry(width, SIZE_PRESETS["Large"][1], keep_position=False)

    def set_border_glow(self, on):
        self.border_glow = bool(on)
        if not on:
            for item, _t in self._border_items:
                try:
                    self.canvas.itemconfig(item, fill=PANEL_BG)
                except tk.TclError:
                    pass
        log(f"border glow {'on' if on else 'off'}")

    def open_settings(self):
        if self.panel is not None:
            self.panel.close()
        if self.settings_win is not None:
            self.settings_win.close()
            return
        try:
            self.settings_win = SettingsWindow(self)
        except tk.TclError:
            self.settings_win = None

    def set_geometry(self, width, height, keep_position=True):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = max(MIN_WIDTH, min(width, screen_w))
        height = max(MIN_HEIGHT, min(height, screen_h))

        if keep_position:
            x, y = self.root.winfo_x(), self.root.winfo_y()
        else:
            # Centred horizontally, sitting near the bottom of whatever screen
            # this actually is - never hardcoded off the bottom edge.
            x = (screen_w - width) // 2
            y = int(screen_h * 0.82)

        x = max(0, min(x, screen_w - width))
        y = max(0, min(y, screen_h - height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.redraw()

    # -- painting ----------------------------------------------------------
    def redraw(self):
        c = self.canvas
        c.delete("all")
        w = max(self.root.winfo_width(), MIN_WIDTH)
        h = max(self.root.winfo_height(), MIN_HEIGHT)

        # Flat fill: the compositor clips our square panel to rounded corners,
        # so drawing our own arcs here would only fight it.
        c.create_rectangle(0, 0, w, h, fill=PANEL_BG, outline=PANEL_BG)
        self.draw_border(w, h)

        controls_left = self.layout_controls(w, h)

        # Centre the caption in the space left between meter and controls
        text_left, text_right = WAVE_ZONE, controls_left
        body_height = h - (NOTICE_STRIP if self._notice else 0)
        caption_font = (self.font_for(self._text), self._font_size, CAPTION_WEIGHT)
        cx = (text_left + text_right) // 2
        cy = body_height // 2
        wrap = max(80, text_right - text_left - EDGE_PAD)

        if self._busy:
            # A moving line says "working" without a word for it
            span = (text_right - text_left) * LOADER_WIDTH_FRAC
            x1, x2 = cx - span / 2, cx + span / 2
            c.create_line(x1, cy, x2, cy, fill=LOADER_TRACK,
                          width=LOADER_THICKNESS, capstyle=tk.ROUND)
            seg = span * LOADER_SEG_FRAC
            self._loader = (x1, x2, cy, seg)
            self._loader_item = c.create_line(
                x1, cy, x1 + seg, cy, fill=LOADER_FILL,
                width=LOADER_THICKNESS, capstyle=tk.ROUND,
            )
            self.draw_notice(h, text_left, text_right)
            self.draw_waves(w, h)
            self.draw_controls()
            self.draw_grip(w, h)
            return
        # A soft shadow one pixel down gives the glyphs an edge, which matters
        # when the panel is floating over a bright slide
        for dx, dy in ((1, 2), (-1, 2)):
            c.create_text(
                cx + dx, cy + dy, text=self._text, font=caption_font,
                fill=CAPTION_SHADOW, width=wrap, justify="center", tags="shadow",
            )
        c.create_text(
            cx, cy,
            text=self._text,
            font=caption_font,
            fill=TEXT_COLOR,
            width=wrap,
            justify="center",
            tags="body",
        )

        self.draw_notice(h, text_left, text_right)

        self.draw_waves(w, h)
        self.draw_controls()

        self.draw_grip(w, h)

    def draw_border(self, w, h):
        """Segmented frame; animate() colours each piece from the input level."""
        pts = rounded_path(BORDER_INSET, BORDER_INSET,
                           w - BORDER_INSET - 1, h - BORDER_INSET - 1,
                           BORDER_RADIUS, BORDER_SEGMENTS)
        self._border_items = []
        if len(pts) < 2:
            return
        for i, (x, y, t) in enumerate(pts):
            nx, ny, _ = pts[(i + 1) % len(pts)]
            item = self.canvas.create_line(
                x, y, nx, ny, fill=PANEL_BG, width=BORDER_WIDTH,
                capstyle=tk.ROUND, tags="border",
            )
            self._border_items.append((item, t))

    def draw_notice(self, h, text_left, text_right):
        """Small status line along the bottom edge."""
        if not self._notice:
            return
        self.canvas.create_text(
            (text_left + text_right) // 2,
            h - NOTICE_STRIP / 2 - 2,
            text=self._notice,
            font=(self.font_for(self._notice), max(9, int(self._font_size * 0.42))),
            fill=NOTICE_COLOR,
            width=max(80, text_right - text_left - EDGE_PAD),
            justify="center",
            tags="notice",
        )

    def draw_grip(self, w, h):
        """Bottom-right resize handle: two short diagonal strokes."""
        grip_fill = ACCENT_HOT if self._hover == "grip" else ACCENT
        for offset in (4, 9):
            self.canvas.create_line(
                w - offset - 3, h - 4, w - 4, h - offset - 3,
                fill=grip_fill, width=1.6, tags="grip",
            )

    def layout_controls(self, w, h):
        """Right-aligned button strip. Returns the x where the buttons start."""
        names = ["source", "listen", "menu", "close"]
        total = len(names) * CONTROL_SIZE + (len(names) - 1) * CONTROL_GAP
        x = w - EDGE_PAD // 2 - total
        y = (h - CONTROL_SIZE) / 2

        self._controls = {}
        for name in names:
            self._controls[name] = (x, y, x + CONTROL_SIZE, y + CONTROL_SIZE)
            x += CONTROL_SIZE + CONTROL_GAP
        return w - EDGE_PAD // 2 - total - 6

    def draw_controls(self):
        """Buttons drawn as vector shapes - glyph fonts render inconsistently
        at these sizes and looked fuzzy against the panel."""
        c = self.canvas
        for name, (x1, y1, x2, y2) in self._controls.items():
            hot = self._hover == name
            if hot:
                draw_rounded_rect(c, x1, y1, x2, y2, 7, CONTROL_BG)
            colour = ACCENT_HOT if hot else ACCENT
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

            if name == "source":
                if self.device_kind == "loopback":
                    # a screen, meaning "what this computer is playing"
                    c.create_rectangle(cx - 7, cy - 5, cx + 7, cy + 4,
                                       outline=colour, width=1.6)
                    c.create_line(cx - 4, cy + 7, cx + 4, cy + 7,
                                  fill=colour, width=1.6)
                    c.create_line(cx, cy + 4, cx, cy + 7, fill=colour, width=1.6)
                else:
                    # a microphone, meaning "the room"
                    c.create_oval(cx - 3, cy - 8, cx + 3, cy + 1,
                                  outline=colour, width=1.6)
                    c.create_arc(cx - 6, cy - 4, cx + 6, cy + 6,
                                 start=200, extent=140, style=tk.ARC,
                                 outline=colour, width=1.6)
                    c.create_line(cx, cy + 5, cx, cy + 8, fill=colour, width=1.6)
            elif name == "listen":
                if self.paused:
                    # Record: a filled dot, red so it reads as "arm capture"
                    dot = RECORD_HOT if hot else RECORD_COLOR
                    c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6,
                                  fill=dot, outline=dot)
                else:
                    # Stop: the square that pairs with a record dot
                    c.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5,
                                       fill=colour, outline=colour)
            elif name == "menu":
                for dy in (-5, 0, 5):
                    c.create_line(
                        cx - 6, cy + dy, cx + 6, cy + dy,
                        fill=colour, width=1.8,
                    )
            elif name == "close":
                c.create_line(cx - 5, cy - 5, cx + 5, cy + 5, fill=colour, width=1.8)
                c.create_line(cx + 5, cy - 5, cx - 5, cy + 5, fill=colour, width=1.8)

    def draw_waves(self, w, h):
        """Concentric arcs radiating from a dot, like a broadcast symbol."""
        c = self.canvas
        self._wave_items = []
        ox, oy = 26, h / 2                      # origin of the waves
        c.create_oval(ox - 4, oy - 4, ox + 4, oy + 4,
                      fill=WAVE_IDLE, outline="", tags="wavedot")

        # Translation direction, as a small chip under the meter. This is the
        # only place the direction is shown, so it replaced the old
        # "Input: English" status text, which said the same thing twice.
        label = self.direction_label()
        item = c.create_text(
            ox - 8, h - 12, text=label, anchor="w",
            font=(self.cjk_font, 8), fill=ACCENT_HOT, tags="direction",
        )
        box = c.bbox(item)
        if box:
            hot = self._hover == "chip"
            draw_rounded_rect(c, box[0] - 6, box[1] - 3, box[2] + 6, box[3] + 3,
                              8, CONTROL_HOT if hot else CONTROL_BG)
            c.tag_raise(item)
            c.itemconfig(item, fill=TEXT_COLOR if hot else ACCENT_HOT)
            # clickable: the languages are the setting people change most
            self._chip_box = (box[0] - 6, box[1] - 3, box[2] + 6, box[3] + 3)
        else:
            self._chip_box = None

        span = min(h / 2 - 8, 26)
        for i in range(WAVE_RINGS):
            r = 11 + i * max(6, span / WAVE_RINGS)
            self._wave_items.append(
                c.create_arc(
                    ox - r, oy - r, ox + r, oy + r,
                    start=-52, extent=104, style=tk.ARC,
                    outline=WAVE_IDLE, width=2.4, tags="wave",
                )
            )

    def animate(self):
        """Drive the meter at a steady frame rate off the Tk event loop."""
        if self.closing:
            return

        # Ease towards the newest reading so the rings glide rather than snap
        target = min(1.0, self._level_raw / self._level_scale)
        self._level += (target - self._level) * 0.35
        self._phase += 1.0 / WAVE_FPS

        for i, item in enumerate(self._wave_items):
            if not self._listening:
                colour, width = WAVE_IDLE, 2.0
            else:
                # Each ring lights once the level passes its share of the range,
                # and a slow outward pulse keeps it alive during quiet moments.
                lit = max(0.0, min(1.0, self._level * WAVE_RINGS - i))
                pulse = 0.5 + 0.5 * math.sin(self._phase * 3.2 - i * 0.9)
                strength = max(lit, 0.16 * pulse)
                colour = blend(WAVE_IDLE, WAVE_PEAK, strength)
                width = 2.0 + 1.8 * lit
            try:
                self.canvas.itemconfig(item, outline=colour, width=width)
            except tk.TclError:
                break  # canvas was rebuilt; skip this frame, keep the loop alive

        if self._border_items:
            if self._listening and self.border_glow:
                lap = (self._phase * BORDER_SPEED) % 1.0
                for item, t in self._border_items:
                    # Two crests chase each other around the frame. Using the
                    # arc position t (not the index) keeps them moving at a
                    # steady speed through the densely sampled corners.
                    wave = 0.5 + 0.5 * math.sin(2 * math.pi * (t - lap) * 2)
                    strength = max(BORDER_IDLE * wave,
                                   self._level * (0.30 + 0.70 * wave))
                    try:
                        self.canvas.itemconfig(
                            item, fill=blend(PANEL_BG, WAVE_PEAK, strength))
                    except tk.TclError:
                        break
            else:
                for item, _t in self._border_items:
                    try:
                        self.canvas.itemconfig(item, fill=PANEL_BG)
                    except tk.TclError:
                        break

        if self._busy and self._loader and self._loader_item is not None:
            x1, x2, y, seg = self._loader
            span = (x2 - x1) + seg
            # Wrap continuously; the segment enters on the left as it exits right
            pos = (self._phase * LOADER_SPEED) % 1.0
            sx = x1 - seg + pos * span
            try:
                self.canvas.coords(
                    self._loader_item,
                    max(x1, sx), y, min(x2, sx + seg), y,
                )
            except tk.TclError:
                pass

        self._wave_job = self.root.after(int(1000 / WAVE_FPS), self.animate)

    def set_level(self, rms):
        """Called from the audio thread for every chunk captured."""
        self._level_raw = rms

    def update_text(self, text):
        self._text = text
        if not self.closing:
            self.redraw()

    def font_for(self, text):
        """Han glyphs need the CJK face; Latin looks better in Segoe."""
        return self.cjk_font if has_cjk(text) else self.latin_font

    def direction_label(self):
        prefix = "" if self.input_mode != "auto" else "AUTO  "
        if self.last_direction:
            src, dst = self.last_direction
            if src == dst:
                return f"{prefix}{LANG_SHORT[src]}"
            return f"{prefix}{LANG_SHORT[src]} → {LANG_SHORT[dst]}"
        if self.input_mode != "auto":
            src = self.input_mode
            dst = self.resolve_target(src)
            return f"{LANG_SHORT[src]} → {LANG_SHORT[dst]}" if dst                 else LANG_SHORT[src]
        if self.target_mode != "auto":
            return f"AUTO → {LANG_SHORT[self.target_mode]}"
        return "AUTO"

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

    def show_translation(self, text):
        """A new translation replaces the caption and clears any warning."""
        self._has_translation = True
        self._notice = ""
        self._busy = False
        self._loader = self._loader_item = None
        self.update_text(text)

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

    # -- interaction -------------------------------------------------------
    def in_grip(self, x, y):
        return (
            x >= self.root.winfo_width() - GRIP_ZONE
            and y >= self.root.winfo_height() - GRIP_ZONE
        )

    def hit_control(self, x, y):
        for name, (x1, y1, x2, y2) in self._controls.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return name
        return None

    def in_chip(self, x, y):
        if not self._chip_box:
            return False
        x1, y1, x2, y2 = self._chip_box
        return x1 <= x <= x2 and y1 <= y <= y2

    def on_hover(self, event):
        control = self.hit_control(event.x, event.y)
        if not control and self.in_chip(event.x, event.y):
            control = "chip"
        if control:
            zone, cursor = control, "hand2"
        elif self.in_grip(event.x, event.y):
            zone, cursor = "grip", "size_nw_se"
        else:
            zone, cursor = None, "fleur"

        self.canvas.config(cursor=cursor)
        if zone != self._hover:      # only repaint when the zone actually changes
            self._hover = zone
            self.redraw()

    def on_press(self, event):
        control = self.hit_control(event.x, event.y)
        if control == "close":
            self.close()
            return
        if control == "listen":
            self.toggle_listening()
            return
        if control == "source":
            self.toggle_source()
            return
        if self.in_chip(event.x, event.y):
            self.show_menu(event, mode="language")
            return
        if control == "menu":
            self.show_menu(event)
            return
        self._start_rx, self._start_ry = event.x_root, event.y_root
        self._start_w = self.root.winfo_width()
        self._start_h = self.root.winfo_height()
        self._start_x = self.root.winfo_x()
        self._start_y = self.root.winfo_y()
        self._drag_mode = "resize" if self.in_grip(event.x, event.y) else "move"

    def on_drag(self, event):
        if not self._drag_mode:
            return
        dx = event.x_root - self._start_rx
        dy = event.y_root - self._start_ry

        if self._drag_mode == "resize":
            width = max(MIN_WIDTH, self._start_w + dx)
            height = max(MIN_HEIGHT, self._start_h + dy)
            self.root.geometry(f"{int(width)}x{int(height)}")
            self.redraw()
        else:
            self.root.geometry(f"+{self._start_x + dx}+{self._start_y + dy}")

    def show_menu(self, event, mode="full"):
        if self.closing:
            return
        if mode == "language":
            if self.panel is not None:
                self.panel.close()
                return
            try:
                self.panel = SettingsPanel(self, event.x_root, event.y_root,
                                           "language")
            except tk.TclError:
                self.panel = None
            return
        self.open_settings()

    def set_opacity(self, value):
        """Panel translucency, 0.35..1.0. A slider, because three named steps
        never landed where you actually wanted it."""
        self.opacity = max(0.35, min(1.0, value))
        try:
            self.root.attributes("-alpha", self.opacity)
        except tk.TclError:
            pass

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

    def _on_escape(self, _event=None):
        """Escape closes an open panel first, and only quits the app when
        nothing else is open. Previously it always quit, so pressing Escape
        to dismiss the settings window killed the whole app."""
        if self.settings_win is not None:
            self.settings_win.close()
            return
        if self.panel is not None:
            self.panel.close()
            return
        self.close()

    def close(self):
        self.closing = True
        if self._wave_job is not None:
            try:
                self.root.after_cancel(self._wave_job)
            except tk.TclError:
                pass
            self._wave_job = None
        self.root.destroy()

    # -- speech loop -------------------------------------------------------
    def on_ui(self, fn):
        """Run fn on the Tk thread. Tk is single threaded, so every widget
        touch from the capture thread has to come through here."""
        if self.closing:
            return
        try:
            self.root.after(0, fn)
        except RuntimeError:
            pass  # window went away mid-flight

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
        appconfig.save(provider=provider, key=key, covers_speech=covers_speech)
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

    def open_ai_dialog(self):
        if self.panel is not None:
            self.panel.close()
        try:
            AISettingsDialog(self)
        except tk.TclError:
            pass

    def _verify_ai(self):
        engine = self.get_ai_engine()
        if engine is not None:
            self.show_status(f"AI translation on: {engine.name}")
        else:
            self.show_status(f"AI key rejected: {self.ai_error or 'check the key'}")

    def start_listening(self, engine_name):
        pairs = translation_pairs()
        log(f"--- start: engine={engine_name} langs={sorted(LANGUAGES)} "
            f"input={self.input_mode} ---")
        try:
            self.engine = build_engine(engine_name, pairs)
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
                    # Media is continuous; a microphone has pauses to cut on
                    if self.device_kind == "loopback":
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
            self.on_ui(lambda t=translated: self.show_translation(t))

    def run(self):
        self.root.mainloop()


def run_setup():
    """Prepare everything a first launch would otherwise wait for.

    The installer calls this so the speech model is already on disk before
    the user ever opens the app. Prints progress and exits.
    """
    print("Preparing Live Translator...", flush=True)
    pairs = translation_pairs()
    engine = build_engine(ENGINE, pairs)
    engine.warm_up()

    # Prove the bundled models really translate, rather than merely loading.
    # This runs at install time, so a broken bundle is caught immediately
    # instead of the first time someone speaks.
    checks = [("Good morning everyone.", "en", "zt"),
              ("大家早上好", "zt", "en"),
              ("Good morning everyone.", "en", "sw")]
    for text, src, dst in checks:
        out = engine.translate(text, src, dst)
        if not out.strip():
            raise RuntimeError(f"translation {src}->{dst} returned nothing")
        print(f"  {src}->{dst}: {text}  ->  {out}", flush=True)
    print("Translation ready (bundled, no download needed).", flush=True)

    def announce(model_size):
        size = WHISPER_DOWNLOAD_MB.get(model_size, "")
        print(f"Downloading speech model ({size} MB). This happens once.",
              flush=True)

    stt = WhisperSTT(WHISPER_MODELS[DEFAULT_WHISPER], notify=announce)
    stt.warm_up()
    print("Speech model ready. Live Translator now works offline.", flush=True)
    return 0


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


if __name__ == "__main__":
    if "--setup" in sys.argv:
        raise SystemExit(run_setup())
    app = FloatingTranslator()
    apply_startup_options(app, sys.argv)
    app.run()
