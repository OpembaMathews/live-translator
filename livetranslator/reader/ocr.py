"""Reading a paper that has no text in it.

Some PDFs hold no text at all: a scan is a photograph of a page, and a file
printed to PDF can have every letter turned into a filled shape. Both look
perfect and say nothing to a program, so the reader had nothing to read.

What happens here is not "extract the words". It is "put the words back".
Each page is rendered, recognised, and the result written into a copy of the
PDF as invisible text sitting exactly where the visible marks are. Everything
downstream then works unchanged -- the heading rules, the furniture
detection, the character-level highlighting -- because from that point on it
is simply a PDF with text in it.

The fit is deliberate: the recogniser runs on ONNX Runtime, which the app
already carries for the voice, so this adds no second way of running models
and nothing that needs PyTorch.
"""
import hashlib
import os
import re

from ..log import log
from ..paths import APP_DIR

CACHE = os.path.join(APP_DIR, "recognised")
# 200 dpi reads cleanly and costs about twelve seconds a page here. 300 is
# barely better on text this size and takes half as long again.
DPI = 200
# Below this the recogniser is guessing, and a guess read aloud with
# confidence is worse than a gap.
MIN_CONFIDENCE = 0.5
_engine = None


def available():
    """Whether the recogniser is installed. It is an optional extra: most
    papers have text in them and should not pay 140 MB for the ones that
    do not."""
    import importlib.util

    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def engine():
    """One recogniser, loaded once: it costs a few seconds to start."""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
        log("ocr: recogniser loaded")
    return _engine


def cache_path(pdf_path):
    """Where the readable copy of this file lives.

    Keyed by what the file is as well as where it is, so editing a paper and
    saving it under the same name does not quietly reuse the old reading.
    """
    try:
        stat = os.stat(pdf_path)
        stamp = f"{pdf_path}|{stat.st_size}|{int(stat.st_mtime)}"
    except OSError:
        stamp = pdf_path
    name = hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE, f"{name}.pdf")


def page_lines(page, dpi=DPI):
    """Recognise one page. Boxes come back in PDF points, not pixels."""
    import numpy as np
    import pymupdf

    pix = page.get_pixmap(dpi=dpi)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n)
    found, _timing = engine()(image[:, :, :3])
    scale = 72.0 / dpi
    lines = []
    for box, text, confidence in (found or []):
        if confidence < MIN_CONFIDENCE or not text.strip():
            continue
        xs = [corner[0] * scale for corner in box]
        ys = [corner[1] * scale for corner in box]
        lines.append((pymupdf.Rect(min(xs), min(ys), max(xs), max(ys)),
                      text, float(confidence)))
    # Reading order: down the page, then across. The recogniser returns
    # boxes in its own order, which is not always either.
    lines.sort(key=lambda line: (round(line[0].y0, 1), line[0].x0))
    return lines


def write_layer(page, lines):
    """Lay the recognised words over the marks they were read from.

    The size is chosen so each line spans the width of its own box. That
    matters beyond tidiness: the highlight is drawn from the position of
    each character, so invisible text that does not sit on the visible text
    would highlight the wrong part of the page.
    """
    import pymupdf

    written = 0
    for rect, text, _confidence in lines:
        if rect.width <= 1 or rect.height <= 1:
            continue
        unit = pymupdf.get_text_length(text, fontname="helv", fontsize=1)
        if unit <= 0:
            continue
        size = max(1.0, min(rect.width / unit, rect.height * 1.4))
        # render_mode 3 draws nothing: the page looks exactly as it did.
        page.insert_text((rect.x0, rect.y1 - rect.height * 0.22), text,
                         fontname="helv", fontsize=size, render_mode=3)
        written += 1
    return written


SQUASHED = re.compile(r"([a-z])([A-Z])")
AFTER_DIGIT = re.compile(r"(\d)([A-Z][a-z])")


