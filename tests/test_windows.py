"""Which window closing does what.

Closing the caption bar used to quit the process, so a paper being read
aloud stopped halfway through. These check the two windows are independent
and that the app still ends when both are gone.
"""
import pytest

from livetranslator.ui.app import QtTranslator


class Fake:
    """Just enough of a window and a timer to drive close() and show()."""

    def __init__(self):
        self.visible = True
        self.closed = False
        self.running = True
        self.raised = 0

    # window
    def isVisible(self):
        return self.visible

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def close(self):
        self.closed = True
        self.visible = False

    def raise_(self):
        self.raised += 1

    def activateWindow(self):
        pass

    # timer
    def start(self, _ms=None):
        self.running = True

    def stop(self):
        self.running = False


class Standin(QtTranslator):
    """A controller with the engine and Qt taken out from under it."""

    def __init__(self, reader=None):
        self.win = Fake()
        self._pump = Fake()
        self.reader = reader
        self.paused = False
        self.closing = False
        self.quit_called = 0
        self.toggled = 0
        self.session = self
        self.saved = 0

    def save(self):                      # stands in for the session
        self.saved += 1
        return "transcript.html"

    def toggle_listening(self):
        self.paused = not self.paused
        self.toggled += 1

    def _quit(self):
        self.quit_called += 1


@pytest.fixture(autouse=True)
def no_real_quit(monkeypatch):
    """close() asks Qt to quit; count it instead."""
    import livetranslator.ui.app as app

    class App:
        def __init__(self, owner):
            self.owner = owner

        def quit(self):
            self.owner.quit_called += 1

    holder = {}

    class Instance:
        @staticmethod
        def instance():
            return App(holder["owner"])

    monkeypatch.setattr(app, "QApplication", Instance)
    return holder


def test_closing_the_captions_alone_quits_the_app(no_real_quit):
    app = Standin()
    no_real_quit["owner"] = app
    app.close()
    assert app.quit_called == 1
    assert app.closing


def test_closing_the_captions_leaves_an_open_reader_running(no_real_quit):
    """The whole bug: a paper being read aloud stopped halfway through."""
    reader = Fake()
    app = Standin(reader=reader)
    no_real_quit["owner"] = app
    app.close()
    assert app.quit_called == 0, "the reader was still open"
    assert not app.closing, "the app is still alive, so callbacks must work"
    assert reader.visible, "the reader must not be closed"
    assert not app.win.visible, "the caption bar is put away"


def test_closing_the_captions_releases_the_microphone(no_real_quit):
    reader = Fake()
    app = Standin(reader=reader)
    no_real_quit["owner"] = app
    app.close()
    assert app.paused, "nothing should be recorded while the bar is away"
    assert not app._pump.running, "no need to repaint a hidden window"


def test_the_transcript_is_saved_either_way(no_real_quit):
    for reader in (None, Fake()):
        app = Standin(reader=reader)
        no_real_quit["owner"] = app
        app.close()
        assert app.saved == 1


def test_a_reader_that_was_closed_does_not_keep_the_app_alive(no_real_quit):
    reader = Fake()
    reader.hide()                        # the user closed it earlier
    app = Standin(reader=reader)
    no_real_quit["owner"] = app
    app.close()
    assert app.quit_called == 1


def test_the_captions_come_back_listening(no_real_quit):
    reader = Fake()
    app = Standin(reader=reader)
    no_real_quit["owner"] = app
    app.close()
    assert app.paused and not app.win.visible
    app.show_captions()
    assert app.win.visible and app.win.raised == 1
    assert not app.paused, "showing the bar again starts listening"
    assert app._pump.running


def test_showing_twice_does_not_stop_the_listening(no_real_quit):
    """show_captions resumes only when it is actually paused."""
    app = Standin(reader=Fake())
    no_real_quit["owner"] = app
    app.close()
    app.show_captions()
    app.show_captions()
    assert not app.paused


def test_the_reader_alone_offers_no_caption_button():
    """Run on its own there is no caption bar to show."""
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from livetranslator.ui.reader_window import ReaderWindow

    alone = ReaderWindow(voice_factory=lambda: None)
    assert alone.on_captions is None
    alone.close()


def test_the_reader_opened_from_the_bar_can_bring_it_back():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from livetranslator.ui.reader_window import ReaderWindow

    asked = []
    window = ReaderWindow(voice_factory=lambda: None,
                          on_captions=lambda: asked.append(True))
    buttons = [b for b in window.findChildren(type(window.open_button))
               if b.toolTip() == "Show the caption bar"]
    assert len(buttons) == 1, "there should be one way back to the captions"
    buttons[0].click()
    assert asked == [True]
    window.close()
