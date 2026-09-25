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


def a_page_view(path):
    """A PageView with a PDF loaded. It is a widget, so Qt must be up."""
    import pymupdf
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from livetranslator.ui.reader_window import PageView

    view = PageView()
    view.load(pymupdf.open(path))
    return view


# --- highlighting the sentence being read ---------------------------------
def test_characters_on_one_line_become_one_highlight():
    from livetranslator.ui.reader_window import rows_of
    row = rows_of([(10, 100, 40, 112), (40, 100, 90, 112),
                   (90, 101, 130, 113)])
    assert row == [(10, 100, 130, 113)]


def test_separate_lines_stay_separate():
    from livetranslator.ui.reader_window import rows_of
    rows = rows_of([(10, 100, 90, 112), (10, 120, 60, 132)])
    assert len(rows) == 2
    assert rows[0][1] < rows[1][1], "lines keep their order down the page"


def test_a_kerned_letter_does_not_start_a_new_line():
    """A wide "T" overlaps the letter after it, which split "Technology"."""
    from livetranslator.ui.reader_window import rows_of
    rows = rows_of([(10, 100, 40, 121), (38, 100, 60, 121)])
    assert rows == [(10, 100, 60, 121)]


def test_the_gutter_of_a_two_column_page_is_not_bridged():
    """Two columns put text at the same height on both sides of a gap.

    One rectangle across both would tint the middle of the page, so a long
    jump to the right ends the line even when the height matches.
    """
    from livetranslator.ui.reader_window import rows_of
    rows = rows_of([(40, 100, 250, 112), (320, 100, 520, 112)])
    assert len(rows) == 2, "the highlight jumped the gutter"


def test_a_title_is_highlighted_to_its_last_word(tmp_path):
    """Searching for the printed text missed a title on three counts.

    The reader adds a full stop so the voice pauses, the page breaks the
    title across lines, and search_for found neither -- so only the first
    seven words were tinted.
    """
    import pymupdf

    from livetranslator.reader.player import Passage
    title = ("Selection, Optimization, and Compensation Strategies "
             "Used by Older Adults to Live Well With Technology")
    doc = pymupdf.open()
    page = doc.new_page()
    box = pymupdf.Rect(40, 60, 300, 160)
    page.insert_textbox(box, title, fontsize=17, fontname="helv")
    path = str(tmp_path / "title.pdf")
    doc.save(path)

    view = a_page_view(path)
    item = Passage(0, title, title + ".", 0, tuple(box), "title")
    boxes = view._find_boxes(item)

    def tinted(word):
        spot = view.doc[0].search_for(word)[0]
        return any(b[0] - 1 <= spot.x0 and spot.x1 <= b[2] + 1
                   and b[1] - 1 <= spot.y0 and spot.y1 <= b[3] + 1
                   for b in boxes)

    assert len(boxes) > 1, "the title wraps, so it needs a box per line"
    assert boxes != [tuple(box)], "it fell back to the block"
    assert tinted("Selection"), "the title does not start tinted"
    assert tinted("Technology"), "the last word of the title is not tinted"


def test_a_sentence_broken_across_lines_still_matches(tmp_path):
    """A hyphenated word is one word in the plan and two on the page."""
    import pymupdf

    from livetranslator.reader.player import Passage
    doc = pymupdf.open()
    page = doc.new_page()
    box = pymupdf.Rect(40, 60, 200, 200)
    page.insert_textbox(box, "Older adults described their own well-being "
                             "as the reason they kept going.",
                        fontsize=11, fontname="helv")
    path = str(tmp_path / "wrap.pdf")
    doc.save(path)

    view = a_page_view(path)
    item = Passage(0, "", "Older adults described their own well-being as "
                          "the reason they kept going.", 0, tuple(box), "text")
    assert view._find_boxes(item) != [tuple(box)], "it fell back to the block"


def test_the_nearer_of_two_identical_sentences_is_chosen(tmp_path):
    """"Yeah." appears all over an interview paper; tint the right one."""
    import pymupdf

    from livetranslator.reader.player import Passage
    doc = pymupdf.open()
    page = doc.new_page()
    for y in (100, 400):
        page.insert_text((60, y), "It was the same answer again",
                         fontsize=11, fontname="helv")
    path = str(tmp_path / "twice.pdf")
    doc.save(path)

    view = a_page_view(path)
    item = Passage(0, "", "It was the same answer again", 0,
                   (60, 390, 300, 404), "text")
    boxes = view._find_boxes(item)
    assert boxes[0][1] > 300, f"tinted the first copy, not the one read: {boxes}"


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


