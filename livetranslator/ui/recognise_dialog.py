"""Reading a paper that has no text in it, when the user asks for it.

Recognition takes minutes and installs a hundred and forty megabytes, so it
is never done behind someone's back. The reader says what is wrong with the
file and offers this; nothing happens until it is asked for.
"""
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from ..log import log
from ..reader import ocr

PACKAGE = "rapidocr-onnxruntime"

from .branding import style_dialog

class Installer(QObject):
    """Fetches the recogniser with the same pip the app runs on."""

    line = Signal(str)
    done = Signal(bool, str)

    def run(self):
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "install",
                 "--no-warn-script-location", PACKAGE],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.done.emit(False, f"{type(e).__name__}: {e}")
            return
        for line in process.stdout:
            text = line.strip()
            if text:
                self.line.emit(text[:90])
        process.wait()
        if process.returncode != 0:
            self.done.emit(False, "pip could not install the recogniser")
            return
        self.done.emit(True, "")


class Reader(QObject):
    """Runs recognition off the UI thread."""

    step = Signal(str)
    progress = Signal(int, int)
    done = Signal(str, str)        # path to the readable copy, or a problem

    def __init__(self, pdf_path):
        super().__init__()
        self.pdf_path = pdf_path
        self.stop = threading.Event()

    def run(self):
        try:
            out = ocr.recognise(
                self.pdf_path, on_step=self.step.emit,
                on_progress=lambda _n, at, total: self.progress.emit(at, total),
                stop=self.stop)
        except ocr.Stopped:
            self.done.emit("", "stopped")
            return
        except Exception as e:
            log(f"ocr: failed ({type(e).__name__}: {e})")
            self.done.emit("", f"{type(e).__name__}: {e}")
            return
        self.done.emit(out, "")


class RecogniseDialog(QDialog):
    """Explains the cost, installs if needed, then reads the pages."""

    def __init__(self, pdf_path, pages, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.result_path = ""
        self._thread = None
        self._worker = None
        self.setWindowTitle("Read this paper anyway")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(11)
        title = QLabel("Recognise the text on these pages")
        title.setStyleSheet("font-size: 13pt; font-weight: 600;")
        layout.addWidget(title)

        minutes = max(1, round(pages * 12 / 60))
        self.what = QLabel(
            f"This paper has no text in it, so the words have to be read off "
            f"the pages first. {pages} pages takes roughly {minutes} minutes, "
            f"once — the result is kept, and opening this paper again is "
            f"immediate.")
        self.what.setWordWrap(True)
        layout.addWidget(self.what)

        self.extra = QLabel(
            "The recogniser is a separate 140 MB download, because most "
            "papers do not need it.")
        self.extra.setWordWrap(True)
        self.extra.setStyleSheet("color: #C8862F;")
        self.extra.setVisible(not ocr.available())
        layout.addWidget(self.extra)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.hide()
        layout.addWidget(self.bar)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color: #6E8CAB;")
        layout.addWidget(self.detail)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.later = QPushButton("Not now")
        self.later.clicked.connect(self.reject)
        self.go = QPushButton("Read the pages")
        self.go.setDefault(True)
        self.go.clicked.connect(self.begin)
        buttons.addWidget(self.later)
        buttons.addWidget(self.go)
        layout.addLayout(buttons)
        style_dialog(self)

    # -- installing, if it is not here yet ---------------------------------
    def begin(self):
        self.go.setEnabled(False)
        self.bar.show()
        if ocr.available():
            self.recognise()
            return
        self.bar.setRange(0, 0)          # no total to count towards
        self.detail.setText("Downloading the recogniser...")
        self._thread = QThread(self)
        self._worker = Installer()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.line.connect(self.detail.setText)
        self._worker.done.connect(self.installed)
        self._thread.start()

    def installed(self, ok, problem):
        self.finish_thread()
        if not ok:
            self.fail(problem)
            return
        self.extra.hide()
        self.recognise()

    # -- the recognition itself --------------------------------------------
    def recognise(self):
        self.bar.setRange(0, 100)
        self.detail.setText("Starting...")
        self._thread = QThread(self)
        self._worker = Reader(self.pdf_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.step.connect(self.detail.setText)
        self._worker.progress.connect(self.show_progress)
        self._worker.done.connect(self.read_finished)
        self.later.setText("Stop")
        self._thread.start()

    def show_progress(self, at, total):
        self.bar.setValue(int(100 * at / total) if total else 0)
        self.bar.setFormat(f"page {at} of {total} — %p%")

    def read_finished(self, path, problem):
        self.finish_thread()
        if path:
            self.result_path = path
            self.accept()
            return
        if problem == "stopped":
            QDialog.reject(self)
            return
        self.fail(problem)

    def fail(self, problem):
        self.bar.hide()
        self.what.setText("The pages could not be read. Nothing was changed, "
                          "and the paper is still there to look at.")
        self.detail.setText(problem)
        self.go.setEnabled(True)
        self.go.setText("Try again")
        self.later.setText("Close")

    def finish_thread(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None

    def reject(self):
        if self._worker is not None and hasattr(self._worker, "stop"):
            self._worker.stop.set()
        super().reject()


def offer(pdf_path, pages, parent=None):
    """Ask, and return the path of a readable copy, or "" if not made."""
    dialog = RecogniseDialog(pdf_path, pages, parent)
    dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dialog.exec()
    return dialog.result_path
