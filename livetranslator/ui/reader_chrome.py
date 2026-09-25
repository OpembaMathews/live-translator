"""The reader's frame: header, file card, page card and transport bar.

Styling only. Every control here drives something the reader already does,
and nothing was added because the reference design showed it: there is no
language menu, no volume slider and no page arrows, because the reader has
no translation, no volume control and no page navigation yet.
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QWidget

PANEL_BG = QColor("#0E1A28")
PANEL_EDGE = QColor("#1E2C3C")
CORNER = 18

QSS = """
QWidget#panel { background: transparent; }
QLabel { color: #DCE8F4; font-size: 10pt; }
QLabel#title { color: #FAFCFF; font-size: 14pt; font-weight: 600; }
QLabel#subtitle, QLabel#meta, QLabel#left, QLabel#right { color: #6E8CAB; font-size: 9pt; }
QLabel#filename { color: #FAFCFF; font-size: 11pt; font-weight: 600; }
QLabel#pill {
    color: #DCE8F4; background-color: #16293D; border-radius: 13px;
    padding: 6px 16px; font-size: 9.5pt;
}
QLabel#pdfbadge {
    color: #FFFFFF; background-color: #C8362F; border-radius: 9px;
    padding: 10px 8px; font-size: 9pt; font-weight: 700;
}
QFrame#card {
    background-color: #12212F; border: 1px solid #1E2C3C; border-radius: 14px;
}
QFrame#paper { background-color: #F7F9FC; border-radius: 14px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px; }
QScrollBar::handle:vertical { background: #2A3E52; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #3A536D; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QPushButton {
    background-color: #16293D; color: #DCE8F4; border: none;
    border-radius: 9px; padding: 8px 16px; font-size: 10pt;
}
QPushButton:hover { background-color: #22405E; }
QPushButton:disabled { color: #40566E; background-color: #101D2B; }
QPushButton#icon {
    background: transparent; color: #8FA9C2; padding: 4px 10px; font-size: 13pt;
}
QPushButton#icon:hover { color: #FAFCFF; background-color: #1B2E42; }
QPushButton#step {
    background: transparent; color: #DCE8F4; font-size: 15pt; padding: 6px 12px;
}
QPushButton#step:hover { color: #FFFFFF; background-color: #1B2E42; }
QPushButton#step:disabled { color: #40566E; background: transparent; }
QPushButton#play {
    background-color: #1B77C4; color: #FFFFFF; font-size: 15pt; font-weight: 600;
    border-radius: 25px; min-width: 50px; min-height: 50px; padding: 0;
}
QPushButton#play:hover { background-color: #2A8BDC; }
QPushButton#play:disabled { background-color: #17324B; color: #40566E; }

QComboBox {
    background-color: #16293D; color: #DCE8F4; border: none;
    border-radius: 15px; padding: 7px 14px; font-size: 10pt;
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background-color: #142130; color: #DCE8F4; border: 1px solid #2A3E52;
    selection-background-color: #22405E; outline: none;
}

QSlider::groove:horizontal { background: #1B2E42; height: 5px; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #1B77C4; height: 5px; border-radius: 3px; }
QSlider::handle:horizontal {
    background: #FFFFFF; width: 13px; height: 13px; margin: -4px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:disabled { background: #40566E; }
"""


class Panel(QWidget):
    """The window's own rounded background, since it has no title bar."""

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        rect = self.rect().adjusted(0, 0, -1, -1)
        path.addRoundedRect(float(rect.x()), float(rect.y()),
                            float(rect.width()), float(rect.height()),
                            CORNER, CORNER)
        p.fillPath(path, PANEL_BG)
        p.setPen(PANEL_EDGE)
        p.drawPath(path)
        p.end()


def card(parent=None):
    frame = QFrame(parent)
    frame.setObjectName("card")
    return frame


def app_icon():
    """The app's own icon, for the header badge."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    path = os.path.join(here, "assets", "translator.ico")
    return QIcon(path) if os.path.exists(path) else QIcon()


def readable_size(path):
    try:
        mb = os.path.getsize(path) / 1e6
    except OSError:
        return ""
    return f"{mb:.1f} MB" if mb >= 1 else f"{mb * 1000:.0f} KB"


class Draggable:
    """Mixin: drag a frameless window by its header strip."""

    header_height = 74

    def _drag_press(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and event.position().y() < self.header_height):
            self._drag_from = (event.globalPosition()
                               - self.frameGeometry().topLeft())
            return True
        return False

    def _drag_move(self, event):
        if getattr(self, "_drag_from", None) is not None:
            self.move((event.globalPosition() - self._drag_from).toPoint())
            return True
        return False

    def _drag_release(self):
        self._drag_from = None
