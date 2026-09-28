"""The first-run download, with a window rather than a silent wait.

The models are fetched on first run, not shipped in the installer. That is
several hundred megabytes, so it cannot happen behind a frozen window with
no explanation: this shows what is being downloaded and how far it has got,
and it can be closed and done later.

Nothing here decides *what* to fetch. models.py owns that; this is only the
face it wears when a person is watching.
"""
from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from . import models
from .log import log


class Fetcher(QObject):
    """Runs the download on a thread of its own and reports back."""

    step = Signal(str)
    progress = Signal(str, int, int)
    done = Signal(bool, str)

    def run(self):
        try:
            ok = models.install(
                on_progress=lambda name, got, total:
                    self.progress.emit(name, got, total),
                on_step=self.step.emit)
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
        self._thread = None

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
            f"{size / 1e6:.0f} MB. It is kept, so this happens once.")
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

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.later = QPushButton("Not now")
        self.later.clicked.connect(self.reject)
        self.start = QPushButton("Download")
        self.start.setDefault(True)
        self.start.clicked.connect(self.begin)
        buttons.addWidget(self.later)
        buttons.addWidget(self.start)
        layout.addLayout(buttons)

    def begin(self):
        self.start.setEnabled(False)
        self.later.setText("Cancel")
        self._thread = QThread(self)
        self._worker = Fetcher()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.step.connect(self.show_step)
        self._worker.progress.connect(self.show_progress)
        self._worker.done.connect(self.finished)
        self._thread.start()

    def show_step(self, text):
        self.detail.setText(text)

    def show_progress(self, name, got, total):
        self.bar.setValue(int(100 * got / total) if total else 0)
        self.bar.setFormat(f"{name}  %p%")

    def finished(self, ok, problem):
        self.ok = ok
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        if ok:
            self.accept()
            return
        # A failure is kept on screen: closing the window on an error is how
        # a user ends up with an app that silently does not work.
        self.bar.setValue(0)
        self.what.setText(
            "The download did not finish. What arrived is kept, so trying "
            "again carries on from there.")
        self.detail.setText(problem or "Some pieces are still missing.")
        self.start.setEnabled(True)
        self.start.setText("Try again")
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
