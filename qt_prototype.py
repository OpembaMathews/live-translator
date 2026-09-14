"""Qt build of the caption widget, following the suggested pill design.

The layout comes from the mockup: a stadium panel with a soft glow, a circular
level gauge on the left holding the source glyph, a LISTENING -> TRANSLATING
status row, the heard line above the translated line, and a control cluster
behind a divider.

No component was dropped on the way across. The mockup's three right-hand
slots carry the app's own controls, and the listen toggle moved onto the gauge
where the mockup put the microphone:

    gauge (click)   start / stop listening, and the input level meter
    source          microphone or system audio, glyph changes with it
    gear            settings
    close           quit

Still a UI prototype: no audio and no translation. A synthetic level drives the
gauge, and set_level() / set_lines() / set_busy() are where the capture and
recognition threads would push into.

Run:  python qt_prototype.py
"""
import math
import sys

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QElapsedTimer
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QBrush,
    QGuiApplication, QLinearGradient,
)
from PySide6.QtWidgets import QApplication, QWidget

# ---------------------------------------------------------------------------
# Palette. Carried over from the app, with the mockup's two accents added:
# cyan for anything to do with hearing, violet for anything to do with
# translating, so the two halves of the pipeline stay visually separate.
# ---------------------------------------------------------------------------
PANEL_BG = "#101A26"
PANEL_EDGE = "#26384C"
INNER_BG = "#1B2A3C"

TEXT_COLOR = "#FAFCFF"
TRANS_COLOR = "#A9D8F2"
NOTICE_COLOR = "#6E8CAB"

HEAR = "#38BDF8"          # listening accent
HEAR_DIM = "#1B394F"
HEAR_TEXT = "#7DD3FC"
TRANSLATE = "#A78BFA"     # translating accent
TRANSLATE_DIM = "#2A2450"
TRANSLATE_TEXT = "#C4B5FD"

RING_TRACK = "#223449"
RING_A = "#22D3EE"        # gauge gradient, low end
RING_B = "#3B82F6"        # gauge gradient, high end
GLOW = "#38BDF8"

ACCENT = "#7E99B5"
ACCENT_HOT = "#D8E8F7"
CONTROL_HOT = "#22405E"
RECORD_COLOR = "#F87171"

# Geometry in logical units. Qt scales these to the display itself, so there
# is no px() helper and no scale factor threaded through the drawing code.
SIZE_PRESETS = {
    "Small": (780, 118, 17),
    "Medium": (1000, 148, 21),
    "Large": (1300, 188, 27),
}
DEFAULT_PRESET = "Medium"
MIN_WIDTH, MIN_HEIGHT = 520, 96

GLOW_PAD = 26          # window margin the glow is painted into
GLOW_RINGS = 18
GLOW_ALPHA = 52        # alpha of the innermost glow ring, at full level

GAUGE_GAP = 64         # degrees of track left open at the bottom
GRIP_ZONE = 22
CONTROL_SIZE = 30
CONTROL_GAP = 8
PILL_H = 30
BAR_COUNT = 5

FPS = 60


def c(name, alpha=255):
    col = QColor(name)
    col.setAlpha(alpha)
    return col


def blend(a, b, t):
    ca, cb = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t),
                  round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t))


class CaptionWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setWindowTitle("Live Translator")

        self._heard = "Hello, how are you?"
        self._translated = "你好，你好嗎？"
        self._notice = ""
        self._listening = True
        self._busy = False
        self._level = 0.0
        self._level_raw = 0.0
        self._phase = 0.0
        self._bars = [0.2] * BAR_COUNT
        self._size_name = DEFAULT_PRESET
        self._font_size = SIZE_PRESETS[DEFAULT_PRESET][2]
        self._device_kind = "mic"

        self._hover = None
        self._controls = {}
        self._gauge = QRectF()
        self._drag_from = None
        self._resize_from = None

        self._clock = QElapsedTimer()
        self._clock.start()
        self._last = 0.0

        self.apply_preset(DEFAULT_PRESET)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(1000 / FPS))

    # -- the surface the app's threads would drive -------------------------
    def set_level(self, value):
        self._level_raw = max(0.0, min(1.0, value))

    def set_lines(self, heard, translated):
        self._heard, self._translated = heard, translated
        self.update()

    def set_busy(self, busy):
        self._busy = busy
        self.update()

    def set_listening(self, on):
        self._listening = on
        self.update()

    def set_notice(self, text):
        self._notice = text
        self.update()

    # -- geometry ----------------------------------------------------------
    def apply_preset(self, name):
        w, h, font_size = SIZE_PRESETS[name]
        self._size_name = name
        self._font_size = font_size
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - (w + GLOW_PAD * 2)) // 2
        y = screen.y() + int(screen.height() * 0.78)
        self.setGeometry(x, y, w + GLOW_PAD * 2, h + GLOW_PAD * 2)

    def panel_rect(self):
        """The pill itself. The window is larger so the glow has room."""
        return QRectF(self.rect()).adjusted(GLOW_PAD, GLOW_PAD,
                                            -GLOW_PAD, -GLOW_PAD)

    def stadium(self, rect):
        path = QPainterPath()
        r = rect.height() / 2
        path.addRoundedRect(rect, r, r)
        return path

    def heard_font(self):
        f = QFont()
        f.setFamilies(["Segoe UI", "Microsoft JhengHei UI"])
        f.setPointSizeF(self._font_size)
        f.setBold(True)
        return f

    def translated_font(self):
        f = QFont()
        f.setFamilies(["Microsoft JhengHei UI", "Segoe UI"])
        f.setPointSizeF(self._font_size * 0.92)
        return f

    def label_font(self):
        f = QFont()
        f.setFamilies(["Segoe UI"])
        f.setPointSizeF(max(8.0, self._font_size * 0.44))
        f.setBold(True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
        return f

    # -- animation ---------------------------------------------------------
    def _tick(self):
        now = self._clock.elapsed() / 1000.0
        dt = min(0.1, now - self._last)
        self._last = now

        if self._listening:
            # Synthetic input so the gauge can be judged without audio.
            # Speech-shaped: bursts with gaps, not a smooth sine.
            env = max(0.0, math.sin(now * 0.9) * 0.75
                      + math.sin(now * 5.3) * 0.25)
            self.set_level(env if env > 0.12 else 0.0)
        else:
            self.set_level(0.0)

        self._level += (self._level_raw - self._level) * min(1.0, dt * 9.0)
        self._phase = (self._phase + dt) % 1000.0

        for i in range(BAR_COUNT):
            target = self._level * (0.45 + 0.55 * abs(
                math.sin(now * (4.0 + i * 1.7) + i)))
            self._bars[i] += (target - self._bars[i]) * min(1.0, dt * 14.0)
        self.update()

    # -- painting ----------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        panel = self.panel_rect()
        self.draw_glow(p, panel)

        p.setPen(QPen(c(PANEL_EDGE), 1.0))
        p.setBrush(QBrush(c(PANEL_BG)))
        p.drawPath(self.stadium(panel))

        gauge_right = self.draw_gauge(p, panel)
        controls_left = self.draw_controls(p, panel)
        self.draw_content(p, panel, gauge_right, controls_left)
        self.draw_grip(p, panel)
        p.end()

    def draw_glow(self, p, panel):
        """Soft halo, brightening with the input level.

        Concentric stadium outlines with falling alpha. This is the border
        animation the Tk build had, moved outside the panel where the mockup
        put it, and it only works because the window has per-pixel alpha.
        """
        strength = 0.35 + 0.65 * self._level
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(GLOW_RINGS, 0, -1):
            spread = i * (GLOW_PAD / GLOW_RINGS)
            fade = (1.0 - i / GLOW_RINGS) ** 2.2
            alpha = int(GLOW_ALPHA * fade * strength)
            if alpha < 2:
                continue
            pen = QPen(c(GLOW, alpha))
            pen.setWidthF(2.2)
            p.setPen(pen)
            p.drawPath(self.stadium(panel.adjusted(-spread, -spread,
                                                   spread, spread)))

    def draw_gauge(self, p, panel):
        """Circular level meter with the source glyph in the middle.

        Replaces the radio-wave arcs. Same job, same input, and clicking it
        is the listen toggle the mockup implies by putting a mic here.
        """
        d = panel.height() - 22
        box = QRectF(panel.left() + 11, panel.top() + 11, d, d)
        self._gauge = box
        cx, cy = box.center().x(), box.center().y()
        r = d / 2

        span = 360 - GAUGE_GAP
        start = 250 - GAUGE_GAP / 2       # gap sits low and to the left

        thick = r * 0.17
        track = QRectF(box).adjusted(thick, thick, -thick, -thick)
        pen = QPen(c(RING_TRACK))
        pen.setWidthF(thick)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawArc(track, int(-start * 16), int(-span * 16))

        lit = self._level if self._listening else 0.0
        if lit > 0.01:
            # Halo first, then the arc on top, so the ring reads as emitting
            # light rather than just being a brighter line.
            for spread, alpha in ((3.2, 26), (1.8, 46)):
                halo = QPen(c(GLOW, alpha))
                halo.setWidthF(thick + spread * 2)
                halo.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(halo)
                p.drawArc(track, int(-start * 16), int(-span * lit * 16))
            grad = QLinearGradient(track.bottomLeft(), track.topRight())
            grad.setColorAt(0.0, c(RING_A))
            grad.setColorAt(1.0, c(RING_B))
            pen = QPen(QBrush(grad), thick)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(track, int(-start * 16), int(-span * lit * 16))

        # inner disc
        inner = r * 0.62
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c(INNER_BG)))
        p.drawEllipse(QPointF(cx, cy), inner, inner)

        glyph = c(TEXT_COLOR) if self._listening else c(RECORD_COLOR)
        if self._hover == "gauge":
            glyph = c(ACCENT_HOT)
        s = inner / 13.0                   # glyph scale, from the inner disc
        self.draw_source_glyph(p, cx, cy, s, glyph)
        return box.right()

    def draw_source_glyph(self, p, cx, cy, s, colour):
        """Microphone or monitor, drawn to the same optical weight.

        One routine so the gauge and the small control button cannot drift
        apart; s is the only thing that differs between them.
        """
        pen = QPen(colour)
        pen.setWidthF(max(1.3, s * 1.15))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if self._device_kind == "loopback":
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(cx - s * 7, cy - s * 6, s * 14, s * 9.5),
                              s * 1.4, s * 1.4)
            p.drawLine(QPointF(cx - s * 4, cy + s * 8),
                       QPointF(cx + s * 4, cy + s * 8))
            p.drawLine(QPointF(cx, cy + s * 3.5), QPointF(cx, cy + s * 8))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(colour))
            p.drawRoundedRect(QRectF(cx - s * 2.6, cy - s * 8, s * 5.2, s * 10),
                              s * 2.6, s * 2.6)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # cradle: starts level with the capsule so the two read as one mark
            p.drawArc(QRectF(cx - s * 5, cy - s * 3.4, s * 10, s * 8),
                      int(195 * 16), int(150 * 16))
            p.drawLine(QPointF(cx, cy + s * 4.6), QPointF(cx, cy + s * 7.6))

    def draw_content(self, p, panel, left, right):
        x = left + panel.height() * 0.18
        w = right - x
        if w < 80:
            return
        top = panel.top() + panel.height() * 0.14

        pill_bottom = self.draw_status_row(p, x, top, w)

        gap = panel.height() * 0.04
        lead = panel.height() * 0.02      # air between the two lines
        remaining = panel.bottom() - pill_bottom - gap
        fm_h = QFontMetricsF(self.heard_font()).height()
        fm_t = QFontMetricsF(self.translated_font()).height()
        block = fm_h + lead + fm_t
        y = (pill_bottom + gap + max(0.0, (remaining - block) / 2)
             - panel.height() * 0.04)

        p.setFont(self.heard_font())
        p.setPen(c(TEXT_COLOR))
        p.drawText(QRectF(x, y, w, fm_h),
                   int(Qt.AlignmentFlag.AlignVCenter
                       | Qt.AlignmentFlag.AlignHCenter),
                   self._heard)

        p.setFont(self.translated_font())
        p.setPen(c(TRANS_COLOR))
        p.drawText(QRectF(x, y + fm_h + lead, w, fm_t),
                   int(Qt.AlignmentFlag.AlignVCenter
                       | Qt.AlignmentFlag.AlignHCenter),
                   self._translated)

    def draw_status_row(self, p, x, y, content_w):
        """LISTENING -> TRANSLATING, centred over the text block."""
        font = self.label_font()
        fm = QFontMetricsF(font)
        h = max(PILL_H, fm.height() + 10)

        hear_on = self._listening
        trans_on = self._busy
        hear_text = self._notice.upper() if self._notice else "LISTENING..."
        hear_w = fm.horizontalAdvance(hear_text) + h * 1.9
        trans_w = fm.horizontalAdvance("TRANSLATING...") + h * 1.9
        arrow_w = h * 1.5
        total = hear_w + arrow_w + trans_w
        sx = x + max(0.0, (content_w - total) / 2)

        p.setFont(font)
        bx = sx
        self.draw_pill(p, bx, y, hear_w, h, hear_text, HEAR, HEAR_DIM,
                       HEAR_TEXT, hear_on, icon="wave")
        bx += hear_w

        p.setPen(QPen(c(ACCENT if not trans_on else TRANSLATE_TEXT), 1.4))
        ay = y + h / 2
        p.drawLine(QPointF(bx + arrow_w * 0.30, ay),
                   QPointF(bx + arrow_w * 0.70, ay))
        p.drawLine(QPointF(bx + arrow_w * 0.70, ay),
                   QPointF(bx + arrow_w * 0.55, ay - 4))
        p.drawLine(QPointF(bx + arrow_w * 0.70, ay),
                   QPointF(bx + arrow_w * 0.55, ay + 4))
        bx += arrow_w

        self.draw_pill(p, bx, y, trans_w, h, "TRANSLATING...", TRANSLATE,
                       TRANSLATE_DIM, TRANSLATE_TEXT, trans_on, icon="lang")
        return y + h

    def draw_pill(self, p, x, y, w, h, text, accent, dim, text_col, on,
                  icon):
        label = p.font()          # the icon routines set fonts of their own
        box = QRectF(x, y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(blend(PANEL_BG, dim, 1.0 if on else 0.55)))
        p.drawRoundedRect(box, h / 2, h / 2)

        ix = x + h * 0.64
        iy = y + h / 2
        col = QColor(accent) if on else blend(PANEL_BG, accent, 0.60)
        if icon == "wave":
            self.draw_bars(p, ix, iy, h, col, on)
        else:
            self.draw_lang_glyph(p, ix, iy, h, col)

        p.setFont(label)
        p.setPen(QColor(text_col) if on else blend(PANEL_BG, text_col, 0.62))
        p.drawText(QRectF(x + h * 1.25, y, w - h * 1.5, h),
                   int(Qt.AlignmentFlag.AlignVCenter
                       | Qt.AlignmentFlag.AlignLeft), text)
        return box

    def draw_bars(self, p, cx, cy, h, colour, live):
        """Five bars that ride the input level, the listening pill's icon."""
        bw = h * 0.075
        gap = bw * 1.7
        span = h * 0.42
        pen = QPen(colour)
        pen.setWidthF(bw)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        start = cx - (BAR_COUNT - 1) * gap / 2
        for i in range(BAR_COUNT):
            amp = self._bars[i] if live else 0.12
            hh = span * (0.22 + 0.78 * max(0.08, amp))
            bx = start + i * gap
            p.drawLine(QPointF(bx, cy - hh / 2), QPointF(bx, cy + hh / 2))

    def draw_lang_glyph(self, p, cx, cy, h, colour):
        """The translate mark: a CJK glyph with a Latin A tucked under it."""
        p.setPen(colour)
        f = QFont()
        f.setFamilies(["Microsoft JhengHei UI", "Segoe UI"])
        f.setPointSizeF(h * 0.30)
        p.setFont(f)
        p.drawText(QRectF(cx - h * 0.36, cy - h * 0.40, h * 0.52, h * 0.60),
                   int(Qt.AlignmentFlag.AlignCenter), "文")
        f2 = QFont()
        f2.setFamilies(["Segoe UI"])
        f2.setPointSizeF(h * 0.22)
        f2.setBold(True)
        p.setFont(f2)
        p.drawText(QRectF(cx - h * 0.05, cy - h * 0.06, h * 0.44, h * 0.46),
                   int(Qt.AlignmentFlag.AlignCenter), "A")

    def draw_controls(self, p, panel):
        names = ["source", "gear", "close"]
        total = len(names) * CONTROL_SIZE + (len(names) - 1) * CONTROL_GAP
        right = panel.right() - panel.height() * 0.42
        x = right - total
        y = panel.center().y() - CONTROL_SIZE / 2

        # divider, as in the mockup
        p.setPen(QPen(c("#33485F"), 1.2))
        dx = x - panel.height() * 0.16
        p.drawLine(QPointF(dx, panel.center().y() - panel.height() * 0.20),
                   QPointF(dx, panel.center().y() + panel.height() * 0.20))

        self._controls = {}
        for name in names:
            box = QRectF(x, y, CONTROL_SIZE, CONTROL_SIZE)
            self._controls[name] = box
            hot = self._hover == name
            if hot:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(c(CONTROL_HOT)))
                p.drawEllipse(box)
            colour = c(ACCENT_HOT if hot else ACCENT)
            self.draw_icon(p, name, box.center(), colour)
            x += CONTROL_SIZE + CONTROL_GAP

        return dx - panel.height() * 0.10

    def draw_icon(self, p, name, centre, colour):
        cx, cy = centre.x(), centre.y()
        pen = QPen(colour)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        if name == "gear":
            # Solid silhouette with the hole punched in the backdrop colour.
            # Stroked teeth at this size just read as a sun.
            hole = c(CONTROL_HOT) if self._hover == name else c(PANEL_BG)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(colour))
            p.save()
            p.translate(cx, cy)
            for i in range(8):
                p.save()
                p.rotate(i * 45)
                p.drawRoundedRect(QRectF(-1.7, -8.6, 3.4, 4.6), 1.0, 1.0)
                p.restore()
            p.drawEllipse(QPointF(0, 0), 6.0, 6.0)
            p.setBrush(QBrush(hole))
            p.drawEllipse(QPointF(0, 0), 2.5, 2.5)
            p.restore()
        elif name == "close":
            pen.setWidthF(1.8)
            p.setPen(pen)
            p.drawLine(QPointF(cx - 5.5, cy - 5.5), QPointF(cx + 5.5, cy + 5.5))
            p.drawLine(QPointF(cx + 5.5, cy - 5.5), QPointF(cx - 5.5, cy + 5.5))
        elif name == "source":
            self.draw_source_glyph(p, cx, cy, 1.15, colour)

    def draw_grip(self, p, panel):
        colour = c(ACCENT_HOT if self._hover == "grip" else ACCENT)
        pen = QPen(colour)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        # tucked inside the right cap, on its diagonal
        bx = panel.right() - panel.height() * 0.26
        by = panel.bottom() - panel.height() * 0.16
        for off in (0, 4.5):
            p.drawLine(QPointF(bx - 5 + off, by),
                       QPointF(bx + 1, by - 6 + off))

    # -- interaction -------------------------------------------------------
    def _hit(self, pos):
        panel = self.panel_rect()
        grip = QRectF(panel.right() - GRIP_ZONE, panel.bottom() - GRIP_ZONE,
                      GRIP_ZONE, GRIP_ZONE)
        if grip.contains(pos):
            return "grip"
        for name, box in self._controls.items():
            if box.contains(pos):
                return name
        if self._gauge.contains(pos):
            return "gauge"
        if not panel.contains(pos):
            return "outside"
        return None

    def mouseMoveEvent(self, event):
        if self._resize_from is not None:
            start, sw, sh = self._resize_from
            d = event.globalPosition() - start
            self.resize(max(MIN_WIDTH + GLOW_PAD * 2, int(sw + d.x())),
                        max(MIN_HEIGHT + GLOW_PAD * 2, int(sh + d.y())))
            return
        if self._drag_from is not None:
            self.move((event.globalPosition() - self._drag_from).toPoint())
            return
        was, self._hover = self._hover, self._hit(event.position())
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
        elif target == "gauge":
            self._listening = not self._listening
            self.update()
        elif target == "source":
            self._device_kind = ("loopback" if self._device_kind == "mic"
                                 else "mic")
            self.update()
        elif target == "gear":
            # stands in for opening the settings window
            self._busy = not self._busy
            self.update()
        elif target != "outside":
            self._drag_from = (event.globalPosition()
                               - self.frameGeometry().topLeft())

    def mouseReleaseEvent(self, _event):
        self._drag_from = None
        self._resize_from = None

    def mouseDoubleClickEvent(self, _event):
        order = list(SIZE_PRESETS)
        self.apply_preset(order[(order.index(self._size_name) + 1)
                                % len(order)])

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()


def main():
    app = QApplication(sys.argv)
    w = CaptionWindow()
    w.show()

    if "--demo" in sys.argv:
        lines = [
            ("Hello, how are you?", "你好，你好嗎？"),
            ("The meeting starts at three.", "會議三點開始。"),
            ("Habari ya asubuhi, karibu sana.", "Good morning, you are welcome."),
        ]
        state = {"i": 0}

        def cycle():
            w.set_busy(True)
            QTimer.singleShot(900, lambda: (
                w.set_busy(False),
                w.set_lines(*lines[state["i"] % len(lines)]),
                state.__setitem__("i", state["i"] + 1)))
        QTimer.singleShot(1500, cycle)
        t = QTimer(w)
        t.timeout.connect(cycle)
        t.start(4000)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
