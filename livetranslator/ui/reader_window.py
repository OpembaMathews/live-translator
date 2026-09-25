"""The reader window: the paper as it looks, read aloud, highlighted as it goes.

The page is shown as the real PDF rather than extracted text, because a
student is reading a paper, not a transcript of one. The sentence being
spoken is tinted on the page and the view scrolls to keep it visible.

Pages are rendered once, lazily, and cached. A 12-page paper at 130 dpi is a
few megabytes; rendering every page up front would stall the open.
"""
import os

import pymupdf
from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from ..log import log
from . import reader_chrome as chrome
from ..reader import document
from ..reader.player import Player, passages

DPI = 130
PAGE_GAP = 14
SIDE_MARGIN = 10
HIGHLIGHT = QColor(56, 189, 248, 62)       # the app's cyan, kept light
HIGHLIGHT_EDGE = QColor(56, 189, 248, 90)
SPEEDS = ("0.8x", "0.9x", "1.0x", "1.1x", "1.25x", "1.5x")



def merge_lines(boxes):
    """Join boxes that sit on the same line of text.

    A search returns a box per fragment it matched, so a sentence comes back
    as a row of little boxes. Drawn separately they look like every word is
    boxed; merged, each line of the sentence gets one highlight.
    """
    rows = []
    for x0, y0, x1, y1 in sorted(boxes, key=lambda b: (round(b[1], 1), b[0])):
        for row in rows:
            overlap = min(y1, row[3]) - max(y0, row[1])
            if overlap > 0.6 * min(y1 - y0, row[3] - row[1]):
                row[0] = min(row[0], x0)
                row[1] = min(row[1], y0)
                row[2] = max(row[2], x1)
                row[3] = max(row[3], y1)
                break
        else:
            rows.append([x0, y0, x1, y1])
    return [tuple(r) for r in rows]


