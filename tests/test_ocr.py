"""Reading a paper that has no text in it.

The recogniser itself is a separate 140 MB download, so these test the
parts that decide what it is given and what is done with what comes back.
"""
import pytest

from livetranslator.reader import ocr

# the real joins, taken from a paper the reader could not read at all
PAPER = [
    "the digital divide presented between the Global South and North",
    "online interactions and sociocultural factors in education",
    "thanks to widespread services and practices for the community",
    "that many low income households lack information transformation",
    "a qualitative study in a northeastern neighborhood in Switzerland",
]


@pytest.fixture
def known():
    return ocr.vocabulary(PAPER)


def test_the_paper_is_its_own_dictionary(known):
    assert "sociocultural" in known and "factors" in known
    assert known["the"] >= 2, "it counts, so common words can be told apart"


@pytest.mark.parametrize("joined,mended", [
    ("dividepresented", "divide presented"),
    ("ofeducation", "of education"),
    ("onlineinteractions", "online interactions"),
    ("socioculturalfactors", "sociocultural factors"),
    ("servicesand", "services and"),
    ("thecommunity", "the community"),
])
def test_run_together_words_are_separated(joined, mended, known):
    assert ocr.space_out(joined, known) == mended


@pytest.mark.parametrize("joined,mended", [
    ("thankstowidespread", "thanks to widespread"),
    ("thatmanylow", "that many low"),
])
def test_three_words_run_together_are_separated(joined, mended, known):
    """One cut and a look at the tail is not enough: the middle piece of
    "thankstowidespread" is "to", so a single cut leaves "towidespread"."""
    assert ocr.space_out(joined, known) == mended


@pytest.mark.parametrize("word", [
    "sociocultural", "Switzerland", "opportunities", "northeastern",
    "qualitative", "neighborhood", "transformation",
])
def test_real_words_are_left_alone(word, known):
    assert ocr.space_out(word, known) == word


def test_capitals_survive_the_split(known):
    assert ocr.space_out("Practicesfor", known) == "Practices for"
    assert ocr.space_out("Transformationin", known) == "Transformation in"


def test_a_word_the_paper_uses_whole_is_never_split(known):
    """"information" could be cut into "in" and "formation", both of which
    are words. It is not, because the paper uses it whole."""
    assert ocr.space_out("information", known) == "information"


def test_nothing_is_split_without_a_vocabulary():
    """Before the whole document has been read there is nothing to judge
    against, and guessing would invent errors."""
    assert ocr.space_out("ofeducation") == "ofeducation"


def test_a_lower_case_meeting_a_capital_is_always_split():
    """This one needs no vocabulary: it cannot occur inside a word."""
    assert ocr.space_out("Abstract.The") == "Abstract.The"
    assert ocr.space_out("dividePresented") == "divide Presented"
    assert ocr.space_out("2.3SocioculturalPerspective") == \
        "2.3 Sociocultural Perspective"


def test_the_recognised_copy_is_kept_per_file(tmp_path):
    first = tmp_path / "one.pdf"
    second = tmp_path / "two.pdf"
    first.write_bytes(b"%PDF-1.4 one")
    second.write_bytes(b"%PDF-1.4 two")
    assert ocr.cache_path(str(first)) != ocr.cache_path(str(second))
    assert ocr.cache_path(str(first)) == ocr.cache_path(str(first))


def test_editing_a_paper_invalidates_its_recognised_copy(tmp_path):
    """Keyed by what the file is, not just where it is, so a corrected
    scan is not read from the old recognition."""
    import os
    import time

    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4 first")
    before = ocr.cache_path(str(path))
    time.sleep(1.1)
    path.write_bytes(b"%PDF-1.4 second, longer than the first")
    os.utime(path, None)
    assert ocr.cache_path(str(path)) != before


def test_it_says_when_the_recogniser_is_not_installed():
    assert isinstance(ocr.available(), bool)


# --- choosing between decompositions that are all "valid" -----------------
def test_fewest_pieces_wins(known):
    """"used" is one word. Preferring the evenest split made it "us ed",
    because that is even and both halves can be found somewhere."""
    vocab = ocr.vocabulary(PAPER + ["as my father used to say", "us", "ed ed ed"])
    assert ocr.split_word("fatherused", vocab) == ["father", "used"]


def test_a_rare_short_fragment_is_refused(known):
    """"conditions in" and "condition sin" are both two pieces of the same
    lengths. Only one of them is English, and it is the common one."""
    vocab = ocr.vocabulary(
        ["conditions in vulnerable communities"] * 4 + ["the sin"])
    assert ocr.space_out("conditionsin", vocab) == "conditions in"


def test_a_word_is_left_joined_rather_than_split_wrongly():
    """When no decomposition is trustworthy, the word stays as it is. A
    nonsense word read aloud is bad; the wrong words read aloud is worse."""
    vocab = ocr.vocabulary(["each participant per form ed the task"] )
    # "performed" is not in this document at all
    assert ocr.split_word("participantperformed", vocab) is None


def test_the_recognisers_habitual_joins_are_always_split():
    """"ofthe" appears so often that it looks like a word to any count of
    its own, so frequency cannot spot it."""
    vocab = ocr.vocabulary(["ofthe"] * 30)
    assert ocr.space_out("ofthe", vocab) == "of the"
    assert ocr.space_out("Ofthe", vocab) == "Of the"


def test_a_long_run_seen_once_is_not_trusted_as_a_word():
    """The vocabulary is built from the recogniser's own output, so it
    contains the recogniser's own mistakes."""
    vocab = ocr.vocabulary(
        ["as my father used to say", "Asmyfatherusedtosay"])
    assert ocr.split_word("Asmyfatherusedtosay", vocab) == \
        ["As", "my", "father", "used", "to", "say"]


def test_a_long_word_the_paper_really_uses_is_kept():
    vocab = ocr.vocabulary(["interculturality matters"] * 3)
    assert ocr.space_out("interculturality", vocab) == "interculturality"


def test_the_common_words_cover_what_a_paper_may_never_use_alone():
    """"say" does not appear on its own in a paper about the digital
    divide, and without it "usedtosay" cannot be taken apart."""
    for word in ("say", "one", "take", "off", "because", "going", "sister"):
        assert word in ocr.GLUE, f"{word} is missing from the common words"


def test_splitting_a_whole_page_is_quick():
    """It runs over every line of every page, so it cannot be slow."""
    import time

    vocab = ocr.vocabulary(PAPER * 20)
    lines = ["thecommunity ofeducation socioculturalfactors " * 6] * 60
    start = time.perf_counter()
    for line in lines:
        ocr.space_out(line, vocab)
    assert time.perf_counter() - start < 2.0


def test_improving_the_code_invalidates_what_was_kept(tmp_path, monkeypatch):
    """A kept copy is the output of this code, not just of this file. An
    improvement nobody sees because the old answer is still on disk is no
    improvement at all."""
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4 unchanged")
    before = ocr.cache_path(str(path))
    monkeypatch.setattr(ocr, "VERSION", ocr.VERSION + 1)
    assert ocr.cache_path(str(path)) != before
