"""A report has to be worth reading without giving the reporter away.

The log holds what makes a report useful and also everything the app has
transcribed. Measured on one machine: of 2,489 lines, 522 carried speech
and 189 carried a personal path.
"""
import re

from livetranslator import feedback

SPOKEN = ("20:48:06    heard [en] +0.80s: "
          "'divine revelation and we believe that institution'")
TRANSLATED = ("19:03:43    translated +0.29s (total 10.31s): "
              "'the floating window is one of them'")
WINDOWS_PAPER = (r"19:03:46  reader: opened C:\Users\opemb\Downloads"
                 r"\Journal Articles\Beyond Peer Review.pdf (3241 words)")
MAC_PAPER = ("19:03:47  reader: opened /Users/mathews/Papers/"
             "Selection and Compensation in Older Adults.pdf (5982 words)")
HARMLESS = ("20:16:38  listening on Internal microphone "
            "(threshold=70, meter scale=417)")


def test_spoken_words_never_survive():
    out = feedback.redact(SPOKEN)
    assert "divine" not in out and "revelation" not in out
    assert "<7 words removed>" in out


def test_a_translation_never_survives():
    out = feedback.redact(TRANSLATED)
    assert "floating" not in out
    assert "translated +0.29s" in out, "the timing is the useful part"


def test_a_paper_title_with_spaces_is_not_left_behind():
    """Stopping a path at whitespace left the title, which is the point."""
    out = feedback.redact(WINDOWS_PAPER)
    for word in ("Beyond", "Peer", "Review", "Journal", "opemb"):
        assert word not in out, f"{word!r} leaked: {out}"
    assert "3241 words" in out, "the size says whether the PDF was readable"


def test_a_mac_path_is_treated_the_same():
    out = feedback.redact(MAC_PAPER)
    for word in ("mathews", "Selection", "Compensation", "Older"):
        assert word not in out, f"{word!r} leaked: {out}"


def test_what_the_app_did_is_kept():
    out = feedback.redact(HARMLESS)
    assert out == HARMLESS, "redaction must not eat the diagnosis"


def test_reasons_for_discarding_survive_without_the_words():
    line = "20:42:46    -> nothing usable (1): unsure -1.41 'a phrase spoken'"
    out = feedback.redact(line)
    assert "unsure -1.41" in out
    assert "phrase spoken" not in out


def test_the_whole_report_carries_no_speech(tmp_path, monkeypatch):
    fake = tmp_path / "translator.log"
    fake.write_text("\n".join([SPOKEN, TRANSLATED, WINDOWS_PAPER, HARMLESS]),
                    encoding="utf-8")
    monkeypatch.setattr(feedback, "LOG_PATH", str(fake))
    text = feedback.report(note="It stopped captioning.")
    assert "It stopped captioning." in text
    for leak in ("divine", "floating", "Beyond Peer Review", "opemb"):
        assert leak not in text, f"{leak!r} reached the report"


def test_the_report_says_enough_to_act_on():
    text = feedback.report(note="")
    for wanted in ("Version", "System", "Python"):
        assert wanted in text


def test_settings_are_included_when_there_is_an_app():
    class App:
        input_mode = "zt"
        target_mode = "en"
        capture_mode = "Phrase"
        response = "Accurate"
        whisper_choice = "Accurate (base)"
        device_label = "System audio (Headphones)"
        ai_provider = "off"

    text = feedback.report(App(), note="")
    assert "Response: Accurate" in text
    assert "Input: zt" in text


def test_a_missing_log_does_not_stop_a_report(monkeypatch):
    monkeypatch.setattr(feedback, "LOG_PATH", "nowhere/at/all.log")
    text = feedback.report(note="first run, nothing works")
    assert "could not be read" in text
    assert "first run, nothing works" in text


def test_there_is_somewhere_to_send_it():
    """An empty address means the report goes nowhere and nobody notices."""
    assert "@" in feedback.FEEDBACK_TO


def test_the_mail_link_carries_the_note_and_the_path():
    link = feedback.mail_link("captions stopped", path=r"C:\reports\a.txt")
    assert link.startswith(f"mailto:{feedback.FEEDBACK_TO}?")
    assert "captions%20stopped" in link
    assert "subject=" in link


def test_the_report_file_is_written_where_it_can_be_found(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "APP_DIR", str(tmp_path))
    path = feedback.save("a report")
    assert path and path.endswith(".txt")
    assert open(path, encoding="utf-8").read() == "a report"
    assert "feedback" in path


def test_every_quoted_thing_in_a_real_log_is_removed():
    """A sweep over the whole log, in case a line takes a shape not thought of."""
    import os

    from livetranslator.paths import LOG_PATH

    if not os.path.exists(LOG_PATH):
        return
    with open(LOG_PATH, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()[-400:]
    for line in lines:
        out = feedback.redact(line)
        assert not re.search(r"""(['"]).{8,}?\1""", out), \
            f"quoted text survived: {out[:90]}"