class PageView(QWidget):
    """The pages, stacked vertically, with one sentence tinted."""

    clicked = Signal(int, QPoint)      # page number, point in PDF coordinates

    def __init__(self):
        super().__init__()
        self.doc = None
        self.scale = DPI / 72.0
        self._pixmaps = {}
        self._boxes = {}
        self._tops = []                # y of each page in this widget
        self.mark = None               # (page, [boxes in PDF points])
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def load(self, doc):
        self.doc = doc
        self.relayout()

    def relayout(self):
        """Work out where each page sits at the current scale."""
        self._pixmaps.clear()
        self._tops = []
        y = width = 0
        for n in range(self.doc.page_count):
            rect = self.doc[n].rect
            self._tops.append(y)
            y += int(rect.height * self.scale) + PAGE_GAP
            width = max(width, int(rect.width * self.scale))
        self.setFixedSize(width, max(y - PAGE_GAP, 1))
        self.update()

    def fit(self, available):
        """Scale the pages to the width of the view, within reason.

        A page at 130 dpi is wider than the window, and reading a paper while
        scrolling sideways is miserable.
        """
        if self.doc is None or available < 200:
            return False
        scale = (available - 2 * SIDE_MARGIN) / self.doc[0].rect.width
        scale = max(0.5, min(scale, 2.5))
        if abs(scale - self.scale) < 0.01:
            return False
        self.scale = scale
        self.relayout()
        return True

    def page_at(self, y):
        """Which page a y position in this widget falls on."""
        for n in range(len(self._tops) - 1, -1, -1):
            if y >= self._tops[n]:
                return n
        return 0

    def rect_for(self, page, bbox):
        """A PDF rectangle as a rectangle in this widget."""
        x0, y0, x1, y1 = bbox
        top = self._tops[page]
        return QRectF(x0 * self.scale, top + y0 * self.scale,
                      (x1 - x0) * self.scale, (y1 - y0) * self.scale)

    def show_mark(self, page, boxes):
        self.mark = (page, list(boxes))
        self.update()

    def boxes_for(self, item):
        """Cached, because a click hit-tests every sentence on the page."""
        if item.index not in self._boxes:
            self._boxes[item.index] = self._find_boxes(item)
        return self._boxes[item.index]

    def _find_boxes(self, item):
        """Where a sentence sits on the page, line by line.

        The block box would tint a whole abstract at once. Searching for the
        sentence gives its own lines instead; if the search fails, which
        happens when a word was hyphenated across a line break, the block is
        the fallback.
        """
        if self.doc is None:
            return [item.bbox]
        page = self.doc[item.page]
        words = item.source.split()
        # The whole sentence first; then its opening and closing words, which
        # is what survives when something in the middle was hyphenated across
        # a line break and so is not on the page as written.
        for attempt in (words, words[:7], words[-7:]):
            phrase = " ".join(attempt).strip(" ,;:")
            if len(phrase) < 8:
                continue
            try:
                found = page.search_for(phrase)
            except Exception:
                found = []
            if found:
                return merge_lines(tuple(r) for r in found)
        return [item.bbox]

    def pixmap(self, n):
        """The page, rendered for this display.

        Qt lays out in logical pixels but paints on a screen that may have
        more of them. Rendering at the logical size and letting Qt stretch
        the result is what made the text look pixelated; rendering at
        scale * devicePixelRatio and telling the pixmap its ratio keeps it
        as sharp as the PDF itself.
        """
        ratio = self.devicePixelRatioF() or 1.0
        if self._pixmaps.get("ratio") != ratio:
            self._pixmaps = {"ratio": ratio}
        if n not in self._pixmaps:
            zoom = self.scale * ratio
            pix = self.doc[n].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            image = QImage(pix.samples, pix.width, pix.height, pix.stride,
                           QImage.Format.Format_RGB888)
            pm = QPixmap.fromImage(image.copy())
            pm.setDevicePixelRatio(ratio)
            self._pixmaps[n] = pm
        return self._pixmaps[n]

    def paintEvent(self, _event):
        if self.doc is None:
            return
        p = QPainter(self)
        area = self.visibleRegion().boundingRect()
        for n, top in enumerate(self._tops):
            height = int(self.doc[n].rect.height * self.scale)
            if top + height < area.top() or top > area.bottom():
                continue           # off screen: do not render it yet
            p.drawPixmap(0, top, self.pixmap(n))
        if self.mark:
            page, boxes = self.mark
            p.setPen(HIGHLIGHT_EDGE)
            p.setBrush(HIGHLIGHT)
            for box in boxes:
                p.drawRoundedRect(
                    self.rect_for(page, box).adjusted(-2, -2, 2, 2), 4, 4)
        p.end()

    def mousePressEvent(self, event):
        if self.doc is None:
            return
        y = event.position().y()
        page = self.page_at(y)
        point = QPoint(int(event.position().x() / self.scale),
                       int((y - self._tops[page]) / self.scale))
        self.clicked.emit(page, point)


