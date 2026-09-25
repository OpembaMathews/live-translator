"""The reader's frame: header, file card, page card and transport bar.

Styling only. Every control here drives something the reader already does,
and nothing was added because the reference design showed it: there is no
language menu, no volume slider and no page arrows, because the reader has
no translation, no volume control and no page navigation yet.
"""
import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QComboBox, QFrame, QPushButton, QWidget

PANEL_BG = QColor("#0E1A28")
PANEL_EDGE = QColor("#1E2C3C")
CORNER = 18

QSS = """
QWidget#panel { background: transparent; }
QLabel { color: #DCE8F4; font-size: 10pt; }
QLabel#title { color: #FAFCFF; font-size: 12.5pt; font-weight: 600; }
QLabel#subtitle, QLabel#meta, QLabel#left, QLabel#right { color: #6E8CAB; font-size: 9pt; }
QLabel#filename { color: #FAFCFF; font-size: 11pt; font-weight: 600; }
QLabel#pill {
    color: #DCE8F4; background-color: #16293D; border-radius: 13px;
    padding: 6px 16px; font-size: 9.5pt;
}
QLabel#pdfbadge {
    color: #FFFFFF; background-color: #C8362F; border-radius: 8px;
    padding: 7px 8px; font-size: 8.5pt; font-weight: 700;
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

QComboBox#lang {
    background-color: #16293D; color: #DCE8F4; border: 1px solid #24405C;
    border-radius: 16px; padding: 6px 26px 6px 12px; font-size: 10pt;
}
QComboBox#lang:hover { background-color: #1D3450; }

QComboBox {
    background-color: #16293D; color: #DCE8F4; border: none;
    border-radius: 15px; padding: 7px 26px 7px 14px; font-size: 10pt;
}
QComboBox::drop-down { border: none; width: 0; }
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


RING = QColor("#3E9BE0")
GLYPH = QColor("#E8F2FB")
DIMMED = QColor("#41576E")


class TransportButton(QPushButton):
    """Back, play or pause, forward -- drawn rather than typed.

    The Unicode transport characters fall back to whichever font on the
    machine happens to carry them, which here drew a boxy emoji pause at a
    size that ignored the button. Painting the three keeps them the same
    weight, the same colour and crisp at any scale.
    """

    def __init__(self, kind, primary=False, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.primary = primary
        self.setFlat(True)
        self.setFixedSize(*((46, 46) if primary else (36, 36)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def set_kind(self, kind):
        self.kind = kind
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        box = QRectF(self.rect())
        live = self.isEnabled()
        colour = (RING if self.primary else GLYPH) if live else DIMMED
        if self.primary:
            p.setPen(QPen(colour, 2.0))
            p.setBrush(QColor(62, 155, 224, 40) if self._hover and live
                       else Qt.BrushStyle.NoBrush)
            p.drawEllipse(box.adjusted(2, 2, -2, -2))
        elif self._hover and live:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(62, 155, 224, 26))
            p.drawEllipse(box)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        self._glyph(p, box)
        p.end()

    def _glyph(self, p, box):
        unit = box.width() * (0.26 if self.primary else 0.30)
        cx, cy = box.center().x(), box.center().y()
        if self.kind == "pause":
            bar, gap = unit * 0.36, unit * 0.32
            for side in (-1, 1):
                p.drawRoundedRect(
                    QRectF(cx + side * gap - bar / 2, cy - unit, bar, unit * 2),
                    bar / 2, bar / 2)
            return
        if self.kind == "play":
            # nudged right, because a triangle looks off-centre in a circle
            p.drawPath(self._triangle(cx + unit * 0.12, cy, unit, 1,
                                      tip=0.86, base=0.62))
            return
        way = -1 if self.kind == "prev" else 1
        p.drawPath(self._triangle(cx, cy, unit, way, tip=0.55, base=0.95))
        bar = unit * 0.24
        edge = cx + way * unit * 0.94
        p.drawRoundedRect(
            QRectF(edge - bar if way > 0 else edge, cy - unit * 0.85,
                   bar, unit * 1.7), bar / 2, bar / 2)

    @staticmethod
    def _triangle(cx, cy, unit, way, tip, base):
        path = QPainterPath()
        path.moveTo(cx - way * unit * base, cy - unit * 0.85)
        path.lineTo(cx + way * unit * tip, cy)
        path.lineTo(cx - way * unit * base, cy + unit * 0.85)
        path.closeSubpath()
        return path


class Chooser(QComboBox):
    """A dropdown that draws its own chevron.

    Qt paints the native arrow in the platform style, which on this dark
    panel came out all but invisible, so the pills read as plain labels
    rather than as something to click.
    """

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor("#89A7C4"), 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        x, y = self.width() - 15.0, self.height() / 2.0 - 1.0
        arrow = QPainterPath()
        arrow.moveTo(x - 4.0, y - 2.0)
        arrow.lineTo(x, y + 2.5)
        arrow.lineTo(x + 4.0, y - 2.0)
        p.drawPath(arrow)
        p.end()


def globe_icon(size=15, colour=QColor("#9CC4E4")):
    """The globe on the language pill, drawn so no font has to carry it."""
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QPen(colour, 1.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    ball = QRectF(1.0, 1.0, size - 2.0, size - 2.0)
    p.drawEllipse(ball)
    p.drawLine(QPointF(ball.left(), ball.center().y()),
               QPointF(ball.right(), ball.center().y()))
    p.drawEllipse(QRectF(ball.center().x() - ball.width() * 0.27, ball.top(),
                         ball.width() * 0.54, ball.height()))
    p.end()
    return QIcon(pixmap)


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
