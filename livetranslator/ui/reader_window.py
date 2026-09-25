"""The reader window: the paper as it looks, read aloud, highlighted as it goes.

The page is shown as the real PDF rather than extracted text, because a
student is reading a paper, not a transcript of one. The sentence being
spoken is tinted on the page and the view scrolls to keep it visible.

Pages are rendered once, lazily, and cached. A 12-page paper at 130 dpi is a
few megabytes; rendering every page up front would stall the open.
"""
import os
import re

import pymupdf
from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from ..log import log
from . import reader_chrome as chrome
from ..reader import document
from ..reader.player import Player, passages
from ..reader.translated import Translation

DPI = 130
PAGE_GAP = 14
SIDE_MARGIN = 10
# Filling a 1400-pixel window would render a page at 2.3x, with body text
# larger than a heading. Acrobat, reading the same paper, settles near 1.6x,
# so the page stops growing there and is centred in whatever is left over.
READABLE = 1.7
HIGHLIGHT = QColor(56, 189, 248, 62)       # the app's cyan, kept light
HIGHLIGHT_EDGE = QColor(56, 189, 248, 90)
SPEEDS = ("0.8x", "0.9x", "1.0x", "1.1x", "1.25x", "1.5x")
# What the language box offers: the paper as written, or spoken in
# another language while the English stays tinted on the page.
READ_IN = (("As written", "en"), ("\u4e2d\u6587", "zt"))



def squash(text):
    """Letters and digits only, lowercased.

    Highlighting compares this rather than the text itself, so a sentence
    still matches the page when it was hyphenated across a line break, when
    the reader added a full stop to a title, or when the two differ only in
    quotation marks and spacing.
    """
    return "".join(c.lower() for c in text if c.isalnum())


def rows_of(boxes):
    """Characters that run along one line, as one rectangle per line.

    The boxes arrive in reading order, so a line ends where the next
    character is on a different line or starts a long way to the right --
    which is what the gutter of a two-column paper looks like, and which
    must not be bridged by a single highlight.
    """
    rows = []
    for x0, y0, x1, y1 in boxes:
        if rows:
            row = rows[-1]
            height = min(y1 - y0, row[3] - row[1])
            level = min(y1, row[3]) - max(y0, row[1]) > 0.6 * height
            # Kerning lets a wide glyph ("T") overlap the letter after it,
            # so a small backward step is still the same line.
            next_to = -0.5 * height <= x0 - row[2] < 3 * height
            if level and next_to:
                row[0], row[1] = min(row[0], x0), min(row[1], y0)
                row[2], row[3] = max(row[2], x1), max(row[3], y1)
                continue
        rows.append([x0, y0, x1, y1])
    return [tuple(r) for r in rows]


def text_map(page, body_size=document.BODY_SIZE):
    """The page's letters and digits in reading order, each with its box.

    Built the way the reading plan was built: the small raised numbers that
    block_text() keeps out of the spoken sentence are kept out here too, so
    "Wei Qi Koh, PhD" matches a page that prints "Wei Qi Koh1, PhD".
    """
    stream, boxes = [], []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                chars = span.get("chars", ())
                text = "".join(c["c"] for c in chars)
                if (span["size"] < body_size * 0.8
                        and re.fullmatch(r"[\d,\s*\u2020]+", text.strip())):
                    continue
                for c in chars:
                    if c["c"].isalnum():
                        stream.append(c["c"].lower())
                        boxes.append(tuple(c["bbox"]))
    return "".join(stream), boxes