class ReaderWindow(QMainWindow, chrome.Draggable):
    """Open a paper, read it aloud, follow along."""

    def __init__(self, voice_factory):
        super().__init__()
        self.voice_factory = voice_factory
        self.voice = None
        self.player = None
        self.items = []
        self.doc = None
        self.path = ""
        self._drag_from = None

        self.setWindowTitle("PDF Reader & AI Voice")
        self.setWindowIcon(chrome.app_icon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(chrome.QSS)
        self.resize(1040, 980)

        panel = chrome.Panel()
        panel.setObjectName("panel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(20, 16, 20, 18)
        outer.setSpacing(14)
        outer.addLayout(self.build_header())
        outer.addWidget(self.build_file_card())
        outer.addWidget(self.build_pages(), 1)
        outer.addLayout(self.build_transport())
        self.setCentralWidget(panel)

        self.follow = QTimer(self)
        self.follow.timeout.connect(self.follow_player)
        self.follow.setInterval(80)
        self.fit_timer = QTimer(self)      # resizing fires many events
        self.fit_timer.setSingleShot(True)
        self.fit_timer.setInterval(120)
        self.fit_timer.timeout.connect(self.fit_pages)
        self._shown = -1
        self._start_at = 0        # where Read begins, set by a click
        self._seeking = False

        # Space is what a reader reaches for; the arrows step a sentence.
        for key, action in (("Space", self.toggle),
                            ("Left", lambda: self.step(-1)),
                            ("Right", lambda: self.step(1)),
                            ("Home", self.restart),
                            ("Esc", self.close)):
            QShortcut(QKeySequence(key), self, activated=action)

    # -- the frame ---------------------------------------------------------
    def build_header(self):
        row = QHBoxLayout()
        row.setSpacing(12)
        badge = QLabel()
        badge.setPixmap(chrome.app_icon().pixmap(38, 38))
        row.addWidget(badge)

        words = QVBoxLayout()
        words.setSpacing(0)
        title = QLabel("PDF Reader & AI Voice")
        title.setObjectName("title")
        subtitle = QLabel("Read. Listen. Learn.")
        subtitle.setObjectName("subtitle")
        words.addWidget(title)
        words.addWidget(subtitle)
        row.addLayout(words)
        row.addStretch(1)

        for glyph, tip, action in (("\u2014", "Minimise", self.showMinimized),
                                   ("\u2715", "Close", self.close)):
            button = QPushButton(glyph)
            button.setObjectName("icon")
            button.setToolTip(tip)
            button.clicked.connect(action)
            row.addWidget(button)
        return row

    def build_file_card(self):
        frame = chrome.card()
        row = QHBoxLayout(frame)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)
        badge = QLabel("PDF")
        badge.setObjectName("pdfbadge")
        row.addWidget(badge)

        words = QVBoxLayout()
        words.setSpacing(2)
        self.filename = QLabel("No paper open")
        self.filename.setObjectName("filename")
        self.meta = QLabel("Open a PDF to begin")
        self.meta.setObjectName("meta")
        words.addWidget(self.filename)
        words.addWidget(self.meta)
        row.addLayout(words)
        row.addStretch(1)

        self.page_pill = QLabel("Page 0 / 0")
        self.page_pill.setObjectName("pill")
        row.addWidget(self.page_pill)
        self.open_button = QPushButton("Open a PDF")
        self.open_button.clicked.connect(self.choose_file)
        row.addWidget(self.open_button)
        return frame

    def build_pages(self):
        frame = QFrame()
        frame.setObjectName("paper")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(10, 10, 10, 10)
        self.view = PageView()
        self.view.clicked.connect(self.jump_to_point)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.view)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda _v: self.update_page_pill())
        inner.addWidget(self.scroll)
        return frame

    def build_transport(self):
        column = QVBoxLayout()
        column.setSpacing(10)

        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.sliderPressed.connect(
            lambda: setattr(self, "_seeking", True))
        self.progress.sliderReleased.connect(self.seek)
        column.addWidget(self.progress)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.speed = QComboBox()
        self.speed.addItems(SPEEDS)
        self.speed.setCurrentText("1.0x")
        self.speed.currentTextChanged.connect(self.change_speed)
        self.left_label = QLabel("")
        self.left_label.setObjectName("left")
        row.addWidget(self.speed)
        row.addWidget(self.left_label)
        row.addStretch(1)

        self.back_button = QPushButton("\u25c0\u25c0")
        self.back_button.setObjectName("step")
        self.back_button.setToolTip("Back one sentence (left arrow)")
        self.back_button.clicked.connect(lambda: self.step(-1))
        self.play_button = QPushButton("\u25b6")
        self.play_button.setObjectName("play")
        self.play_button.setToolTip("Read or pause (space)")
        self.play_button.clicked.connect(self.toggle)
        self.forward_button = QPushButton("\u25b6\u25b6")
        self.forward_button.setObjectName("step")
        self.forward_button.setToolTip("Forward one sentence (right arrow)")
        self.forward_button.clicked.connect(lambda: self.step(1))
        for button in (self.back_button, self.play_button, self.forward_button):
            button.setEnabled(False)
            row.addWidget(button)

        row.addStretch(1)
        self.right_label = QLabel("")
        self.right_label.setObjectName("right")
        row.addWidget(self.right_label)
        self.restart_button = QPushButton("Restart")
        self.restart_button.setToolTip("Read from the beginning (Home)")
        self.restart_button.setEnabled(False)
        self.restart_button.clicked.connect(self.restart)
        row.addWidget(self.restart_button)
        column.addLayout(row)

        # The status line lives at the bottom left; open() and the voice
        # loader write to it, so it keeps the name they use.
        self.status = self.left_label
        return column

    # -- the frameless window ---------------------------------------------
    def mousePressEvent(self, event):
        if not self._drag_press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._drag_move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_release()
        super().mouseReleaseEvent(event)

    # -- the transport -----------------------------------------------------
    def show_playing(self, playing):
        # Text glyphs, not the emoji forms, which render in their own colour
        self.play_button.setText("\u275a\u275a" if playing else "\u25b6")

    def seek(self):
        self._seeking = False
        if self.items:
            self.go_to(self.progress.value())

    def update_page_pill(self):
        """Which page is under the top of the view."""
        if not self.doc:
            return
        top = self.scroll.verticalScrollBar().value() + 40
        self.page_pill.setText(
            f"Page {self.view.page_at(top) + 1} / {self.doc.page_count}")

    # -- opening -----------------------------------------------------------
    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a paper", "", "PDF files (*.pdf)")
        if path:
            self.open(path)

    def open(self, path):
        self.stop()
        self.doc = document.open_document(path)
        lines = document.read_lines(self.doc)
        self.items = passages(lines)
        self.view.load(self.doc)
        self.fit_pages()
        if self.items:
            self.mark_item(self.items[0])
        words = sum(len(i.text.split()) for i in self.items)
        self.path = path
        self.filename.setText(os.path.basename(path))
        self.meta.setText(
            f"{chrome.readable_size(path)}  ·  {self.doc.page_count} pages"
            f"  ·  about {words / 150:.0f} min to read")
        self.progress.setRange(0, max(0, len(self.items) - 1))
        self.progress.setValue(0)
        self.progress.setEnabled(bool(self.items))
        self.update_page_pill()
        for button in (self.play_button, self.restart_button,
                       self.back_button, self.forward_button):
            button.setEnabled(bool(self.items))
        self._start_at = 0
        self.report()
        self.setWindowTitle(f"Read: {os.path.basename(path)}")
        log(f"reader: opened {path} ({words} words)")

    def fit_pages(self):
        """Scale pages to the window, keeping the marked sentence in view."""
        if self.view.fit(self.scroll.viewport().width())                 and getattr(self, "_marked", None):
            self.mark_item(self._marked, follow=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_timer.start()

    # -- playing -----------------------------------------------------------
    def speed_value(self):
        return float(self.speed.currentText().rstrip("x"))

    def ensure_voice(self):
        if self.voice is None:
            self.status.setText("Loading the voice...")
            self.repaint()
            self.voice = self.voice_factory()
            if self.voice is None:
                raise RuntimeError("no voice is available")
        return self.voice

    def toggle(self):
        if self.player is None:
            try:
                voice = self.ensure_voice()
            except Exception as e:
                self.status.setText(f"No voice: {e}")
                log(f"reader: voice unavailable: {type(e).__name__}: {e}")
                return
            self.player = Player(voice, self.items, speed=self.speed_value())
            self.player.start(self._start_at)
            self.follow.start()
            self.show_playing(True)
            self.report()
            return
        self.player.toggle()
        self.show_playing(self.player.playing)
        self.report()

    def change_speed(self, _text):
        if self.player is None:
            return
        at = self.player.position()[0]
        self.player.speed = self.speed_value()
        self.player.jump(at)        # the sentence is remade at the new speed

    def current_index(self):
        return self.player.position()[0] if self.player else self._start_at

    def stop(self):
        self.follow.stop()
        if self.player is not None:
            self.player.stop()
            self.player = None
        self.show_playing(False)

    def report(self):
        """Where the reading is, and how much of the paper is left."""
        if not self.items:
            return
        index = self.current_index()
        left = sum(len(i.text.split()) for i in self.items[index:])
        state = ("Reading" if self.player and self.player.playing
                 else "Paused" if self.player else "Ready")
        self.left_label.setText(
            f"{state}  ·  sentence {index + 1} of {len(self.items)}")
        self.right_label.setText(f"about {max(1, round(left / 150))} min left")
        if not self._seeking:
            self.progress.setValue(index)

    def follow_player(self):
        """Tint the sentence being read and keep it in view."""
        if self.player is None:
            return
        index, _elapsed = self.player.position()
        if index == self._shown:
            if not self.player.playing:
                self.show_playing(False)
            return
        self._shown = index
        self._start_at = index
        self.report()
        if 0 <= index < len(self.items):
            self.mark_item(self.items[index], follow=True)

    def mark_item(self, item, follow=False):
        """Tint one sentence, and bring it into view if asked."""
        boxes = self.view.boxes_for(item)
        self.view.show_mark(item.page, boxes)
        self._marked = item
        if follow:
            top = min(b[1] for b in boxes)
            bottom = max(b[3] for b in boxes)
            self.keep_in_view_bbox(item.page, (0, top, 1, bottom))

    def keep_in_view_bbox(self, page, bbox):
        rect = self.view.rect_for(page, bbox)
        bar = self.scroll.verticalScrollBar()
        top, bottom = bar.value(), bar.value() + self.scroll.viewport().height()
        margin = self.scroll.viewport().height() * 0.28
        if rect.top() < top + margin or rect.bottom() > bottom - margin:
            bar.setValue(int(rect.top() - margin))

    def jump_to_point(self, page, point):
        """Click a sentence to read from there.

        The sentence's own boxes are tried first; a paragraph's block box
        would pick its first sentence wherever in it you clicked.
        """
        x, y = point.x(), point.y()
        here = [i for i in self.items if i.page == page]

        def hit(boxes):
            return any(x0 <= x <= x1 and y0 - 1 <= y <= y1 + 1
                       for x0, y0, x1, y1 in boxes)

        # Sentences located on the page first. A sentence that could not be
        # found falls back to its block, and a block covers a whole paragraph,
        # so checking both together would let it swallow every click in it.
        for item in here:
            boxes = self.view.boxes_for(item)
            if boxes != [item.bbox] and hit(boxes):
                return self.go_to(item.index)
        for item in here:
            if hit([item.bbox]):
                return self.go_to(item.index)

    def go_to(self, index):
        """Read from one sentence, whether or not reading has started."""
        index = max(0, min(index, len(self.items) - 1))
        self._start_at = index
        self._shown = index
        self.mark_item(self.items[index], follow=True)
        if self.player is None:
            self.toggle()
        else:
            self.player.jump(index)
            self.show_playing(self.player.playing)
        self.report()

    def restart(self):
        self.go_to(0)

    def step(self, by):
        self.go_to(self.current_index() + by)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


def main():
    """Run the reader on its own: python -m livetranslator.ui.reader_window."""
    import sys

    from PySide6.QtWidgets import QApplication

    from ..reader.voice import Voice

    app = QApplication(sys.argv)
    window = ReaderWindow(Voice)
    window.show()
    if len(sys.argv) > 1:
        window.open(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
