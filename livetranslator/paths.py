"""Where the app keeps what it writes: the log, settings and transcripts.

Everything sits in the project folder, beside the code, while the app is
run from source. This is the one place to change that.
"""

import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT, "translator.log")
SETTINGS_PATH = os.path.join(ROOT, "settings.json")
SESSION_DIR = os.path.join(ROOT, "transcripts")
# Translations a person has approved, and the sentences still waiting
# for one. The seed ships with the app; the other two are written here.
MEMORY_SEED = os.path.join(ROOT, "data", "translation-memory.json")
MEMORY_PATH = os.path.join(ROOT, "translation-memory.json")
CORRECTIONS_DIR = os.path.join(ROOT, "to-correct")

# Models are too big to ship inside an installer, so they are fetched on
# first run into the user's own app-data folder rather than beside the code:
# a program installed for everyone cannot write to its own directory.
APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "LiveTranslator")
TTS_DIR = os.path.join(APP_DIR, "tts-models")
PACK_DIR = os.path.join(APP_DIR, "translate-packs")
