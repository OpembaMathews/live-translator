"""Where the app keeps what it writes: the log, settings and transcripts.

Everything sits in the project folder, beside the code, while the app is
run from source. This is the one place to change that.
"""

import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT, "translator.log")
SETTINGS_PATH = os.path.join(ROOT, "settings.json")
SESSION_DIR = os.path.join(ROOT, "transcripts")
