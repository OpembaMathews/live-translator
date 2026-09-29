"""Collecting a bug report without collecting the user along with it.

The log is what makes a report worth having: it holds the timings, the
device, the reasons phrases were discarded. It also holds every word the app
has transcribed and the full path of every paper opened. Measured on one
machine's log, 522 of 2,489 lines carried speech and 189 carried a personal
path -- somebody's conversations and what they have been reading.

So the log is never sent as it is. Quoted text and paths are taken out and
replaced by a note of what was there, which keeps a line diagnosable without
keeping what it said:

    heard [en] +0.80s: <14 words removed>
    reader: opened <a file> (3241 words)

The report is also shown in full in the dialog before anything is sent.
Saying "diagnostics will be included" asks for trust; showing the text does
not need it.
"""
import os
import platform
import re
import sys
import time
import urllib.parse

from . import __version__
from .log import log
from .paths import APP_DIR, LOG_PATH

# Where feedback is sent. This ships in plain text inside the app, so it is
# readable by anyone who installs it; the +livetranslator alias lets Gmail
# file the reports on arrival and shows where the address leaked from if it
# ever starts attracting spam.
FEEDBACK_TO = "mathews.opemba+livetranslator@gmail.com"
LOG_LINES = 120

QUOTED = re.compile(r"""(['"])(.{4,}?)\1""", re.S)
# A path is matched up to its extension first. Stopping at whitespace left
# "Beyond Peer Review.pdf" behind -- the title of the paper, which is the
# part worth protecting.
NAMED_FILE = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\r\n|]*?\.[A-Za-z0-9]{1,5}\b")
WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/]\S*")
UNIX_PATH = re.compile(r"(?:/[^\s/'\"]+){2,}")
HOME = re.compile(r"(?i)users[\\/][^\\/\s]+")


def redact(line):
    """One log line with what it said taken out, and what it did left in."""
    def words(match):
        count = len(match.group(2).split())
        return f"<{count} word{'s' if count != 1 else ''} removed>"

    line = NAMED_FILE.sub("<a file>", line)
    line = WINDOWS_PATH.sub("<a file>", line)
    line = UNIX_PATH.sub("<a file>", line)
    line = HOME.sub("users/<name>", line)
    return QUOTED.sub(words, line)


def recent_log(lines=LOG_LINES):
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as fh:
            tail = fh.read().splitlines()[-lines:]
    except OSError as e:
        return [f"(the log could not be read: {type(e).__name__})"]
    return [redact(line) for line in tail]


def about(app=None):
    """The facts that explain a report, none of which identify anyone."""
    facts = [
        ("Version", __version__),
        ("System", f"{platform.system()} {platform.release()}"),
        ("Machine", platform.machine()),
        ("Python", sys.version.split()[0]),
    ]
    if app is not None:
        for name, value in (
                ("Input", getattr(app, "input_mode", "?")),
                ("Target", getattr(app, "target_mode", "?")),
                ("Capture", getattr(app, "capture_mode", "?")),
                ("Response", getattr(app, "response", "?")),
                ("Speech", getattr(app, "whisper_choice", "?")),
                ("Device", getattr(app, "device_label", "?")),
                ("AI translation", getattr(app, "ai_provider", "off")),
        ):
            facts.append((name, str(value)))
    try:
        from . import models

        voices, packs, _size = models.missing()
        facts.append(("Models missing",
                      f"{len(voices)} voice files, {len(packs)} packs"))
    except Exception:
        pass
    return facts


def report(app=None, note=""):
    """The whole thing, as it will be sent, ready to be read first."""
    out = ["Live Translator feedback", f"Written {time.strftime('%Y-%m-%d %H:%M')}", ""]
    if note.strip():
        out += ["What happened", "-------------", note.strip(), ""]
    out += ["About this machine", "------------------"]
    out += [f"{name}: {value}" for name, value in about(app)]
    out += ["", "Recent log, with speech and file paths removed",
            "---------------------------------------------"]
    out += recent_log()
    return "\n".join(out)


def save(text):
    """Write the report where the user can find and attach it."""
    folder = os.path.join(APP_DIR, "feedback")
    path = os.path.join(folder, f"feedback {time.strftime('%Y-%m-%d %H-%M')}.txt")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as e:
        log(f"feedback: could not write {path}: {type(e).__name__}: {e}")
        return None
    log(f"feedback: report saved to {path}")
    return path


def mail_link(note="", to=None, path=None):
    """A mailto: the mail client will open with the message already written.

    The report goes as a file rather than in the body: mail clients cut a
    long mailto body off without saying so, and a truncated log is worse
    than none.
    """
    to = FEEDBACK_TO if to is None else to
    subject = f"Live Translator {__version__} feedback ({platform.system()})"
    body = [note.strip() or "(what happened)", ""]
    if path:
        body += ["The report is saved at:", path,
                 "", "Please attach it to this message."]
    query = urllib.parse.urlencode(
        {"subject": subject, "body": "\n".join(body)}, quote_via=urllib.parse.quote)
    return f"mailto:{to}?{query}"
