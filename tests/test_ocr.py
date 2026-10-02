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
