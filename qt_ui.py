"""The caption window, in Qt: the pill design from the mockup.

A view and nothing else. It paints what it is told and reports clicks to a
controller; qt_app.py supplies the real one, backed by the capture and
translation engine in translation.py.

The mockup has three control slots and the app has four things to do, so the
listen toggle sits on the gauge where the mockup put the microphone:

    gauge (click)   start / stop listening, and the input level meter
    source          microphone or system audio, glyph changes with it
    gear            settings
    close           quit

Run this file directly for a self-contained demo with a synthetic level.
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
GRIP_ZONE = 30          # clickable square around the resize handle
CONTROL_SIZE = 30
CONTROL_GAP = 8
PILL_H = 30
# Caption size as a share of the pill's height. 21pt on the 148-unit Medium
# pill, and the same ratio lands within a point of Small and Large, so the
# presets look as they did while a dragged size gets text to match.
CAPTION_RATIO = 21 / 148
MIN_CAPTION_PT = 9.0
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
    """Paints the pill. Every click is forwarded to `controller`.

    The controller only has to answer the five methods named in _act(); with
    none supplied the window drives itself, which is what the demo uses.
    """

    def __init__(self, controller=None, demo=False):
        super().__init__()
        self.controller = controller
        self.demo = demo
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
        w, h, _pt = SIZE_PRESETS[name]    # text size now follows the height
        self._size_name = name
        full_w, full_h = w + GLOW_PAD * 2, h + GLOW_PAD * 2
        if self.isVisible():
            # Grow or shrink about the current centre, so a size change does
            # not throw away where the user dragged the window. Kept on the
            # screen it is on.
            # QRect.center() rounds down, which crept the window a pixel up
            # and left on every change; keep the exact centre instead.
            g = self.geometry()
            cx2, cy2 = 2 * g.x() + g.width(), 2 * g.y() + g.height()
            screen = (self.screen() or QGuiApplication.primaryScreen()
                      ).availableGeometry()
            x = (cx2 - full_w) // 2
            y = (cy2 - full_h) // 2
            x = max(screen.left(), min(x, screen.right() - full_w))
            y = max(screen.top(), min(y, screen.bottom() - full_h))
        else:
            screen = QGuiApplication.primaryScreen().availableGeometry()
            x = screen.x() + (screen.width() - full_w) // 2
            y = screen.y() + int(screen.height() * 0.78)
        self.setGeometry(x, y, full_w, full_h)

    def panel_rect(self):
        """The pill itself. The window is larger so the glow has room."""
        return QRectF(self.rect()).adjusted(GLOW_PAD, GLOW_PAD,
                                            -GLOW_PAD, -GLOW_PAD)

    def stadium(self, rect):
        path = QPainterPath()
        r = rect.height() / 2
        path.addRoundedRect(rect, r, r)
        return path

    def heard_font(self, pt):
        f = QFont()
        f.setFamilies(["Segoe UI", "Microsoft JhengHei UI"])
        f.setPointSizeF(pt)
        f.setBold(True)
        return f

    def translated_font(self, pt):
        f = QFont()
        f.setFamilies(["Microsoft JhengHei UI", "Segoe UI"])
        f.setPointSizeF(pt * 0.92)
        return f

    def label_font(self, pt):
        f = QFont()
        f.setFamilies(["Segoe UI"])
        f.setPointSizeF(max(8.0, pt * 0.44))
        f.setBold(True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
        return f

    @staticmethod
    def fit_line(text, make_font, pt, width):
        """The font and text to draw one caption line in `width`.

        Shrinks the face first, down to 72% of the size the height allows,
        because a slightly smaller whole sentence reads better than a cut
        one. Only past that does it elide.
        """
        floor = max(MIN_CAPTION_PT, pt * 0.72)
        size = pt
        while True:
            font = make_font(size)
            fm = QFontMetricsF(font)
            if fm.horizontalAdvance(text) <= width or size <= floor:
                break
            size = max(floor, size * 0.94)
        if fm.horizontalAdvance(text) > width:
            text = fm.elidedText(text, Qt.TextElideMode.ElideRight, width)
        return font, text

    # -- animation ---------------------------------------------------------
    def _tick(self):
        now = self._clock.elapsed() / 1000.0
        dt = min(0.1, now - self._last)
        self._last = now

        if self.demo:
            # Speech-shaped synthetic input: bursts with gaps, not a sine.
            env = max(0.0, math.sin(now * 0.9) * 0.75
                      + math.sin(now * 5.3) * 0.25)
            self.set_level(env if self._listening and env > 0.12 else 0.0)
        elif not self._listening:
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
        """Status row over two caption lines, all sized from the pill.

        Everything here follows the window as it is dragged. The status row
        drops to icon-only pills when the words do not fit beside the
        controls, and disappears when the pill is too short to hold it.
        """
        x = left + panel.height() * 0.18
        w = right - x
        if w < 80:
            return

        pt = max(MIN_CAPTION_PT, panel.height() * CAPTION_RATIO)
        lead = panel.height() * 0.02
        row_gap = panel.height() * 0.05
        budget = panel.height() * 0.86

        # Settle on a caption size whose block, with the row, fits the height
        while True:
            row_h = self.status_row_height(pt)
            show_row = panel.height() >= 104
            block = (QFontMetricsF(self.heard_font(pt)).height() + lead
                     + QFontMetricsF(self.translated_font(pt)).height())
            total = block + ((row_h + row_gap) if show_row else 0)
            if total <= budget or pt <= MIN_CAPTION_PT:
                break
            pt = max(MIN_CAPTION_PT, pt * 0.94)

        y = panel.center().y() - total / 2
        heard, is_notice = self._heard, False
        if show_row:
            compact = not self.draw_status_row(p, x, y, w, pt)
            y += row_h + row_gap
            if compact and self._notice:
                # Icon-only pills have no room for the notice; it borrows the
                # heard line until it clears.
                heard, is_notice = self._notice, True
        elif self._notice:
            heard, is_notice = self._notice, True

        font, text = self.fit_line(heard, self.heard_font, pt, w)
        h1 = QFontMetricsF(font).height()
        p.setFont(font)
        p.setPen(c(NOTICE_COLOR if is_notice else TEXT_COLOR))
        p.drawText(QRectF(x, y, w, h1), int(Qt.AlignmentFlag.AlignCenter), text)

        font, text = self.fit_line(self._translated, self.translated_font,
                                   pt, w)
        h2 = QFontMetricsF(font).height()
        p.setFont(font)
        p.setPen(c(TRANS_COLOR))
        p.drawText(QRectF(x, y + h1 + lead, w, h2),
                   int(Qt.AlignmentFlag.AlignCenter), text)

    def status_row_height(self, pt):
        return max(PILL_H, QFontMetricsF(self.label_font(pt)).height() + 10)

    def draw_status_row(self, p, x, y, content_w, pt):
        """LISTENING -> TRANSLATING, centred over the text block.

        Returns False when it had to fall back to icon-only pills.
        """
        font = self.label_font(pt)
        fm = QFontMetricsF(font)
        h = self.status_row_height(pt)

        hear_on = self._listening
        trans_on = self._busy
        hear_text = self._notice.upper() if self._notice else "LISTENING..."
        arrow_w = h * 1.5
        hear_w = fm.horizontalAdvance(hear_text) + h * 1.9
        trans_w = fm.horizontalAdvance("TRANSLATING...") + h * 1.9
        full = hear_w + arrow_w + trans_w <= content_w
        if not full:
            hear_w = trans_w = h * 1.4
            hear_text = trans_text = ""
        else:
            trans_text = "TRANSLATING..."
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

        self.draw_pill(p, bx, y, trans_w, h, trans_text, TRANSLATE,
                       TRANSLATE_DIM, TRANSLATE_TEXT, trans_on, icon="lang")
        return full

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

    def grip_rect(self, panel):
        """Where the resize handle is, for drawing and for clicking alike.

        It sits inside the right cap on its diagonal. The corner of the
        bounding box would be the obvious spot, but on a pill that corner is
        outside the shape: nothing visible is there, and the handle that was
        drawn further in did not overlap it, so resizing could not be found.
        """
        cx = panel.right() - panel.height() * 0.26
        cy = panel.bottom() - panel.height() * 0.19
        return QRectF(cx - GRIP_ZONE / 2, cy - GRIP_ZONE / 2,
                      GRIP_ZONE, GRIP_ZONE)

    def draw_grip(self, p, panel):
        hot = self._hover == "grip" or self._resize_from is not None
        box = self.grip_rect(panel)
        if hot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(c(CONTROL_HOT)))
            p.drawEllipse(box.center(), 11, 11)
        pen = QPen(c(ACCENT_HOT if hot else ACCENT))
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        cx, cy = box.center().x(), box.center().y()
        for off in (0, 4.5):
            p.drawLine(QPointF(cx - 4 + off, cy + 3.5),
                       QPointF(cx + 3.5, cy - 4 + off))

    # -- interaction -------------------------------------------------------
    def _hit(self, pos):
        panel = self.panel_rect()
        if self.grip_rect(panel).contains(pos):
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
        elif target in ("close", "gauge", "source", "gear"):
            self._act(target, event.globalPosition())
        elif target != "outside":
            self._drag_from = (event.globalPosition()
                               - self.frameGeometry().topLeft())

    def _act(self, target, global_pos):
        """Ask the controller, or fall back to local state for the demo."""
        ctl = self.controller
        if ctl is not None:
            {"close": ctl.close,
             "gauge": ctl.toggle_listening,
             "source": ctl.toggle_source,
             "gear": lambda: ctl.open_settings(global_pos)}[target]()
            return
        if target == "close":
            self.close()
        elif target == "gauge":
            self._listening = not self._listening
        elif target == "source":
            self._device_kind = ("loopback" if self._device_kind == "mic"
                                 else "mic")
        elif target == "gear":
            self._busy = not self._busy
        self.update()

    def mouseReleaseEvent(self, _event):
        self._drag_from = None
        self._resize_from = None
        self.update()

    def mouseDoubleClickEvent(self, _event):
        order = list(SIZE_PRESETS)
        name = order[(order.index(self._size_name) + 1) % len(order)]
        if self.controller is not None:
            self.controller.set_size(name)
        else:
            self.apply_preset(name)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.controller is not None:
                self.controller.close()
            else:
                self.close()

    # -- state the controller pushes in ------------------------------------
    def set_device_kind(self, kind):
        self._device_kind = kind
        self.update()

    def set_size(self, name):
        if name in SIZE_PRESETS:
            self.apply_preset(name)


def main():
    """Standalone demo. The real app lives in qt_app.py."""
    app = QApplication(sys.argv)
    w = CaptionWindow(demo=True)
    w.show()

    if True:
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