# --- page furniture, built as a small PDF so no paper is needed ------------
def make_pdf(tmp_path, pages=4, footer="Downloaded from example.org on page {n}"):
    import pymupdf

    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 60), "Journal of Testing, Vol 12", fontsize=9)
        page.insert_text((72, 300),
                         f"This is the body of page {n}, which should be read "
                         "aloud in full.", fontsize=11)
        page.insert_text((72, page.rect.height - 40), footer.format(n=n),
                         fontsize=8)
    path = tmp_path / "paper.pdf"
    doc.save(path)
    return document.open_document(str(path))


def test_a_repeating_footer_is_found(tmp_path):
    doc = make_pdf(tmp_path)
    shapes = document.furniture(doc)
    assert any("downloaded from" in s for s in shapes)
    assert any("journal of testing" in s for s in shapes)


def test_the_footer_is_skipped_and_the_body_is_read(tmp_path):
    rows = document.plan(make_pdf(tmp_path))
    read = " ".join(r.text for r in rows if r.action == document.READ)
    assert "body of page 1" in read
    assert "Downloaded" not in read, "the footer was read aloud"
    assert "Journal of Testing" not in read, "the header was read aloud"


def test_a_line_that_appears_once_is_not_treated_as_furniture(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    for n in range(3):
        page = doc.new_page()
        page.insert_text((72, 300), f"Body text on page {n}.", fontsize=11)
    page = doc[-1]
    page.insert_text((72, page.rect.height - 40),
                     "A closing remark that appears only once.", fontsize=11)
    path = tmp_path / "once.pdf"
    doc.save(path)
    shapes = document.furniture(document.open_document(str(path)))
    assert not any("closing remark" in s for s in shapes)


# --- the window fits the screen it opens on -------------------------------
def test_the_window_opens_inside_the_usable_screen():
    """A frameless window is not fitted by Windows, so it fits itself.

    It asked for 980 pixels of height on a screen with 816 usable, which put
    the transport controls under the taskbar.
    """
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from livetranslator.ui.reader_window import ReaderWindow

    window = ReaderWindow(voice_factory=lambda: None)
    window.show()
    app.processEvents()
    usable = QGuiApplication.primaryScreen().availableGeometry()
    frame = window.frameGeometry()
    assert frame.height() <= usable.height()
    assert frame.width() <= usable.width()
    assert usable.contains(frame), "the window hangs off the screen"
    window.close()


# --- how much of a page you see -------------------------------------------
def open_window(tmp_path, pages=3):
    """A reader window with a small generated paper open in it."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from livetranslator.ui.reader_window import ReaderWindow

    make_pdf(tmp_path, pages=pages)          # writes paper.pdf
    path = str(tmp_path / "paper.pdf")
    window = ReaderWindow(voice_factory=lambda: None)
    window.show()
    app.processEvents()
    window.open(path)
    for _ in range(3):
        window.fit_pages()
        app.processEvents()
    return app, window


def test_the_page_fills_the_width_by_default(tmp_path):
    """A paper opens ready to read, not as a thumbnail.

    Fitting a whole page on a 816-pixel screen rendered it 387 wide, which
    is legible but nothing anyone would choose to read.
    """
    app, window = open_window(tmp_path)
    from livetranslator.ui import reader_window as rw

    page = window.doc[0].rect
    view = window.scroll.viewport()
    assert window.fit_mode == "width"
    shown = page.width * window.view.scale
    assert shown > view.width() * 0.75 or window.view.scale >= rw.READABLE, \
        "the page should fill the width, or have stopped at readable size"
    assert window.view.width() <= view.width() + 1, \
        "filling the width must not cause sideways scrolling"
    window.close()


def test_the_text_stops_growing_when_the_window_is_wide(tmp_path):
    """A very wide window centres the page instead of magnifying it."""
    app, window = open_window(tmp_path)
    from livetranslator.ui import reader_window as rw

    window.resize(2200, window.height())
    app.processEvents()
    for _ in range(3):
        window.fit_pages()
        app.processEvents()
    assert window.view.scale <= rw.READABLE + 0.01
    assert window.paper.maximumWidth() < window.centralWidget().width(), \
        "with width to spare the page card should be centred, not stretched"
    window.close()


def test_the_button_switches_to_a_whole_page(tmp_path):
    app, window = open_window(tmp_path)
    window.switch_fit()
    app.processEvents()
    for _ in range(3):
        window.fit_pages()
        app.processEvents()
    page = window.doc[0].rect
    assert window.fit_mode == "page"
    assert page.height * window.view.scale <= window.scroll.viewport().height() + 1, \
        "the page is taller than the view, so it cannot be seen whole"
    assert window.view.width() <= window.scroll.viewport().width() + 1
    window.close()
