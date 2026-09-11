"""Qt rebuild of the floating caption widget, as a like-for-like comparison.

This is a UI prototype, not a port of the app. It reproduces the main window
exactly - same palette, same geometry, same behaviours - so the two can be
judged side by side. No audio, no translation: a synthetic level drives the
meter so the animation can be seen, and set_level() is where the real capture
thread would push into.

The point of the exercise is the three things the caption widget needs that a
settings panel does not: frameless with no OS chrome, always on top of a
presentation, and a translucent rounded panel that can sit over a bright
slide without a hard rectangular edge.

Run:  python qt_prototype.py
"""
import math
import sys

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QElapsedTimer
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QBrush,
    QGuiApplication,
)
from PySide6.QtWidgets import QApplication, QWidget

# ---------------------------------------------------------------------------
# Palette, copied from translation.py rather than imported: importing that
# module pulls in speech_recognition and the engines, which this does not need.
# ---------------------------------------------------------------------------
PANEL_BG = "#0D1B2A"
TEXT_COLOR = "#FAFCFF"
CAPTION_SHADOW = "#04090F"
RECORD_COLOR = "#E8564F"
RECORD_HOT = "#FF7B74"
ACCENT = "#5C7A99"
ACCENT_HOT = "#9EC1E8"
CONTROL_BG = "#16293D"
CONTROL_HOT = "#22405E"
NOTICE_COLOR = "#6E8CAB"
WAVE_IDLE = "#26405C"
WAVE_PEAK = "#63D2FF"
LOADER_TRACK = "#1B3149"
LOADER_FILL = "#63D2FF"

# Geometry in logical units. Qt scales these to the display itself, so unlike
# the Tk version there is no px() helper and no scale factor to thread through.
SIZE_PRESETS = {
    "Small": (620, 78, 18),
    "Medium": (900, 112, 26),
    "Large": (1240, 162, 36),
}
DEFAULT_PRESET = "Medium"
MIN_WIDTH, MIN_HEIGHT = 320, 60

# Matches the Tk build, where the Windows compositor rounded the window
# with a ~9 physical px radius. Here it is ours to set, in logical units.
PANEL_RADIUS = 7.2
BORDER_INSET = 2.5
BORDER_WIDTH = 2.0
BORDER_SEGMENTS = 96
BORDER_SPEED = 0.42
BORDER_IDLE = 0.10

EDGE_PAD = 18
GRIP_ZONE = 20
CONTROL_SIZE = 30
CONTROL_GAP = 3
WAVE_ZONE = 78
WAVE_RINGS = 4
NOTICE_PAD = 7

LOADER_WIDTH_FRAC = 0.46
LOADER_SEG_FRAC = 0.32
LOADER_THICKNESS = 3
LOADER_SPEED = 0.55

FPS = 60          # Tk ran at 25; Qt repaints cheaply enough to ask for more


def c(name, alpha=255):
    col = QColor(name)
    col.setAlpha(alpha)
    return col


def blend(a, b, t):
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
    )