class PageView(QWidget):
    """The pages, stacked vertically, with one sentence tinted."""

    clicked = Signal(int, QPoint)      # page number, point in PDF coordinates

    def __init__(self):
        super().__init__()
        self.doc = None
        self.scale = DPI / 72.0
        self._pixmaps = {}
        self._boxes = {}
        self._maps = {}
        self._tops = []                # y of each page in this widget
        self.mark = None               # (page, [boxes in PDF points])
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def load(self, doc):
        self.doc = doc
        self._maps.clear()
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

    def fit(self, width, height=None, mode="width"):
        """Scale the pages to the view.

        "width" is how a paper is actually read: the page fills the width, up
        to the point where the text stops getting easier to read, and you
        scroll. "page" shows a whole page at once, which is useful to see
        where you are but leaves the text small on a laptop screen.
        """
        if self.doc is None or width < 200:
            return False
        first = self.doc[0].rect
        scale = (width - 2 * SIDE_MARGIN) / first.width
        if mode == "page" and height:
            scale = min(scale, (height - 2 * SIDE_MARGIN) / first.height)
        else:
            scale = min(scale, READABLE)
        scale = max(0.2, min(scale, 2.5))
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

    def text_map(self, page):
        if page not in self._maps:
            self._maps[page] = text_map(self.doc[page])
        return self._maps[page]

    def _find_boxes(self, item):
        """Where a sentence sits on the page, line by line.

        The block box would tint a whole abstract at once, so the sentence is
        looked for among the page's own characters. Comparing squashed text
        finds it even when the page breaks a word across two lines; when it
        genuinely is not there -- a sentence continued from the page before
        -- the block is the fallback.
        """
        if self.doc is None:
            return [item.bbox]
        stream, boxes = self.text_map(item.page)
        wanted = squash(item.source)
        if len(wanted) < 6:
            return [item.bbox]
        at = self._nearest(stream, wanted, boxes, item.bbox)
        if at < 0:
            return [item.bbox]
        return rows_of(boxes[at:at + len(wanted)])

    @staticmethod
    def _nearest(stream, wanted, boxes, bbox):
        """The occurrence closest to where the reading plan said it was.

        A short sentence ("Results.") can appear on a page several times, and
        the highlight has to land on the one being read.
        """
        best, distance, at = -1, None, stream.find(wanted)
        while at >= 0:
            gap = abs(boxes[at][1] - bbox[1])
            if distance is None or gap < distance:
                best, distance = at, gap
            at = stream.find(wanted, at + 1)
        return best

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
        self.setMinimumSize(560, 420)

        panel = chrome.Panel()
        panel.setObjectName("panel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)
        outer.addLayout(self.build_header())
        outer.addWidget(self.build_file_card())
        outer.addWidget(self.build_pages(), 1)
        outer.addLayout(self.build_transport())
        self.setCentralWidget(panel)
        self.fit_to_screen()

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
        self.into = None       # a Translation once another language is picked
        self.warning = ""
        self.fit_mode = "width"   # the fit button switches to "page"

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

        self.language = chrome.Chooser()
        self.language.setObjectName("lang")
        globe = chrome.globe_icon()
        for name, _code in READ_IN:
            self.language.addItem(globe, name)
        self.language.setToolTip("Read the paper aloud in this language")
        self.language.currentIndexChanged.connect(self.change_language)
        row.addWidget(self.language)

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
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)
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
        self.fit_button = QPushButton("Whole page")
        self.fit_button.setToolTip("Show a whole page at once, or fill the width")
        self.fit_button.clicked.connect(self.switch_fit)
        row.addWidget(self.fit_button)
        self.open_button = QPushButton("Open a PDF")
        self.open_button.clicked.connect(self.choose_file)
        row.addWidget(self.open_button)
        return frame

    def build_pages(self):
        frame = self.paper = QFrame()
        frame.setObjectName("paper")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(6, 6, 6, 6)
        self.view = PageView()
        self.view.clicked.connect(self.jump_to_point)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.view)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda _v: self.update_page_pill())
        inner.addWidget(self.scroll)

        # Stretches either side so the card centres when it is narrowed to
        # wrap a page, and still fills the window when it is not. Aligning
        # the card instead would stop it expanding at all.
        holder = QWidget()
        row = self.paper_row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(frame, 10)
        row.addStretch(1)
        return holder

    def build_transport(self):
        """Two rows: where the reading is, then what it is doing.

        The reference design flanks the progress track with the times and
        centres the controls under it. Putting the status text on the track
        row is what makes that work: it used to sit beside the speed, and a
        line as long as "sentence 141 of 329, Chinese, offline translation"
        pushed the play button well off centre.
        """
        column = QVBoxLayout()
        column.setSpacing(9)

        track = QHBoxLayout()
        track.setSpacing(12)
        self.left_label = QLabel("")
        self.left_label.setObjectName("left")
        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setMinimumWidth(260)
        self.progress.sliderPressed.connect(
            lambda: setattr(self, "_seeking", True))
        self.progress.sliderReleased.connect(self.seek)
        self.right_label = QLabel("")
        self.right_label.setObjectName("right")
        track.addWidget(self.left_label)
        track.addWidget(self.progress, 1)
        track.addWidget(self.right_label)
        column.addLayout(track)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.speed = chrome.Chooser()
        self.speed.addItems(SPEEDS)
        self.speed.setCurrentText("1.0x")
        self.speed.setToolTip("How fast the voice reads")
        self.speed.currentTextChanged.connect(self.change_speed)
        row.addWidget(self.speed)
        row.addStretch(1)

        self.back_button = chrome.TransportButton("prev")
        self.back_button.setToolTip("Back one sentence (left arrow)")
        self.back_button.clicked.connect(lambda: self.step(-1))
        self.play_button = chrome.TransportButton("play", primary=True)
        self.play_button.setToolTip("Read or pause (space)")
        self.play_button.clicked.connect(self.toggle)
        self.forward_button = chrome.TransportButton("next")
        self.forward_button.setToolTip("Forward one sentence (right arrow)")
        self.forward_button.clicked.connect(lambda: self.step(1))
        for button in (self.back_button, self.play_button, self.forward_button):
            button.setEnabled(False)
            row.addWidget(button)

        row.addStretch(1)
        self.restart_button = QPushButton("Restart")
        self.restart_button.setToolTip("Read from the beginning (Home)")
        self.restart_button.setEnabled(False)
        self.restart_button.clicked.connect(self.restart)
        row.addWidget(self.restart_button)
        column.addLayout(row)

        # open() and the voice loader write here, so it keeps the name they use
        self.status = self.left_label
        return column

    def fit_to_screen(self, want=(1400, 960)):
        """Open on most of the screen, but never taller than it.

        A frameless window is not fitted by Windows, so asking for 980 on a
        screen with 816 usable pixels put the controls below the taskbar.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        width = min(want[0], int(area.width() * 0.96))
        height = min(want[1], int(area.height() * 0.96))
        self.resize(width, height)
        self.move(area.x() + (area.width() - width) // 2,
                  area.y() + (area.height() - height) // 2)

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
        self.play_button.set_kind("pause" if playing else "play")

    def seek(self):
        self._seeking = False
        if self.items:
            self.go_to(self.progress.value())

    def switch_fit(self):
        """Swap between filling the width and showing a whole page."""
        self.fit_mode = "page" if self.fit_mode == "width" else "width"
        self.fit_button.setText(
            "Fill width" if self.fit_mode == "page" else "Whole page")
        self.fit_pages()

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
        view = self.scroll.viewport()
        # Both modes measure the window, not the viewport. The card's width
        # is set from the result, so measuring the viewport would feed back:
        # a narrower card, a smaller page, a narrower card again. It also
        # lags a mode change by one pass, because the card is still the old
        # width when the fit runs.
        room = self.centralWidget().width() - 64
        changed = self.view.fit(room, view.height(), self.fit_mode)
        # Either mode can leave width to spare: a whole page always does, and
        # a wide window does once the page has stopped growing. The card then
        # wraps the page and the stretches either side centre it.
        if self.doc is not None:
            card = int(self.doc[0].rect.width * self.view.scale) + 26
            spare = card < room
            self.paper_row.setStretch(0, 1 if spare else 0)
            self.paper_row.setStretch(2, 1 if spare else 0)
            self.paper.setMaximumWidth(card if spare else 16777215)
        if changed and getattr(self, "_marked", None):
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
            self.player = Player(voice, self.items, speed=self.speed_value(),
                                 into=self.into)
            self.player.start(self._start_at)
            self.follow.start()
            self.show_playing(True)
            self.report()
            return
        self.player.toggle()
        self.show_playing(self.player.playing)
        self.report()

    # -- reading it in another language ------------------------------------
    def language_code(self):
        return READ_IN[self.language.currentIndex()][1]

    def build_translation(self, code):
        """The engine to translate with, and what to warn about it.

        The offline packs are what this machine has, and on the long
        sentences of a journal they drop clauses: one test sentence came back
        as "the technology that older adults chose," and nothing else. They
        are still offered, because a rough gist offline beats nothing, but
        the status line has to say so rather than let a student believe they
        have heard the paper.
        """
        from .. import settings
        from ..translate import build_cloud_engine, build_engine

        if settings.ai_enabled():
            config = settings.load()
            engine = build_cloud_engine(config["ai_provider"], config["ai_key"])
            return Translation(code, engine), ""
        return (Translation(code, build_engine()),
                "offline translation, rough on long sentences")

    def change_language(self, _index):
        """Switch languages, carrying on from the sentence being read."""
        code = self.language_code()
        if code == "en":
            self.into, self.warning = None, ""
        else:
            self.status.setText("Getting the translator ready...")
            self.repaint()
            try:
                self.into, self.warning = self.build_translation(code)
                self.language.setToolTip(
                    self.warning or "Read the paper aloud in this language")
            except Exception as e:
                self.language.setCurrentIndex(0)
                self.into, self.warning = None, ""
                self.status.setText(f"No translation: {e}")
                log(f"reader: no translation: {type(e).__name__}: {e}")
                return
        if self.player is not None:
            at = self.player.position()[0]
            self.stop()
            self.toggle()          # a new player, reading in the new voice
            self.go_to(at)
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
        if self.into is not None:
            said = {code: name for name, code in READ_IN}[self.language_code()]
            note = "  \u00b7  rough offline translation" if self.warning else ""
            self.left_label.setText(
                self.left_label.text() + f"  \u00b7  {said}{note}")
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
