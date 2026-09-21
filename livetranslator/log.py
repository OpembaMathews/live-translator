"""The app's log file, the first place to look when captions stop.
"""

import sys
import threading
import time
from .paths import LOG_PATH


LOG_LOCK = threading.Lock()


# Windows consoles default to cp1252, which cannot represent Chinese. Any
# log line carrying a translation would raise UnicodeEncodeError and take the
# process down, so make the streams tolerant before anything writes to them.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass  # pythonw has no streams to reconfigure


def log(message):
    stamp = time.strftime("%H:%M:%S")
    line = f"{stamp}  {message}"
    try:
        print(line)
    except (OSError, ValueError, UnicodeEncodeError):
        pass  # no console, or one that cannot render the text
    try:
        with LOG_LOCK, open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # logging must never take the app down
