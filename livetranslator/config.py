"""Languages, and every tunable number the engine runs on.

Nothing here does any work. It is the one place to look up or adjust a
threshold, and each value carries the reason it has the value it has.
"""


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

# Streaming captions, as an alternative to waiting for a pause.
#
# Waiting for silence means a sentence shows nothing until it ends, and a long
# one is cut at the phrase limit. Instead a rolling buffer is re-read every
# STREAM_STEP seconds, and a word is only shown once two consecutive reads
# agree on it. Whisper rewrites the end of a buffer constantly but almost
# never revises the start, so agreement is what makes an early word safe to
# show. Measured on a 14.5s clip: median 4.2s behind the speaker with phrase
# capture, 2.1s with this, and no words lost at phrase boundaries.
CAPTURE_MODES = ("Phrase", "Streaming")
DEFAULT_CAPTURE = "Phrase"
STREAM_STEP = 0.9          # seconds of new audio between reads
STREAM_MAX_BUFFER = 16.0   # force a flush rather than re-reading forever
STREAM_QUIET_FLUSH = 1.0   # silence that ends an utterance and commits the tail
STREAM_SENTENCE_END = ".!?。！？"
STREAM_TRIM_AT = 8.0       # cut back to the last committed word past this
STREAM_SILENCE_PEAK = 0.01  # below this the buffer is silence, not speech
# Lift by loudness rather than by peak. A quiet mic with one transient in the
# buffer gets almost no gain from peak scaling: measured on this laptop, peak
# scaling took speech to 0.03 RMS where Whisper mostly returned nothing.
STREAM_TARGET_RMS = 0.08
STREAM_CEILING = 0.95
STREAM_MIN_FIRST = 2.0     # audio to gather before the first read of a phrase
STREAM_VOICE_FLOOR = 0.0015   # absolute RMS below which a chunk is not speech
STREAM_VOICE_OVER_NOISE = 4.0  # ...or this far above the quietest chunk seen

# Fallback full-scale RMS. In practice the meter rescales itself against the
# recogniser's ambient-noise calibration, since a quiet room and a loud hall
# differ by more than an order of magnitude.
WAVE_FULL_SCALE = 900.0
WAVE_SCALE_FLOOR = 400.0      # never get so twitchy that room tone lights it
WAVE_THRESHOLD_SPAN = 6.0     # full rings at ~6x the speech-detection threshold
