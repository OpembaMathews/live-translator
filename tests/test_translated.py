"""Reading a paper aloud in another language."""
import pytest

from livetranslator.reader.player import Passage, Player
from livetranslator.reader.translated import (
    PHONEMES, VOICES, Translation, default_voice, voices_for,
)
from livetranslator.reader.voice import Spoken


class FakeEngine:
    """Records what it was asked, so the test can check the source text."""

    def __init__(self, fail=False):
        self.asked = []
        self.fail = fail

    def translate(self, text, source, target):
        self.asked.append((text, source, target))
        if self.fail:
            raise RuntimeError("no route")
        return f"[{target}] {text}"


class FakeVoice:
    def __init__(self):
        self.said = []

    def say(self, text, speed=1.0, voice=None, lang="en-us"):
        self.said.append((text, voice, lang))
        return Spoken(text, [], [])


def a_passage(index=0, source="Older adults chose technology.", text=None):
    return Passage(index, text if text is not None else source, source,
                   0, (0, 0, 10, 10), "text")


def test_the_printed_sentence_is_what_gets_translated():
    """The spoken form says "S-O-C", which is not English worth translating."""
    engine = FakeEngine()
    into = Translation("zt", engine)
    item = a_passage(source="The S O C model was used.",
                     text="The S-O-C model was used.")
    into.text_for(item)
    assert engine.asked == [("The S O C model was used.", "en", "zt")]


def test_a_sentence_is_translated_once_and_kept():
    engine = FakeEngine()
    into = Translation("zt", engine)
    item = a_passage()
    assert into.text_for(item) == into.text_for(item)
    assert len(engine.asked) == 1, "stepping back must not pay again"


def test_a_failed_translation_falls_back_to_the_original():
    """Silence would look like a crash; the English still gets read."""
    into = Translation("zt", FakeEngine(fail=True))
    item = a_passage()
    assert into.text_for(item) == item.source


def test_chinese_is_spoken_in_a_chinese_voice():
    voice = FakeVoice()
    into = Translation("zt", FakeEngine())
    into.say(voice, a_passage(), 1.0)
    text, name, lang = voice.said[0]
    assert text.startswith("[zt]")
    assert lang == PHONEMES["zt"]
    assert name.startswith("z"), "a Kokoro Mandarin voice is named zf_/zm_"


def test_a_chosen_voice_is_the_one_that_speaks():
    voice = FakeVoice()
    into = Translation("zt", FakeEngine(), voice="zm_yunxi")
    into.say(voice, a_passage(), 1.0)
    assert voice.said[0][1] == "zm_yunxi"


def test_every_language_offers_a_woman_and_a_man():
    """The point of the list is a choice, not a longer menu."""
    for code, offered in VOICES.items():
        sexes = {name[1] for _label, name in offered}
        assert sexes == {"f", "m"}, f"{code} offers only {sexes}"
        assert len(offered) >= 2


def test_a_voice_is_named_for_the_language_it_reads():
    """An American voice reading Mandarin is nonsense, not an accent."""
    for code, offered in VOICES.items():
        first = {"en": ("a", "b"), "zt": ("z",)}[code]
        for label, name in offered:
            assert name[0] in first, f"{name} cannot read {code}"
            assert "female" in label or "male" in label


def test_every_offered_language_has_voices_and_phonemes():
    from livetranslator.ui.reader_window import READ_IN

    for _name, code in READ_IN:
        assert voices_for(code), f"{code} is offered but nothing can speak it"
        assert code in PHONEMES, f"{code} has no espeak language"
        assert default_voice(code) == VOICES[code][0][1]


def test_the_player_reads_the_translation_not_the_original():
    items = [a_passage(0, "First sentence here."),
             a_passage(1, "Second sentence here.")]
    voice = FakeVoice()
    player = Player(voice, items, into=Translation("zt", FakeEngine()))
    player._next = 0
    player._stop.set()          # synthesise nothing; call the path directly
    for item in items:
        item.spoken = player.into.say(voice, item, 1.0)
    assert [t for t, _v, _l in voice.said] == ["[zt] First sentence here.",
                                               "[zt] Second sentence here."]


def test_reading_as_written_needs_no_engine():
    """The default path must not build a translator it will never use."""
    voice = FakeVoice()
    player = Player(voice, [a_passage()])
    assert player.into is None


@pytest.mark.parametrize("name", sorted(
    n for offered in VOICES.values() for _label, n in offered))
def test_the_model_really_has_this_voice(name):
    """A typo here is silent until someone picks that voice."""
    import os

    from livetranslator.reader.voice import MODEL, VOICES as VOICE_FILE, Voice

    if not (os.path.exists(MODEL) and os.path.exists(VOICE_FILE)):
        pytest.skip("the Kokoro model files are not installed")
    assert name in Voice().kokoro.get_voices()


def test_the_player_reads_in_the_voice_it_was_given():
    voice = FakeVoice()
    item = a_passage()
    player = Player(voice, [item], voice_name="am_michael")
    player.voice.say(item.text, speed=1.0, voice=player.voice_name)
    assert voice.said[0][1] == "am_michael"
