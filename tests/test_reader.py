"""Tests for the reading rules and the pronunciation pass.

The PDF tests need a real paper, and papers are not committed here, so they
are skipped unless one is present. Point READER_TEST_PDF at a journal article
to run them:

    set READER_TEST_PDF=C:\\path\\to\\paper.pdf
    pytest
"""
import os
import re

import pytest

from livetranslator.reader import document, speakable

PDF = os.environ.get("READER_TEST_PDF")
needs_pdf = pytest.mark.skipif(not (PDF and os.path.exists(PDF)),
                               reason="set READER_TEST_PDF to a journal article")


# --- joining wrapped lines -------------------------------------------------
def test_wrapped_lines_get_the_missing_space():
    # A PDF line carries no trailing space, so a naive join glues words
    assert document.join_lines(["and are", "becoming essential"]) \
        == "and are becoming essential"
    assert document.join_lines(["aged", "55 years"]) == "aged 55 years"


def test_a_word_split_at_a_line_end_is_rejoined():
    assert document.join_lines(["online plat-", "forms."]) == "online platforms."
    assert document.join_lines(["collabora-", "tion"]) == "collaboration"


def test_a_real_compound_keeps_its_hyphen():
    assert document.join_lines(["self-", "monitoring"]) == "self-monitoring"
    assert document.join_lines(["in-", "person focus groups"]) \
        == "in-person focus groups"


def test_a_capital_after_a_hyphen_is_not_a_split_word():
    assert document.join_lines(["the Smith-", "Jones method"]) \
        == "the Smith- Jones method"


# --- the authors line ------------------------------------------------------
def test_degrees_and_affiliation_numbers_leave_the_authors_line():
    line = "Wei Qi Koh , PhD; Kristiana Ludlow , PhD; Nancy A Pachana , PhD"
    assert document._authors(line) == \
        "By Wei Qi Koh, Kristiana Ludlow, and Nancy A Pachana."


def test_a_single_author_reads_without_a_list():
    assert document._authors("Wei Qi Koh , PhD") == "By Wei Qi Koh."


# --- pronunciation ---------------------------------------------------------
def test_a_defined_abbreviation_is_spelled_out_every_time():
    text = ("the Selection, Optimization, and Compensation (SOC) model, "
            "analyzed with respect to SOC processes")
    said = speakable.speakable(text)
    assert "S-O-C" in said
    assert not re.search(r"\bSOC\b", said)


def test_a_defined_abbreviation_gets_a_pause_where_it_is_introduced():
    said = speakable.speakable(
        "the Selection, Optimization, and Compensation (SOC) model")
    assert said ==         "the Selection, Optimization, and Compensation, S-O-C, model"


def test_an_abbreviation_the_long_form_does_not_spell_is_left_alone():
    # "Compensation" does not spell SOC, so this is not a definition
    assert speakable.speakable("the Compensation (SOC) model")         == "the Compensation (SOC) model"


def test_two_letter_capitals_are_spelled_out():
    # Kokoro reads "UQ" as "uck" on its own
    assert "U-Q" in speakable.speakable("the University of Queensland (UQ)")


def test_words_that_are_said_as_words_are_left_alone():
    said = speakable.speakable("funding from NASA during COVID")
    assert "NASA" in said and "COVID" in said
    assert "N-A-S-A" not in said


def test_a_heading_in_capitals_is_not_spelled_out():
    # "OF" is a word here, not an abbreviation
    assert speakable.speakable("RESULTS OF THE STUDY IN BRIEF") \
        == "RESULTS OF THE STUDY IN BRIEF"


def test_currency_is_left_alone():
    # Rewriting it broke the grammar of "a AU $20 gift voucher"
    assert "$20" in speakable.speakable("a AU $20 gift voucher")


# --- the reading plan, against a real paper --------------------------------
@needs_pdf
def test_the_plan_reads_the_title_first_and_skips_the_contact_details():
    doc = document.open_document(PDF)
    rows = document.plan(doc)
    read = [r for r in rows if r.action == document.READ]
    assert read[0].reason == "title"
    reasons = [r.reason for r in rows if r.action == document.SKIP]
    assert "running header" in reasons
    assert any("contact details" in r for r in reasons)


