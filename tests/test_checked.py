"""Spotting a translation that broke off, and mending it."""
from livetranslator.translate import Checked


class Engine:
    """Answers from a script, so a test can stage a truncation."""

    def __init__(self, answers, fallback="翻譯。"):
        self.answers = answers
        self.fallback = fallback
        self.asked = []

    def translate(self, text, source, target):
        self.asked.append(text)
        return self.answers.get(text, self.fallback)


WHOLE = ("Older adults selected technology intentionally, "
         "to enhance different aspects of their daily lives.")


def test_a_good_translation_is_left_alone():
    engine = Engine({WHOLE: "年長者有意選擇科技。"})
    got = Checked(engine).attempt(WHOLE, "en", "zt")
    assert got.ok and not got.repaired
    assert engine.asked == [WHOLE], "a sound sentence must not be retried"


def test_a_sentence_that_breaks_off_is_retried_by_clause():
    engine = Engine({
        WHOLE: "年長者選擇的技術,",          # ends on a comma: broke off
        "Older adults selected technology intentionally": "年長者有意選擇科技",
        "to enhance different aspects of their daily lives.": "以提升日常生活的不同方面。",
    })
    got = Checked(engine).attempt(WHOLE, "en", "zt")
    assert got.ok and got.repaired
    assert got.text == ("年長者有意選擇科技"
                        "，以提升日常生活的不同方面。")
    assert len(engine.asked) == 3, "the whole sentence, then each clause"


def test_a_sentence_with_one_clause_cannot_be_mended():
    """Nothing to split, so it is reported rather than quietly accepted."""
    text = "Compensation involves acquiring resources."
    engine = Engine({text: "補償涉及,"})
    checker = Checked(engine)
    got = checker.attempt(text, "en", "zt")
    assert not got.ok and not got.repaired
    assert got.text == "補償涉及,", "the original is still returned"
    assert checker.suspect == 1


def test_a_retry_that_breaks_off_again_is_not_accepted():
    engine = Engine({
        WHOLE: "年長者選擇的技術,",
        "Older adults selected technology intentionally": "年長者,",
        "to enhance different aspects of their daily lives.": "以,",
    })
    got = Checked(engine).attempt(WHOLE, "en", "zt")
    assert not got.ok, "a repair that fails the same check is no repair"


def test_the_plain_interface_still_returns_a_string():
    engine = Engine({WHOLE: "年長者有意選擇科技。"})
    assert isinstance(Checked(engine).translate(WHOLE, "en", "zt"), str)


def test_it_counts_what_it_did():
    engine = Engine({}, fallback="翻譯。")
    checker = Checked(engine)
    for _ in range(3):
        checker.attempt(WHOLE, "en", "zt")
    assert checker.checked == 3 and checker.repairs == 0


def test_the_reader_remembers_which_sentences_it_doubts():
    from livetranslator.reader.player import Passage
    from livetranslator.reader.translated import Translation

    text = "Compensation involves acquiring resources."
    into = Translation("zt", Engine({text: "補償涉及,"}))
    item = Passage(7, text, text, 0, (0, 0, 1, 1), "text")
    into.text_for(item)
    assert into.doubted(7)
    assert not into.doubted(8)
