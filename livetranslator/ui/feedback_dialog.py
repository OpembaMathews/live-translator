"""Reporting a problem, with the report on screen before it goes anywhere.

The report is shown in full, scrollable, in the dialog itself. A line saying
"diagnostics will be included" asks to be trusted; showing the text does not
need to be.
"""
import os
import subprocess
import sys
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from .. import feedback
from ..log import log


def reveal(path):
    """Open the folder holding the report, with the file picked out."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception as e:
        log(f"feedback: could not open the folder: {type(e).__name__}: {e}")

from .branding import style_dialog

class FeedbackDialog(QDialog):
    """What happened, what will be sent, and a way to send it."""

    def __init__(self, app=None, parent=None):
        super().__init__(parent)
        self.app = app
        self.saved_to = None
        self.setWindowTitle("Send feedback")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel("What went wrong, or what would you change?")
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        layout.addWidget(title)

        self.note = QPlainTextEdit()
        self.note.setPlaceholderText(
            "What you did, what you expected, and what happened instead.")
        self.note.setMinimumHeight(110)
        layout.addWidget(self.note)

        self.attach = QCheckBox(
            "Include a report about this machine (recommended)")
        self.attach.setChecked(True)
        self.attach.toggled.connect(self.refresh)
        layout.addWidget(self.attach)

        self.explain = QLabel(
            "Nothing you have said or read is included. Transcribed words "
            "and file names are taken out of the log before it is shown "
            "here — this is exactly what would be sent:")
        self.explain.setWordWrap(True)
        self.explain.setStyleSheet("color: #6E8CAB;")
        layout.addWidget(self.explain)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(190)
        self.preview.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(self.preview, 1)

        buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy to clipboard")
        self.copy_button.clicked.connect(self.copy)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        self.send = QPushButton("Save and open email")
        self.send.setDefault(True)
        self.send.clicked.connect(self.send_it)
        buttons.addWidget(close)
        buttons.addWidget(self.send)
        layout.addLayout(buttons)
        style_dialog(self)

        self.note.textChanged.connect(self.refresh)
        self.refresh()

    def text(self):
        note = self.note.toPlainText()
        if not self.attach.isChecked():
            return note.strip() or "(nothing written)"
        return feedback.report(self.app, note)

    def refresh(self):
        self.preview.setPlainText(self.text())
        showing = self.attach.isChecked()
        self.explain.setVisible(showing)
        self.preview.setVisible(showing)

    def copy(self):
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.text())
        self.copy_button.setText("Copied")

    def send_it(self):
        """Save the report, then open the mail client with it named.

        A mailto cannot carry an attachment and mail clients silently cut a
        long body, so the report goes to a file and the message says where
        it is. The folder opens too, so attaching it is a drag away.
        """
        path = feedback.save(self.text())
        self.saved_to = path
        link = feedback.mail_link(self.note.toPlainText(), path=path)
        try:
            webbrowser.open(link)
        except Exception as e:
            log(f"feedback: no mail client: {type(e).__name__}: {e}")
        if path:
            reveal(path)
        self.accept()


def ask(app=None, parent=None):
    dialog = FeedbackDialog(app, parent)
    dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dialog.exec()
    return dialog.saved_to
