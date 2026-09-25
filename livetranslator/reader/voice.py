"""Speaking a document out loud, with word timings for the highlight.

Kokoro runs on ONNX Runtime, offline and without PyTorch, which matters
because Smart App Control blocks PyTorch on this machine. Measured here, the
full-precision model reads about 2.5 times faster than real time, so a
sentence can be made while the one before it is still playing.

Use the full model, not the smaller int8 one: on this processor int8 measured
almost four times slower than real time, which is unusable.
"""
import os
import re
from dataclasses import dataclass, field

MODEL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         "LiveTranslator", "tts-models", "kokoro")
MODEL = os.path.join(MODEL_DIR, "kokoro-v1.0.onnx")
VOICES = os.path.join(MODEL_DIR, "voices-v1.0.bin")
DEFAULT_VOICE = "af_heart"
THREADS = 8          # measured fastest here; 16 was slower than 8
SAMPLE_RATE = 24000
# Sounds that mark a gap between words rather than a word
BREAKS = set(" ,.;:!?…")
SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Spoken:
    """One sentence: its audio, and when each word is said within it."""
    text: str
    audio: object                      # float32 samples at SAMPLE_RATE
    words: list = field(default_factory=list)

    @property
    def seconds(self):
        return len(self.audio) / SAMPLE_RATE


def sentences(text):
    """Split on sentence ends, keeping the punctuation."""
    return [s.strip() for s in SENTENCE.split(text) if s.strip()]


class Voice:
    """Kokoro, loaded once and reused."""

    def __init__(self, model=MODEL, voices=VOICES, voice=DEFAULT_VOICE,
                 threads=THREADS):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        for path in (model, voices):
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"{path} is missing. Download the Kokoro model files into "
                    f"{MODEL_DIR}.")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        session = ort.InferenceSession(model, options,
                                       providers=["CPUExecutionProvider"])
        self.kokoro = Kokoro.from_session(session, voices)
        self.voice = voice
        if not self.kokoro.has_timings:
            raise RuntimeError(
                "this Kokoro export reports no durations, so words cannot be "
                "highlighted; use the model-files-v1.1 release")

    def say(self, text, speed=1.0):
        """Speak one sentence and report when each word is said."""
        audio, _rate, sounds = self.kokoro.create_timed(
            text, voice=self.voice, speed=speed, lang="en-us")
        spoken = Spoken(text, audio, [])
        spoken.words = align(text, sounds, spoken.seconds)
        return spoken


def align(text, sounds, total=None):
    """Group per-sound timings into words, matched to the written words.

    Kokoro times each sound, not each word. The sounds between gaps make one
    word, in order, so they zip back onto the text.

    Two short words occasionally share a group ("to be" becomes one), and a
    zip would then shift every later word onto its neighbour's timing, which
    highlights the wrong word for the rest of the sentence. When the counts
    disagree the timings are spread across the words by length instead: the
    highlight drifts a little, but never points at the wrong word.
    """
    groups, current, start, last_end = [], False, None, 0.0
    for sound in sounds:
        symbol = sound.phoneme.strip()
        if not symbol or symbol in BREAKS:
            if current:
                groups.append((start, last_end))
                current = False
            continue
        if not current:
            current, start = True, sound.start
        last_end = sound.end
    if current:
        groups.append((start, last_end))

    written = [w for w in re.findall(r"\S+", text) if re.search(r"\w", w)]
    if len(groups) == len(written):
        return [Word(w, s, e) for w, (s, e) in zip(written, groups)]
    return spread(written, total if total is not None else last_end)


def spread(written, total):
    """Share a span across words by how long each one is."""
    if not written or total <= 0:
        return [Word(w, 0.0, 0.0) for w in written]
    sizes = [len(w) for w in written]
    scale = total / sum(sizes)
    out, at = [], 0.0
    for word, size in zip(written, sizes):
        out.append(Word(word, at, at + size * scale))
        at += size * scale
    return out
