"""Tests for the reading player, using a stand-in voice and no sound card."""
import time

import numpy as np
import pytest

from livetranslator.reader import player as player_mod
from livetranslator.reader.document import Line
from livetranslator.reader.voice import SAMPLE_RATE, Spoken


class FakeVoice:
    """Returns a fixed length of silence, quickly, and counts the calls."""

    def __init__(self, seconds=0.4):
        self.seconds, self.said = seconds, []

    def say(self, text, speed=1.0, voice=None, lang="en-us"):
        self.said.append(text)
        samples = np.zeros(int(SAMPLE_RATE * self.seconds), dtype=np.float32)
        return Spoken(text, samples, [])


class FakeStream:
    """Stands in for the sound card, consuming audio at the speed it plays.

    A stream that accepted everything instantly would let the whole reading
    finish before a test could pause it.
    """

    def __init__(self):
        self.bytes = 0

    def write(self, data):
        frames = len(data) / 4           # float32
        time.sleep(frames / SAMPLE_RATE)
        self.bytes += len(data)

    def stop_stream(self):
        pass

    def close(self):
        pass


@pytest.fixture
def silent(monkeypatch):
    stream = FakeStream()
    monkeypatch.setattr(player_mod.Player, "_open_stream", lambda self: stream)
    return stream


def make_player(voice, count=4, **kw):
    lines = [Line(0, (0, 0, 1, 1), "read", "text", f"Sentence {i}.")
             for i in range(count)]
    return player_mod.Player(voice, player_mod.passages(lines), **kw)


def test_a_line_becomes_one_passage_per_sentence():
    lines = [Line(2, (1, 2, 3, 4), "read", "text", "One. Two. Three.")]
    items = player_mod.passages(lines)
    assert [i.text for i in items] == ["One.", "Two.", "Three."]
    assert all(i.page == 2 and i.bbox == (1, 2, 3, 4) for i in items)
    assert [i.index for i in items] == [0, 1, 2]


def test_abbreviations_are_fixed_before_the_voice_sees_them():
    lines = [Line(0, (0, 0, 1, 1), "read", "text",
                  "The Selection, Optimization, and Compensation (SOC) model.")]
    assert "S-O-C" in player_mod.passages(lines)[0].text


def test_it_plays_every_passage_in_order(silent):
    voice = FakeVoice(0.2)
    p = make_player(voice, 4)
    p.start()
    for _ in range(100):
        if not p.playing:
            break
        time.sleep(0.05)
    p.stop()
    assert voice.said == [f"Sentence {i}." for i in range(4)]
    assert silent.bytes == pytest.approx(4 * 0.2 * SAMPLE_RATE * 4, rel=0.05)


def test_it_reports_where_it_is(silent):
    seen = []
    p = make_player(FakeVoice(0.3), 3, on_change=seen.append)
    p.start()
    time.sleep(0.4)
    index, elapsed = p.position()
    p.stop()
    assert seen[0] == 0
    assert 0 <= index < 3
    assert elapsed >= 0


def test_pause_stops_the_audio_and_resume_continues_it(silent):
    p = make_player(FakeVoice(1.0), 3)
    p.start()
    time.sleep(0.2)
    p.pause()
    written = silent.bytes
    time.sleep(0.3)
    chunk = player_mod.CHUNK * 4       # a write already begun still finishes
    assert silent.bytes - written <= chunk, "paused playback kept writing"
    p.resume()
    time.sleep(0.3)
    assert silent.bytes > written, "resumed playback wrote nothing"
    p.stop()


def test_jump_starts_from_the_chosen_passage(silent):
    voice = FakeVoice(0.15)
    p = make_player(voice, 5)
    p.start()
    time.sleep(0.1)
    voice.said.clear()
    p.jump(3)
    time.sleep(0.5)
    p.stop()
    assert voice.said and voice.said[0] == "Sentence 3."


def test_a_sentence_that_will_not_speak_is_skipped(silent):
    class Broken(FakeVoice):
        def say(self, text, speed=1.0, voice=None, lang="en-us"):
            if "2" in text:
                raise RuntimeError("no")
            return super().say(text, speed)

    voice = Broken(0.1)
    p = make_player(voice, 4)
    p.start()
    for _ in range(100):
        if not p.playing:
            break
        time.sleep(0.05)
    p.stop()
    # the reading carried on past the broken sentence
    assert "Sentence 3." in voice.said


def test_stepping_and_restarting_stay_inside_the_document():
    """go_to() clamps, so the arrows cannot run off either end."""
    from livetranslator.reader.document import Line

    lines = [Line(0, (0, 0, 1, 1), "read", "text", "One. Two. Three.")]
    items = player_mod.passages(lines)
    assert len(items) == 3
    for asked, expected in ((-5, 0), (0, 0), (2, 2), (99, 2)):
        assert max(0, min(asked, len(items) - 1)) == expected
