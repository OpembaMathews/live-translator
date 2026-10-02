"""Telling someone an update exists, and putting it in place if they want it.

Two rules, both about not being a nuisance. Nothing is downloaded without
being asked, and nothing is replaced while the app is running from it --
the files are swapped and the app restarts, which takes a second because an
update is a couple of hundred kilobytes of Python.
"""
import threading

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout,
)

from .. import __version__, update
from ..log import log

_looking = None          # the startup check, kept alive while it runs
_found = None            # what it found, for the menu to offer later

from .branding import style_dialog

class Looker(QObject):
    """Asks GitHub what the latest release is, off the UI thread."""

    answered = Signal(object)

    def run(self):
        self.answered.emit(update.look())


class Installer(QObject):
    """Downloads and applies, reporting as it goes."""

    progress = Signal(int, int)
    done = Signal(bool, str)

    def __init__(self, manifest):
        super().__init__()
        self.manifest = manifest
        self.stop = threading.Event()

    def run(self):
        try:
            archive = update.download(
                self.manifest,
                on_progress=lambda _n, got, total: self.progress.emit(got, total),
                stop=self.stop)
            folder = update.unpack(archive)
            update.apply(folder)
        except update.Stopped:
            self.done.emit(False, "stopped")
            return
        except Exception as e:
            log(f"update: failed ({type(e).__name__}: {e})")
            self.done.emit(False, f"{type(e).__name__}: {e}")
            return
        self.done.emit(True, "")


class UpdateDialog(QDialog):
    def __init__(self, manifest, parent=None):
        super().__init__(parent)
        self.manifest = manifest
        self.applied = False
        self._thread = None
        self._worker = None
        self.setWindowTitle("Update")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(11)

        there = manifest.get("version", "?")
        size = manifest.get("size", 0)
        title = QLabel(f"Version {there} is available")
        title.setStyleSheet("font-size: 13pt; font-weight: 600;")
        layout.addWidget(title)

        self.what = QLabel(
            f"This is version {__version__}. The update is "
            f"{size / 1e3:.0f} KB — only the app itself, so the voice, "
            f"the language packs and the speech model are not downloaded "
            f"again.")
        self.what.setWordWrap(True)
        layout.addWidget(self.what)

        notes = (manifest.get("notes") or "").strip()
        if notes:
            what_changed = QLabel(notes)
            what_changed.setWordWrap(True)
            what_changed.setStyleSheet("color: #6E8CAB;")
            layout.addWidget(what_changed)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.hide()
        layout.addWidget(self.bar)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.later = QPushButton("Not now")
        self.later.clicked.connect(self.reject)
        self.go = QPushButton("Update and restart")
        self.go.setDefault(True)
        self.go.clicked.connect(self.begin)
        buttons.addWidget(self.later)
        buttons.addWidget(self.go)
        layout.addLayout(buttons)
        style_dialog(self)

    def begin(self):
        self.go.setEnabled(False)
        self.bar.show()
        self._thread = QThread(self)
        self._worker = Installer(self.manifest)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.show_progress)
        self._worker.done.connect(self.finished)
        self._thread.start()

    def show_progress(self, got, total):
        self.bar.setValue(int(100 * got / total) if total else 0)
        self.bar.setFormat(f"{got / 1e3:.0f} of {total / 1e3:.0f} KB — %p%")

    def finished(self, ok, problem):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        if ok:
            self.applied = True
            log(f"update: applied {self.manifest.get('version')}")
            QMessageBox.information(
                self, "Update",
                "The update is in place. Live Translator will restart.")
            self.accept()
            update.relaunch()
            return
        self.bar.hide()
        self.what.setText(
            "The update did not go in. Nothing was changed, and the version "
            "you have carries on working.")
        self.what.setToolTip(problem)
        self.go.setEnabled(True)
        self.go.setText("Try again")


def offer(manifest, parent=None):
    dialog = UpdateDialog(manifest, parent)
    dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dialog.exec()
    return dialog.applied


def check_now(parent=None):
    """From the menu: look, and say either way."""
    manifest = update.look()
    if manifest is None:
        QMessageBox.information(
            parent, "Update",
            "Could not reach the update server. Check the connection and "
            "try again.")
        return False
    if not update.newer(manifest.get("version", "")):
        QMessageBox.information(
            parent, "Update",
            f"Live Translator {__version__} is the latest version.")
        return False
    return offer(manifest, parent)


def look_quietly(on_found=None):
    """At startup: ask in the background and stay silent if there is nothing.

    A check that blocks the splash, or a box that appears over a talk
    already in progress, would both be worse than not checking at all.
    """
    global _looking

    thread = QThread()
    looker = Looker()
    looker.moveToThread(thread)

    def answered(manifest):
        global _found
        if manifest and update.newer(manifest.get("version", "")):
            _found = manifest
            log(f"update: version {manifest.get('version')} is available")
            if on_found:
                on_found(manifest)
        thread.quit()

    def let_go():
        # Only once the thread has actually stopped. Releasing the last
        # reference from inside answered() dropped a QThread that was still
        # running, Python collected it, and destroying a running QThread
        # takes the process down -- the app closed a second after starting.
        global _looking
        _looking = None

    looker.answered.connect(answered)
    thread.started.connect(looker.run)
    thread.finished.connect(let_go)
    _looking = (thread, looker)
    thread.start()


def waiting():
    """What the startup check found, if anything."""
    return _found
