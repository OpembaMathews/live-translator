"""What the app says between opening the microphone and the first caption.

A real session captured thirteen phrases over thirty-nine seconds without
showing anything: each one held no recognisable speech, so nothing reached
the screen, and the status line said "Listening" throughout. That is
indistinguishable from broken.
"""
from livetranslator.engine import LiveTranslator


class Standin(LiveTranslator):
    """The reporting, with the engine and the audio taken out from under it."""

    def __init__(self):
        self.said = []
        self.paused = False
        self._listening = True
        self._level_raw = 0.0
        self._captions_shown = 0
        self._phrases_caught = 0

    def show_status(self, text, busy=None):
        self.said.append(text)


def test_the_first_phrase_says_so():
    """Silence while it works is what made the app look dead."""
    app = Standin()
    app.note_capture(2.0)
    assert app.said, "capturing audio and saying nothing is the bug"
    assert "Transcribing" in app.said[0]
    assert "seconds" in app.said[0], "say roughly how long, not nothing"


def test_it_keeps_saying_so_while_nothing_is_recognised():
    app = Standin()
    for _ in range(10):
        app.note_capture(2.0)
    assert len(app.said) == 3, "a message at the first, fourth and tenth"
    assert "closer to the microphone" in app.said[-1], "say what to do about it"


def test_it_stops_once_captions_are_flowing():
    """After the first caption the captions are the feedback."""
    app = Standin()
    app.note_capture(2.0)
    app._captions_shown = 1
    before = len(app.said)
    for _ in range(20):
        app.note_capture(2.0)
    assert len(app.said) == before, "it must not talk over the captions"


def test_stopping_and_starting_explains_itself_again():
    app = Standin()
    app.note_capture(2.0)
    app._captions_shown = 3
    app.note_capture(2.0)
    assert len(app.said) == 1

    # what toggle_listening does when it pauses
    app._phrases_caught = 0
    app._captions_shown = 0
    app.note_capture(2.0)
    assert len(app.said) == 2, "a new session starts explaining again"


def test_it_never_sounds_like_it_is_saving_words_up():
    """Nothing is banked: a phrase is transcribed as it arrives or dropped.

    Counting what had been heard read as though the app were collecting
    words to translate later, which is the opposite of what it does.
    """
    app = Standin()
    for _ in range(10):
        app.note_capture(2.0)
    for line in app.said:
        assert "phrases heard" not in line
        assert not any(c.isdigit() for c in line), f"a tally crept back: {line}"


# --- how much audio the model gets to work with ---------------------------
def test_media_windows_are_long_enough_to_be_understood():
    """Whisper reads each window knowing nothing of the one before it.

    Measured on 25 seconds of a Chinese video, one model, only the window
    length changed: 3s gave eleven mostly-nonsense fragments and cost 21.5s
    of CPU; 10s gave three pieces close to a single pass and cost 7.7s.
    Short windows were the worst of both.
    """
    from livetranslator.config import RESPONSE_PRESETS

    for name, preset in RESPONSE_PRESETS.items():
        window, overlap = preset[2], preset[3]
        assert window >= 5.0, f"{name} gives the model only {window}s"
        assert overlap >= 0.8, f"{name} can cut a word at every boundary"
        assert overlap < window / 2, f"{name} repeats more than it advances"


def test_the_presets_are_ordered_the_way_they_are_named():
    from livetranslator.config import RESPONSE_PRESETS

    fast = RESPONSE_PRESETS["Fast"]
    balanced = RESPONSE_PRESETS["Balanced"]
    accurate = RESPONSE_PRESETS["Accurate"]
    assert fast[2] < balanced[2] < accurate[2], \
        "a faster preset must not hand the model more audio to chew on"
    assert fast[0] < balanced[0] < accurate[0]
