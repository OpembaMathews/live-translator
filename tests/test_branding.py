"""The taskbar should show the app's icon, not the icon of whatever ran it."""
import os
import sys

import pytest


def an_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_icon_file_ships_with_the_app():
    from livetranslator.ui.branding import icon_path

    assert os.path.isfile(icon_path()), "no icon to show"


def test_the_icon_has_the_sizes_windows_asks_for():
    """A .ico with only one size is scaled by Windows and looks it."""
    an_app()
    from livetranslator.ui.branding import app_icon

    sizes = {s.width() for s in app_icon().availableSizes()}
    assert not app_icon().isNull()
    for wanted in (16, 32, 48, 256):
        assert wanted in sizes, f"no {wanted}px version: have {sorted(sizes)}"


def test_the_application_carries_the_icon():
    """Windows inherit it, so a window that forgets still looks right."""
    app = an_app()
    from livetranslator.ui.branding import apply_to

    apply_to(app)
    assert not app.windowIcon().isNull()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_is_told_this_is_its_own_application():
    """Without this the taskbar files the app under pythonw.exe and shows
    the Python icon, whatever the window icon says."""
    import ctypes

    from livetranslator.ui.branding import APP_ID, claim_identity

    assert claim_identity()
    got = ctypes.c_wchar_p()
    ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(
        ctypes.byref(got))
    assert got.value == APP_ID


def test_one_definition_of_the_icon():
    """The header badge and the window used to load it separately."""
    an_app()
    from livetranslator.ui import branding, reader_chrome

    assert reader_chrome.app_icon().availableSizes() == \
        branding.app_icon().availableSizes()


def test_both_windows_set_an_icon():
    """A window with no icon of its own shows a blank square in the taskbar."""
    import inspect

    from livetranslator.ui import caption, reader_window

    for module in (caption, reader_window):
        assert "setWindowIcon" in inspect.getsource(module), \
            f"{module.__name__} never sets a window icon"
