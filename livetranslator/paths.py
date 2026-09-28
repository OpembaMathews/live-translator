"""Where the app keeps what it writes.

Everything a person creates -- the log, the settings, the transcripts, the
translations they have corrected -- belongs to them, not to the copy of the
program that happened to write it. So it all lives in the folder each system
keeps application data in, never beside the code.

That is not tidiness. Uninstalling removes the install folder, and anything
written there goes with it: a term's transcripts, deleted without a word.
Updating replaces the code in place, and a settings file sitting among it is
one slip away from being replaced too.

Only the shipped seed for the translation memory lives with the code, because
it is part of the app rather than part of anyone's work.
"""

import os
import shutil
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_dir():
    """The folder each system expects an application to keep its data in."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "LiveTranslator")
    if sys.platform == "darwin":
        return os.path.expanduser(
            "~/Library/Application Support/LiveTranslator")
    base = (os.environ.get("XDG_DATA_HOME")
            or os.path.expanduser("~/.local/share"))
    return os.path.join(base, "LiveTranslator")


APP_DIR = _app_dir()

LOG_PATH = os.path.join(APP_DIR, "translator.log")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
SESSION_DIR = os.path.join(APP_DIR, "transcripts")
MEMORY_PATH = os.path.join(APP_DIR, "translation-memory.json")
CORRECTIONS_DIR = os.path.join(APP_DIR, "to-correct")
TTS_DIR = os.path.join(APP_DIR, "tts-models")
PACK_DIR = os.path.join(APP_DIR, "translate-packs")

# The one thing that stays with the code: translations that ship with the
# app rather than ones this machine has collected.
MEMORY_SEED = os.path.join(ROOT, "data", "translation-memory.json")

# Made here, once, because everything below assumes it exists: log() opens
# its file for append and swallows OSError, so a missing folder would mean
# no log at all on a machine that has never run the app.
try:
    os.makedirs(APP_DIR, exist_ok=True)
except OSError:
    pass

# What used to be written beside the code, and where it goes now.
MOVED = (
    ("translator.log", LOG_PATH),
    ("settings.json", SETTINGS_PATH),
    ("transcripts", SESSION_DIR),
    ("translation-memory.json", MEMORY_PATH),
    ("to-correct", CORRECTIONS_DIR),
)


def migrate(root=None, report=None):
    """Move anything left beside the code into the app-data folder.

    Run once at startup. Somebody who has been using the app already has
    transcripts and settings in the old place, and an upgrade that silently
    stopped finding them would look like the app had lost them.
    """
    root = root or ROOT
    moved = []
    for name, new in MOVED:
        old = os.path.join(root, name)
        if not os.path.exists(old) or os.path.exists(new):
            continue
        try:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            shutil.move(old, new)
            moved.append(name)
        except OSError as e:
            if report:
                report(f"paths: could not move {name}: {type(e).__name__}: {e}")
    if moved and report:
        report(f"paths: moved {', '.join(moved)} into {APP_DIR}")
    return moved