@needs_pdf
def test_reading_stops_at_the_back_matter():
    doc = document.open_document(PDF)
    rows = document.plan(doc)
    stops = [r for r in rows if r.action == document.STOP]
    assert len(stops) == 1, "reading should end exactly once"
    after = [r for r in rows if r.page > stops[0].page]
    assert all(r.action == document.SKIP for r in after), \
        "nothing after the back matter should be read"


@needs_pdf
def test_no_glued_or_split_words_survive_into_the_speech():
    doc = document.open_document(PDF)
    said = " ".join(r.text for r in document.read_lines(doc))
    assert not re.search(r"[a-z]- [a-z]", said), "a split word was left"
    assert not re.search(r"\[\d", said), "a citation bracket was left"


@needs_pdf
def test_a_table_is_announced_but_its_rows_are_not_read():
    doc = document.open_document(PDF)
    rows = document.plan(doc)
    captions = [r for r in rows if r.reason.startswith("table caption")]
    if not captions:
        pytest.skip("this paper has no table")
    assert "skipped" in captions[0].text
    assert any(r.reason == "table row" for r in rows)


# --- word timings ----------------------------------------------------------
class FakeSound:
    def __init__(self, phoneme, start, end):
        self.phoneme, self.start, self.end = phoneme, start, end


def test_timings_map_to_words_when_the_counts_agree():
    from livetranslator.reader import voice
    sounds = [FakeSound("h", 0.0, 0.2), FakeSound("i", 0.2, 0.4),
              FakeSound(" ", 0.4, 0.45),
              FakeSound("ð", 0.45, 0.6), FakeSound("ɛɹ", 0.6, 0.9)]
    words = voice.align("hi there", sounds, 0.9)
    assert [w.text for w in words] == ["hi", "there"]
    assert words[0].start == 0.0 and words[1].end == 0.9


def test_a_merged_pair_does_not_shift_every_later_word():
    from livetranslator.reader import voice
    # three written words, two groups: a zip would put "c" on b's timing
    sounds = [FakeSound("a", 0.0, 0.3), FakeSound(" ", 0.3, 0.35),
              FakeSound("bc", 0.35, 0.9)]
    words = voice.align("aa bb cc", sounds, 0.9)
    assert [w.text for w in words] == ["aa", "bb", "cc"]
    assert words[0].start == 0.0
    assert words[-1].end == pytest.approx(0.9)
    assert words[1].start < words[2].start, "words must stay in order"


# --- merging highlight boxes ----------------------------------------------
def test_boxes_on_one_line_merge_into_one():
    from livetranslator.ui.reader_window import merge_lines
    # a search returns a box per fragment; the line should end up as one
    row = merge_lines([(10, 100, 40, 112), (42, 100, 90, 112),
                       (92, 101, 130, 113)])
    assert row == [(10, 100, 130, 113)]


def test_separate_lines_stay_separate():
    from livetranslator.ui.reader_window import merge_lines
    rows = merge_lines([(10, 100, 90, 112), (10, 120, 60, 132)])
    assert len(rows) == 2
    assert rows[0][1] < rows[1][1], "lines keep their order down the page"


# --- links, which are unlistenable when read out in full -------------------
def test_a_web_address_becomes_the_word_url():
    said = speakable.speakable(
        "The data are at https://aging.jmir.org/2025/1/e75019 for readers.")
    assert said == "The data are at URL for readers."


def test_a_doi_is_spelled_rather_than_read_as_doy():
    for text in ("doi: 10.2196/75019", "https://doi.org/10.2196/75019"):
        assert speakable.speakable(f"See {text} for details.") \
            == "See D-O-I for details."


def test_an_email_address_is_named_not_spelled():
    assert speakable.speakable("Write to weiqi.koh@uq.edu.au today.") \
        == "Write to an email address today."


def test_ordinary_text_with_a_full_stop_is_untouched():
    text = "The result was 10.5 percent. That was the finding."
    assert speakable.speakable(text) == text


def test_the_punctuation_after_a_link_survives():
    # the address runs to the next space, so it would swallow the comma
    assert speakable.speakable("See doi: 10.2196/75019, then read on.") \
        == "See D-O-I, then read on."
    assert speakable.speakable("Data are at www.example.org/set.") \
        == "Data are at URL."
