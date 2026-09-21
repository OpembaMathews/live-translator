"""The optional AI translation setting, as a Qt dialog.

A key is typed in, never shown back: a saved key is only acknowledged, and
leaving the field empty keeps it. The dialog hands the choice to a callback
and does nothing with the key itself.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QVBoxLayout,
)

DIALOG_QSS = """
QDialog { background-color: #101A26; }
QLabel { color: #DCE8F4; font-size: 10pt; }
QLabel#title { font-size: 13pt; font-weight: 600; color: #FAFCFF; }
QLabel#note { color: #7E99B5; font-size: 9pt; }
QRadioButton, QCheckBox { color: #DCE8F4; font-size: 10pt; spacing: 8px; }
QRadioButton:disabled, QCheckBox:disabled { color: #4A6078; }
QLineEdit {
    background-color: #182636; color: #FAFCFF; border: 1px solid #2A3E52;
    border-radius: 6px; padding: 7px 9px; font-size: 10pt;
    selection-background-color: #2E7FA8;
}
QLineEdit:focus { border-color: #38BDF8; }
QLineEdit:disabled { color: #4A6078; }
QPushButton {
    background-color: #16293D; color: #DCE8F4; border: none;
    border-radius: 6px; padding: 7px 18px; font-size: 10pt;
}
QPushButton:hover { background-color: #22405E; }
QPushButton#save { background-color: #2E7FA8; color: #FFFFFF; font-weight: 600; }
QPushButton#save:hover { background-color: #3B93C0; }
"""

PROVIDERS = (("off", "Off, use the local models"),
             ("claude", "Claude"),
             ("gemini", "Gemini"))


class AIKeyDialog(QDialog):
    def __init__(self, parent, provider, has_key, covers_speech, on_save):
        super().__init__(parent)
        self.on_save = on_save
        self.has_key = has_key
        self.setWindowTitle("AI translation")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet(DIALOG_QSS)
        self.setMinimumWidth(430)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)

        title = QLabel("Translate with your own AI key")
        title.setObjectName("title")
        lay.addWidget(title)
        note = QLabel("Optional. A Claude or Gemini key gives better translation, "
                      "especially for Kiswahili. Leave it off to keep everything "
                      "on this computer.")
        note.setObjectName("note")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.group = QButtonGroup(self)
        for code, label in PROVIDERS:
            rb = QRadioButton(label)
            rb.setProperty("code", code)
            rb.setChecked(code == provider)
            self.group.addButton(rb)
            lay.addWidget(rb)
        self.group.buttonToggled.connect(lambda *_: self._sync())

        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(
            "A key is saved. Leave empty to keep it" if has_key
            else "Paste your API key")
        lay.addSpacing(4)
        lay.addWidget(self.key)

        self.key.textChanged.connect(lambda *_: self._can_save())

        self.speech = QCheckBox("Also use Gemini for speech recognition")
        self.speech.setChecked(covers_speech)
        lay.addWidget(self.speech)

        stored = QLabel("The key is encrypted for your Windows account and "
                        "only sent in your own requests to the provider.")
        stored.setObjectName("note")
        stored.setWordWrap(True)
        lay.addWidget(stored)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.save = QPushButton("Save")
        self.save.setObjectName("save")
        self.save.setDefault(True)
        self.save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save)
        lay.addSpacing(6)
        lay.addLayout(buttons)
        self._sync()

    def provider(self):
        b = self.group.checkedButton()
        return b.property("code") if b else "off"

    def _sync(self):
        """Only offer what makes sense for the chosen provider."""
        p = self.provider()
        self.key.setEnabled(p != "off")
        self.speech.setEnabled(p == "gemini")
        if p != "gemini":
            self.speech.setChecked(False)
        self._can_save()

    def _can_save(self):
        """A provider needs a key: a new one, or the one already saved."""
        self.save.setEnabled(self.provider() == "off" or self.has_key
                             or bool(self.key.text().strip()))

    def _save(self):
        p = self.provider()
        self.on_save(p, self.key.text().strip(), self.speech.isChecked())
        self.accept()