WORD = re.compile(r"[A-Za-z]+")
# The commonest English words, which a paper may never happen to use on
# their own even though its sentences are full of them. "say" does not
# appear alone anywhere in a paper about the digital divide, so without
# this "Asmyfatherusedtosay" cannot be taken apart.
GLUE = set("""
a an the this that these those and or but not nor so yet for of in on at to
from by with without within into onto over under above below between among
through during before after since until while is are was were be been being
am do does did doing have has had having can could will would shall should
may might must as if then than when where why how what which who whom whose
all any both each few more most other some such no only own same too very
one two three four five six seven eight nine ten first second next last
i me my we us our you your he him his she her it its they them their
there here out up down off again once about against because though although
said say says tell told take takes took make makes made go goes going went
come comes came get gets got give gives gave know knows knew think thinks
see sees saw want wants need needs use uses used work works worked
people person man woman child children family father mother sister brother
day time year week month home school life world part way thing things
good new old long great little big small high low right left early late
also just even still back well much many lite now today
""".split())


def vocabulary(texts):
    """The words this document uses, from the lines that came out whole.

    A paper is its own best dictionary. "sociocultural" and "factors" both
    appear properly spaced elsewhere in it, and that is what makes
    "socioculturalfactors" splittable without shipping a word list.
    """
    seen = {}
    for text in texts:
        for word in WORD.findall(text):
            if len(word) >= 2:
                low = word.lower()
                seen[low] = seen.get(low, 0) + 1
    return seen


def _decompose(low, known, depth):
    """Every piece a word the document uses, or None if it will not divide.

    Worked out from the end backwards and remembered, because whole
    sentences come back with every space missing -- "Asmyfatherusedtosay"
    is six words, and trying each cut independently would take longer than
    the recognition did.
    """
    length = len(low)
    # best[at] is the decomposition of low[at:], or None
    best = [None] * (length + 1)
    best[length] = []
    for at in range(length - 1, -1, -1):
        whole = low[at:]
        if used_whole(whole, known) or whole in GLUE:
            best[at] = [whole]
            continue
        choice = None
        for cut in range(at + 2, length + 1):
            piece = low[at:cut]
            if not (used_whole(piece, known) or piece in GLUE):
                continue
            # A very short piece has to be a word in its own right, not a
            # fragment the paper happens to contain once. "performed"
            # became "per form ed" and "conditions in" became "condition
            # sin" because "ed" and "sin" were technically present.
            if len(piece) <= 3 and piece not in GLUE and known.get(piece, 0) < 3:
                continue
            rest = best[cut]
            if rest is None:
                continue
            if len(rest) + 1 > depth:
                continue
            # Fewest pieces first, then the evenest. Preferring evenness
            # alone turned "used" into "us ed" and "performed" into
            # "per form ed": both are even, and both are wrong.
            # Fewest pieces, then the ones the paper actually uses most.
            # Counting commonness breaks the ties that length alone cannot:
            # "conditions in" and "condition sin" are both two pieces of the
            # same lengths, and only one of them is English.
            shortest = min([len(piece)] + [len(r) for r in rest])
            common = min([known.get(piece, 0)]
                         + [known.get(r, 0) for r in rest])
            score = (-(len(rest) + 1), common, shortest)
            if choice is None or score > choice[0]:
                choice = (score, [piece] + rest)
        best[at] = choice[1] if choice else None
    return best[0]


# Past this, a run of letters is almost certainly several words. The
# longest words in an academic paper -- "interculturality",
# "epistemologies" -- sit just under it.
LONGEST_REAL_WORD = 17


def used_whole(low, known):
    """Whether the document really uses this as a word.

    The vocabulary is built from the recogniser's own output, so it contains
    the recogniser's own mistakes: "asmyfatherusedtosay" appears in it once,
    and a word that counts as known is never taken apart. A long run seen
    only once is treated as the mistake it almost certainly is.
    """
    if low not in known:
        return False
    if len(low) <= LONGEST_REAL_WORD:
        return True
    return known[low] >= 2


