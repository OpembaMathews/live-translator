"""The first-run download, with a window rather than a silent wait.

The models are fetched on first run, not shipped in the installer. That is
several hundred megabytes, so it cannot happen behind a frozen window with
no explanation, and on a slow connection it cannot demand to be watched
either. So it says how fast it is going, and offers the two things a person
actually wants: stop and finish later, or get on with something else and be
told when it is done.

Nothing here decides *what* to fetch. models.py owns that; this is only the
face it wears when a person is watching.
"""
import threading

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout,
)

from . import models
from .log import log

# A download left running in the background outlives its dialog, so
# something has to hold the thread. It is released on the thread's finished
# signal, never from inside a slot the thread is still running: dropping the
# last reference to a running QThread takes the process down with it.
_running = None


def _release():
    global _running
    _running = None


class Fetcher(QObject):
    """Runs the download on a thread of its own and reports back."""

    step = Signal(str)
    progress = Signal(str, int, int, str, bool)   # name, got, total, rate, slow
    done = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.stop = threading.Event()
        self.speed = models.Speed()

    def run(self):
        def report(name, got, total):
            self.speed.note(got)
            self.progress.emit(name, got, total,
                               self.speed.describe(got, total),
                               self.speed.slow)
        try:
            ok = models.install(on_progress=report, on_step=self.step.emit,
                                stop=self.stop)
        except models.Stopped:
            self.done.emit(False, "paused")
            return
        except Exception as e:
            log(f"first run: download failed: {type(e).__name__}: {e}")
            self.done.emit(False, f"{type(e).__name__}: {e}")
            return
        self.done.emit(ok, "")


class FirstRun(QDialog):
    """Asks once, downloads, and says plainly when it cannot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live Translator: first run")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.ok = False
        self.backgrounded = False
        self._thread = None
        self._worker = None
        self._warned_slow = False

        voices, packs, size = models.missing()
        what = []
        if voices:
            what.append("the voice")
        if packs:
            what.append(f"{len(packs)} translation packs")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        title = QLabel("One download before the first use")
        title.setStyleSheet("font-size: 13pt; font-weight: 600;")
        layout.addWidget(title)

        self.what = QLabel(
            f"Live Translator needs {' and '.join(what)}: about "
            f"{size / 1e6:.0f} MB. It is kept, so this happens once, and it "
            f"can be stopped and carried on later without losing what has "
            f"already arrived.")
        self.what.setWordWrap(True)
        layout.addWidget(self.what)

        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setRange(0, 100)
        layout.addWidget(self.bar)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color: #6E8CAB;")
        layout.addWidget(self.detail)

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #C8862F;")
        self.warning.hide()
        layout.addWidget(self.warning)

        buttons = QHBoxLayout()
        self.later = QPushButton("Not now")
        self.later.clicked.connect(self.reject)
        buttons.addWidget(self.later)
        self.background = QPushButton("Continue in the background")
        self.background.setEnabled(False)
        self.background.clicked.connect(self.go_background)
        buttons.addWidget(self.background)
        buttons.addStretch(1)
        self.start = QPushButton("Download")
        self.start.setDefault(True)
        self.start.clicked.connect(self.begin)
        buttons.addWidget(self.start)
        layout.addLayout(buttons)

    def begin(self):
        self.start.setEnabled(False)
        self.later.setText("Pause")
        self.background.setEnabled(True)
        self._thread = QThread(self)
        self._worker = Fetcher()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.step.connect(self.show_step)
        self._worker.progress.connect(self.show_progress)
        self._worker.done.connect(self.download_finished)
        self._thread.start()

    def show_step(self, text):
        self.detail.setText(text)

    def show_progress(self, name, got, total, rate, slow):
        # Across the whole download, not one file at a time: a bar that ran
        # nought to a hundred six times answered none of "how much longer".
        self.bar.setValue(int(100 * got / total) if total else 0)
        self.bar.setFormat(
            f"{got / 1e6:.0f} of {total / 1e6:.0f} MB — %p%")
        self.detail.setText(f"{name}    {rate}" if rate else name)
        if slow and not self._warned_slow:
            self._warned_slow = True
            self.warning.setText(
                "This connection is slow. Leave it running in the "
                "background, or pause and carry on later — what has "
                "arrived is kept either way.")
            self.warning.show()
        elif not slow and self._warned_slow:
            self._warned_slow = False
            self.warning.hide()

    # -- the three ways out ------------------------------------------------
    def go_background(self):
        """Leave it running and get out of the way."""
        global _running
        self.backgrounded = True
        self._thread.setParent(None)      # it has to outlive this dialog
        _running = (self._thread, self._worker)
        self._thread.finished.connect(_release)
        self._worker.done.connect(self._say_when_done)
        log("first run: download carrying on in the background")
        self.accept()

    @staticmethod
    def _say_when_done(ok, problem):
        if ok:
            log("first run: background download finished")
            QMessageBox.information(
                None, "Live Translator",
                "The voice and translation packs have finished downloading. "
                "Everything is ready.")
        elif problem != "paused":
            QMessageBox.warning(
                None, "Live Translator",
                "The download did not finish: " + problem + chr(10) + chr(10)
                + "It carries on from where it stopped next time.")

    def reject(self):
        """Not now, or Pause once it has started. Both keep what arrived."""
        if self._worker is not None and not self.backgrounded:
            self._worker.stop.set()
            log("first run: download paused by the user")
        super().reject()

    def download_finished(self, ok, problem):
        """Not called finished(): QDialog already has a finished(int) signal
        of its own, and connecting to the name silently failed."""
        self.ok = ok
        if self._thread is not None and not self.backgrounded:
            self._thread.quit()
            self._thread.wait(3000)
        if ok:
            self.accept()
            return
        if problem == "paused":
            QDialog.reject(self)
            return
        # A failure is kept on screen: closing the window on an error is how
        # a user ends up with an app that silently does not work.
        self.bar.setValue(0)
        self.what.setText(
            "The download did not finish. What arrived is kept, so trying "
            "again carries on from there.")
        self.detail.setText(problem or "Some pieces are still missing.")
        self.warning.hide()
        self.start.setEnabled(True)
        self.start.setText("Try again")
        self.background.setEnabled(False)
        self.later.setText("Close")


def ensure(parent=None):
    """Fetch what is missing, asking first. True if everything is present."""
    voices, packs, _size = models.missing()
    if not voices and not packs:
        return True
    dialog = FirstRun(parent)
    dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dialog.exec()
    return dialog.ok