class CaptionWindow(QWidget):
    def __init__(self):
        super().__init__()
        # Frameless, above everything, and absent from the taskbar and from
        # alt-tab. Qt.Tool is what keeps it out of both.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # The panel paints its own rounded shape, so the window itself must be
        # transparent. This is the part Tk could not do: there the corners came
        # from the Windows compositor because a colour key cannot antialias.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setWindowTitle("Live Translator")

        self._text = "Listening - System default"
        self._notice = ""
        self._busy = False
        self._paused = False
        self._level = 0.0        # smoothed, 0..1
        self._level_raw = 0.0
        self._phase = 0.0
        self._size_name = DEFAULT_PRESET
        self._font_size = SIZE_PRESETS[DEFAULT_PRESET][2]
        self._device_kind = "mic"
        self._direction = "AUTO"

        self._hover = None
        self._controls = {}
        self._chip_rect = QRectF()
        self._drag_from = None
        self._resize_from = None

        self._clock = QElapsedTimer()
        self._clock.start()
        self._last = 0.0

        self.apply_preset(DEFAULT_PRESET)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(1000 / FPS))

    # -- public surface the capture thread would drive ---------------------
    def set_level(self, value):
        self._level_raw = max(0.0, min(1.0, value))

    def set_text(self, text):
        self._text = text
        self.update()

    def set_notice(self, text):
        self._notice = text
        self.update()

    def set_busy(self, busy):
        self._busy = busy
        self.update()

    # -- geometry ----------------------------------------------------------
    def apply_preset(self, name):
        w, h, font_size = SIZE_PRESETS[name]
        self._size_name = name
        self._font_size = font_size
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - w) // 2
        y = screen.y() + int(screen.height() * 0.82)
        self.setGeometry(x, y, w, h)

    def caption_font(self):
        f = QFont("Segoe UI", self._font_size)
        f.setBold(True)
        return f

    def notice_font(self):
        return QFont("Segoe UI", max(9, int(self._font_size * 0.42)))

    def notice_strip(self):
        if not self._notice:
            return 0.0
        return QFontMetricsF(self.notice_font()).height() + NOTICE_PAD

    def panel_path(self):
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, PANEL_RADIUS, PANEL_RADIUS)
        return path

    def border_path(self):
        d = BORDER_INSET
        r = QRectF(self.rect()).adjusted(d, d, -d, -d)
        path = QPainterPath()
        path.addRoundedRect(r, PANEL_RADIUS - d, PANEL_RADIUS - d)
        return path

    def layout_controls(self):
        names = ["source", "listen", "menu", "close"]
        total = len(names) * CONTROL_SIZE + (len(names) - 1) * CONTROL_GAP
        x = self.width() - EDGE_PAD / 2 - total
        y = (self.height() - CONTROL_SIZE) / 2
        self._controls = {}
        for name in names:
            self._controls[name] = QRectF(x, y, CONTROL_SIZE, CONTROL_SIZE)
            x += CONTROL_SIZE + CONTROL_GAP
        return self.width() - EDGE_PAD / 2 - total - 6

    # -- animation ---------------------------------------------------------
    def _tick(self):
        now = self._clock.elapsed() / 1000.0
        dt = min(0.1, now - self._last)
        self._last = now

        # Synthetic input so the meter and border can be judged without audio.
        # Speech-shaped: bursts with gaps, not a smooth sine.
        env = max(0.0, math.sin(now * 0.9) * 0.75 + math.sin(now * 5.3) * 0.25)
        self.set_level(env if env > 0.12 else 0.0)

        # Ease towards the newest reading so the rings glide rather than snap
        self._level += (self._level_raw - self._level) * min(1.0, dt * 9.0)
        self._phase = (self._phase + dt) % 1000.0
        self.update()

    # -- painting ----------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # The panel. One antialiased rounded rect: no compositor involved, so
        # the corners are ours to size and the shape is the same on Windows 10.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c(PANEL_BG)))
        p.drawPath(self.panel_path())

        self.draw_border(p)
        controls_left = self.layout_controls()

        text_left, text_right = WAVE_ZONE, controls_left
        body_h = self.height() - self.notice_strip()

        if self._busy:
            self.draw_loader(p, text_left, text_right, body_h / 2)
        else:
            self.draw_caption(p, text_left, text_right, body_h)

        self.draw_notice(p, text_left, text_right)
        self.draw_waves(p)
        self.draw_controls(p)
        self.draw_grip(p)
        p.end()

    def draw_border(self, p):
        """Segments lit by a wave travelling around the perimeter.

        Qt hands us the outline as a path we can sample by percentage, so the
        corners come out evenly spaced for free. In Tk this needed a
        hand-rolled arc walker and still had to be tuned by eye.
        """
        path = self.border_path()
        level = self._level
        if level <= 0.001:
            return
        head = (self._phase * BORDER_SPEED) % 1.0
        pen = QPen()
        pen.setWidthF(BORDER_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        for i in range(BORDER_SEGMENTS):
            t0 = i / BORDER_SEGMENTS
            t1 = (i + 1) / BORDER_SEGMENTS
            d = (t0 - head) % 1.0
            lit = math.exp(-(d * 6.0) ** 2) * level
            amount = max(BORDER_IDLE * level, lit)
            if amount < 0.02:
                continue
            pen.setColor(blend(PANEL_BG, WAVE_PEAK, min(1.0, amount)))
            p.setPen(pen)
            p.drawLine(path.pointAtPercent(t0), path.pointAtPercent(t1))

    def draw_caption(self, p, left, right, body_h):
        rect = QRectF(left, 0, right - left, body_h)
        font = self.caption_font()
        p.setFont(font)
        flags = int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap)
        # A soft shadow a pixel down gives the glyphs an edge over a bright slide
        p.setPen(c(CAPTION_SHADOW))
        p.drawText(rect.translated(1, 2), flags, self._text)
        p.setPen(c(TEXT_COLOR))
        p.drawText(rect, flags, self._text)

    def draw_loader(self, p, left, right, cy):
        span = (right - left) * LOADER_WIDTH_FRAC
        x1 = (left + right) / 2 - span / 2
        pen = QPen(c(LOADER_TRACK))
        pen.setWidthF(LOADER_THICKNESS)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(x1, cy), QPointF(x1 + span, cy))

        seg = span * LOADER_SEG_FRAC
        travel = (self._phase * LOADER_SPEED) % 1.0
        sx = x1 + travel * (span - seg)
        pen.setColor(c(LOADER_FILL))
        p.setPen(pen)
        p.drawLine(QPointF(sx, cy), QPointF(sx + seg, cy))

    def draw_notice(self, p, left, right):
        if not self._notice:
            return
        strip = self.notice_strip()
        rect = QRectF(left, self.height() - strip - BORDER_INSET,
                      right - left, strip)
        p.setFont(self.notice_font())
        p.setPen(c(NOTICE_COLOR))
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self._notice)

    def draw_waves(self, p):
        ox, oy = 26.0, self.height() / 2.0
        lit = self._level

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(blend(WAVE_IDLE, WAVE_PEAK, lit)))
        p.drawEllipse(QPointF(ox, oy), 4, 4)

        span = min(self.height() / 2 - 8, 26)
        pen = QPen()
        pen.setWidthF(2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(WAVE_RINGS):
            r = 11 + i * max(6, span / WAVE_RINGS)
            # Each ring answers to a slice of the range, so the meter reads
            # like a level rather than all lighting at once
            share = max(0.0, min(1.0, lit * WAVE_RINGS - i))
            pen.setColor(blend(WAVE_IDLE, WAVE_PEAK, share))
            p.setPen(pen)
            box = QRectF(ox - r, oy - r, r * 2, r * 2)
            p.drawArc(box, int(-52 * 16), int(104 * 16))

        # Direction chip, anchored to the bottom edge
        p.setFont(QFont("Segoe UI", 8))
        fm = QFontMetricsF(p.font())
        tw = fm.horizontalAdvance(self._direction)
        th = fm.height()
        chip = QRectF(ox - 8 - 6, self.height() - BORDER_INSET - 5 - th - 3,
                      tw + 12, th + 6)
        self._chip_rect = chip
        hot = self._hover == "chip"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c(CONTROL_HOT if hot else CONTROL_BG)))
        p.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
        p.setPen(c(TEXT_COLOR if hot else ACCENT_HOT))
        p.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), self._direction)

    def draw_controls(self, p):
        for name, box in self._controls.items():
            hot = self._hover == name
            if hot:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(c(CONTROL_BG)))
                p.drawRoundedRect(box, 7, 7)
            colour = c(ACCENT_HOT if hot else ACCENT)
            cx, cy = box.center().x(), box.center().y()
            pen = QPen(colour)
            pen.setWidthF(1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            if name == "source":
                if self._device_kind == "loopback":
                    p.drawRect(QRectF(cx - 7, cy - 5, 14, 9))
                    p.drawLine(QPointF(cx - 4, cy + 7), QPointF(cx + 4, cy + 7))
                    p.drawLine(QPointF(cx, cy + 4), QPointF(cx, cy + 7))
                else:
                    p.drawRoundedRect(QRectF(cx - 3, cy - 8, 6, 9), 3, 3)
                    p.drawArc(QRectF(cx - 6, cy - 4, 12, 10),
                              int(200 * 16), int(140 * 16))
                    p.drawLine(QPointF(cx, cy + 5), QPointF(cx, cy + 8))
            elif name == "listen":
                p.setPen(Qt.PenStyle.NoPen)
                if self._paused:
                    dot = c(RECORD_HOT if hot else RECORD_COLOR)
                    p.setBrush(QBrush(dot))
                    p.drawEllipse(QPointF(cx, cy), 6, 6)
                else:
                    p.setBrush(QBrush(colour))
                    p.drawRect(QRectF(cx - 5, cy - 5, 10, 10))
            elif name == "menu":
                pen.setWidthF(1.8)
                p.setPen(pen)
                for dy in (-5, 0, 5):
                    p.drawLine(QPointF(cx - 6, cy + dy), QPointF(cx + 6, cy + dy))
            elif name == "close":
                pen.setWidthF(1.8)
                p.setPen(pen)
                p.drawLine(QPointF(cx - 5, cy - 5), QPointF(cx + 5, cy + 5))
                p.drawLine(QPointF(cx + 5, cy - 5), QPointF(cx - 5, cy + 5))

    def draw_grip(self, p):
        colour = c(ACCENT_HOT if self._hover == "grip" else ACCENT)
        pen = QPen(colour)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        w, h = self.width(), self.height()
        for off in (4, 9):
            p.drawLine(QPointF(w - off - 3, h - 4), QPointF(w - 4, h - off - 3))

    # -- interaction -------------------------------------------------------
    def _hit(self, pos):
        if (pos.x() >= self.width() - GRIP_ZONE
                and pos.y() >= self.height() - GRIP_ZONE):
            return "grip"
        for name, box in self._controls.items():
            if box.contains(pos):
                return name
        if self._chip_rect.contains(pos):
            return "chip"
        return None

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._resize_from is not None:
            start_pos, sw, sh = self._resize_from
            d = event.globalPosition() - start_pos
            self.resize(max(MIN_WIDTH, int(sw + d.x())),
                        max(MIN_HEIGHT, int(sh + d.y())))
            return
        if self._drag_from is not None:
            self.move((event.globalPosition() - self._drag_from).toPoint())
            return
        was, self._hover = self._hover, self._hit(pos)
        if was != self._hover:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor
                           if self._hover == "grip"
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = self._hit(event.position())
        if target == "grip":
            self._resize_from = (event.globalPosition(),
                                 self.width(), self.height())
        elif target == "close":
            self.close()
        elif target == "listen":
            self._paused = not self._paused
            self._text = ("Paused" if self._paused
                          else "Listening - System default")
            self.update()
        elif target == "source":
            self._device_kind = ("loopback" if self._device_kind == "mic"
                                 else "mic")
            self.update()
        elif target == "menu":
            self._busy = not self._busy
            self.update()
        elif target == "chip":
            order = ["AUTO", "EN -> 中文", "中文 -> EN", "SW -> EN"]
            i = order.index(self._direction) if self._direction in order else 0
            self._direction = order[(i + 1) % len(order)]
            self.update()
        else:
            self._drag_from = event.globalPosition() - self.frameGeometry().topLeft()

    def mouseReleaseEvent(self, _event):
        self._drag_from = None
        self._resize_from = None

    def mouseDoubleClickEvent(self, _event):
        order = list(SIZE_PRESETS)
        i = order.index(self._size_name)
        self.apply_preset(order[(i + 1) % len(order)])

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()


def main():
    app = QApplication(sys.argv)
    w = CaptionWindow()
    w.set_notice("Qt prototype - drag to move, corner to resize, "
                 "double-click to cycle size")
    w.show()

    if "--demo-states" in sys.argv:
        QTimer.singleShot(3000, lambda: w.set_busy(True))
        QTimer.singleShot(6000, lambda: (w.set_busy(False),
                                         w.set_text("今天的會議將討論下一季的預算")))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
