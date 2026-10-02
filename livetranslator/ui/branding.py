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

# Every dialog opened from a window carries that window's stylesheet, which
# set a pale text colour for a dark panel. On a dialog, which keeps the
# system's own light background, that was pale text on pale grey and could
# not be read at all. Each dialog states its own colours instead of
# inheriting half of someone else's.
DIALOG_QSS = """
QDialog { background-color: #0E1A28; }
QDialog QLabel { color: #DCE8F4; font-size: 10pt; }
QDialog QPlainTextEdit, QDialog QLineEdit {
    background-color: #12212F; color: #F2F7FC;
    border: 1px solid #28405A; border-radius: 8px; padding: 7px;
    selection-background-color: #22405E;
}
QDialog QProgressBar {
    background-color: #12212F; border: 1px solid #28405A;
    border-radius: 8px; height: 20px; text-align: center; color: #DCE8F4;
}
QDialog QProgressBar::chunk { background-color: #1B77C4; border-radius: 7px; }
QDialog QCheckBox { color: #DCE8F4; }
QDialog QPushButton {
    background-color: #16293D; color: #DCE8F4; border: 1px solid #28405A;
    border-radius: 9px; padding: 8px 18px; font-size: 10pt;
}
QDialog QPushButton:hover { background-color: #22405E; }
QDialog QPushButton:default {
    background-color: #1B77C4; color: #FFFFFF; border: none; font-weight: 600;
}
QDialog QPushButton:default:hover { background-color: #2A8BDC; }
QDialog QPushButton:disabled { color: #4A6176; background-color: #101D2B; }
"""


def style_dialog(dialog):
    """Give a dialog the app's own colours, whatever opened it."""
    dialog.setStyleSheet(DIALOG_QSS)
    return dialog


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
