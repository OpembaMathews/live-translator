"""The app's identity to the desktop: its icon, and who Windows thinks it is.

The taskbar showed a Python icon rather than the app's own. Setting the
window icon is not enough on Windows: the taskbar groups by an
"application user model ID", and a program that never sets one is filed
under the executable that happens to be running it -- here pythonw.exe,
whose icon is Python's.

Telling Windows who we are has to happen before the first window exists,
because that is when the grouping is decided.
"""
import os
import sys

from PySide6.QtGui import QIcon

from ..log import log

# Any string will do so long as it is ours and it does not change: Windows
# uses it to group windows, to pin to the taskbar, and to keep jump lists.
APP_ID = "OpembaMathews.LiveTranslator"


def icon_path():
    here = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(here, "assets", "translator.ico")


def app_icon():
    """The app's own icon, or an empty one if the file is missing."""
    path = icon_path()
    return QIcon(path) if os.path.exists(path) else QIcon()


def claim_identity():
    """Tell Windows this is its own application, not whatever launched it."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception as e:
        # Only the icon suffers, so this must never stop the app
        log(f"branding: could not set the application id "
            f"({type(e).__name__}: {e})")
        return False
    return True


def apply_to(app):
    """Give every window the same icon, and claim the taskbar entry.

    Called before any window is built. setWindowIcon on the application is
    what windows inherit; the identity is what the taskbar reads.
    """
    claim_identity()
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    return icon