def split_word(word, known, depth=12):
    """One run-together word as its parts, or None to leave it alone.

    Only words the document never uses whole are touched, and only where
    every piece is a word it does use. Anything weaker invents mistakes
    rather than fixing them.
    """
    low = word.lower()
    if len(low) < 5 or used_whole(low, known):
        return None
    pieces = _decompose(low, known, depth)
    if pieces is None or len(pieces) < 2:
        return None
    # Put the original capitals back: the pieces are the same letters.
    out, at = [], 0
    for piece in pieces:
        out.append(word[at:at + len(piece)])
        at += len(piece)
    return out


# Two short words run together where no English word can be the result.
# These are the recogniser's habits rather than a document's vocabulary,
# and they appear often enough to be in it, which is why frequency cannot
# be used to spot them.
ALWAYS = {
    "ofthe": "of the", "inthe": "in the", "andthe": "and the",
    "tothe": "to the", "forthe": "for the", "onthe": "on the",
    "atthe": "at the", "isthe": "is the", "asthe": "as the",
    "bythe": "by the", "thatthe": "that the", "withthe": "with the",
    "fromthe": "from the", "ofa": "of a", "ina": "in a", "toa": "to a",
    "ofthis": "of this", "inthis": "in this", "ofour": "of our",
    "andin": "and in", "andto": "and to", "andof": "and of",
    "ofan": "of an", "isa": "is a", "wasa": "was a", "area": "are a",
}


def space_out(text, known=None):
    """Put back spaces the recogniser ran together.

    It returns "ofthe" and "2.3SocioculturalPerspective" often enough to
    matter, because the voice reads those as words rather than as the words
    they are. Two unambiguous signals are used first -- a lower-case letter
    against an upper-case one, and a heading number against its first word
    -- and then, where the document's own vocabulary is known, run-together
    lower-case words are split using it.
    """
    text = AFTER_DIGIT.sub(r"\1 \2", text)
    text = SQUASHED.sub(r"\1 \2", text)
    if not known:
        return text

    def mend(match):
        word = match.group(0)
        fixed = ALWAYS.get(word.lower())
        if fixed:
            # keep the original capital, if there was one
            return fixed.capitalize() if word[0].isupper() else fixed
        parts = split_word(word, known)
        return " ".join(parts) if parts else word

    return WORD.sub(mend, text)


def recognise(pdf_path, on_progress=None, on_step=None, stop=None, dpi=DPI):
    """Produce a copy of the PDF with a text layer, and return its path.

    A recognised copy already on disk is reused: this is minutes of work and
    nobody should pay for it twice.
    """
    import pymupdf

    out = cache_path(pdf_path)
    if os.path.exists(out):
        log(f"ocr: using the copy recognised earlier, {os.path.basename(out)}")
        return out

    step = on_step or (lambda text: None)
    doc = pymupdf.open(pdf_path)
    total = doc.page_count

    # Every page is read before anything is written, because the spacing is
    # mended using the whole document's vocabulary and that is not known
    # until the last page has been seen.
    per_page = []
    for number in range(total):
        if stop is not None and stop.is_set():
            doc.close()
            raise Stopped()
        step(f"Reading page {number + 1} of {total}")
        per_page.append(page_lines(doc[number], dpi))
        if on_progress:
            on_progress(f"page {number + 1} of {total}", number + 1, total)

    known = vocabulary(text for lines in per_page
                       for _rect, text, _confidence in lines)
    step("Tidying up the spacing")
    found = 0
    for number, lines in enumerate(per_page):
        mended = [(rect, space_out(text, known), confidence)
                  for rect, text, confidence in lines]
        found += write_layer(doc[number], mended)

    os.makedirs(CACHE, exist_ok=True)
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    log(f"ocr: recognised {total} pages of {os.path.basename(pdf_path)}, "
        f"{found} lines of text")
    return out


class Stopped(Exception):
    """Recognition was asked to stop. Nothing is written."""
