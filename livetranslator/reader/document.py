"""Turn a PDF into what a voice should read, in the order it should read it.

A journal page is not a stream of text. It has two columns, a running header,
a footer, affiliations, a table laid out as loose rows, and a reference list
nobody wants read aloud. This module decides, for every block on every page,
whether to read it and why, so the decision can be inspected and drawn back
onto the page rather than taken on trust.

The rules were built against a JMIR paper. Other publishers lay pages out
differently, so treat them as a starting point, not a finished job.
"""
import re
from dataclasses import dataclass

import pymupdf

# Where the article body ends. Everything from here on is back matter.
STOP_HEADINGS = ("acknowledgments", "acknowledgements", "references",
                 "data availability", "conflicts of interest", "abbreviations",
                 "funding", "authors' contributions", "authors’ contributions")
# A hyphen at a line end is a split word ("plat-\nforms") unless the first half
# is one of these, in which case it is a real compound ("self-\nmonitoring").
KEEP_HYPHEN = ("well", "self", "long", "non", "co", "multi", "semi", "cross",
               "short", "in", "low", "high", "full", "part", "real", "old")
DEGREES = r",?\s*\b(PhD|MD|MSc|MPH|MBBS|BSc|MA|MS|RN|DPhil|Prof)\b\.?"
CITATION = re.compile(r"\s*\[(\d+(?:\s*[,–\-]\s*\d+)*)\]")
BODY_SIZE = 10.0        # what ordinary text is set in, for these journals
HEADING_SIZE = 14.5
SUBHEADING_SIZE = 12.5
HEADER_BAND = 62        # points from the top that hold the running header
FOOTER_BAND = 60        # and from the bottom, the footer and page number
# Publishers put very different things in a footer, at very different heights:
# a download line, a copyright, a journal and volume, a page number. What they
# have in common is repetition, so anything near the top or bottom edge that
# says the same thing on several pages is treated as furniture.
EDGE_BAND = 0.14        # share of the page height counted as near an edge
REPEATS = 0.4           # ...on at least this share of the pages
# Affiliations on the front page and a table's own rows are both set at 8.5
# point in this journal, so one threshold cannot tell them apart. The front
# page is stricter: everything small there is front matter. In the body, only
# true footnote sizes are dropped, which leaves table captions readable.
SMALL_PRINT_FRONT = 9
SMALL_PRINT_BODY = 8

READ, SKIP, STOP = "read", "skip", "stop"


@dataclass
class Line:
    """One block of the page, and what the reader decided about it."""
    page: int
    bbox: tuple
    action: str
    reason: str
    text: str = ""


def join_lines(lines):
    """Rebuild running text from wrapped lines.

    A PDF gives each line separately and with no trailing space, so joining
    them naively produces "arebecoming" and "aged55".
    """
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
        elif out.endswith("-") and ln[:1].islower():
            stem = re.search(r"(\w+)-$", out)
            if stem and stem.group(1).lower() in KEEP_HYPHEN:
                out += ln
            else:
                out = out[:-1] + ln
        else:
            out += " " + ln
    return re.sub(r"\s+", " ", out).strip()


def block_text(block, body_size=BODY_SIZE):
    """The text of a block, without superscripts, with bold labels set apart.

    An abstract runs "Background: ... Objectives: ..." with the labels in
    bold. Each becomes its own short sentence so the voice pauses there.
    """
    lines = []
    for line in block["lines"]:
        parts = []
        for s in line["spans"]:
            t = s["text"]
            if not t.strip():
                parts.append(t)
                continue
            if (s["size"] < body_size * 0.8
                    and re.fullmatch(r"[\d,\s*†]+", t.strip())):
                continue            # affiliation and footnote markers
            if s["flags"] & 16 and t.strip().endswith(":"):
                t = f"\n{t.strip()[:-1]}.\n"
            parts.append(t)
        lines.append("".join(parts))
    text = "\n".join(lines)
    chunks = re.split(r"\n(?=[A-Z][^\n]{0,30}\.\n)|(?<=\.)\n", text)
    return " ".join(c for c in (join_lines(p.split("\n")) for p in chunks) if c)


def dominant_size(block):
    """The size most of a block's characters are set in."""
    sizes = {}
    for line in block["lines"]:
        for s in line["spans"]:
            if s["text"].strip():
                key = round(s["size"], 1)
                sizes[key] = sizes.get(key, 0) + len(s["text"])
    return max(sizes, key=sizes.get) if sizes else 0


def _authors(text):
    """"Wei Qi Koh1, PhD; Kristiana Ludlow2,3, PhD" -> "By Wei Qi Koh and ..."."""
    names = [n for n in (re.sub(DEGREES, "", n).strip(" ,")
                         for n in text.split(";")) if n]
    joined = (", ".join(names[:-1]) + ", and " + names[-1]
              if len(names) > 1 else names[0])
    return f"By {joined}."


def _shape(text):
    """The text with its numbers removed, so page numbers match each other."""
    return re.sub(r"\d+", "#", " ".join(text.split()).lower())


def furniture(doc, sample=None):
    """Lines near the top or bottom edge that repeat across the pages."""
    pages = range(doc.page_count if sample is None else min(sample, doc.page_count))
    seen = {}
    for pno in pages:
        page = doc[pno]
        height = page.rect.height
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            x0, y0, x1, y1 = block["bbox"]
            if y1 > height * EDGE_BAND and y0 < height * (1 - EDGE_BAND):
                continue           # not near an edge
            shape = _shape(block_text(block))
            if len(shape) > 3:
                seen.setdefault(shape, set()).add(pno)
    least = max(2, int(len(pages) * REPEATS))
    return {shape for shape, pages_seen in seen.items()
            if len(pages_seen) >= least}


def plan(doc):
    """Every block of the document, in reading order, with its decision."""
    rows = []
    repeated = furniture(doc)
    stopped = after_title = skip_contact = in_table = False
    for pno in range(doc.page_count):
        page = doc[pno]
        height = page.rect.height
        blocks = [b for b in page.get_text("dict")["blocks"]
                  if b.get("type") == 0]
        sizes = [dominant_size(b) for b in blocks]
        title_size = max(sizes) if pno == 0 and sizes else None

        for block, size in zip(blocks, sizes):
            x0, y0, x1, y1 = block["bbox"]
            text = block_text(block)
            low = text.lower()

            def add(action, reason, spoken=""):
                rows.append(Line(pno, (x0, y0, x1, y1), action, reason, spoken))

            # Page furniture first: it is skipped for its own reason, even on
            # a page where the article body has already ended.
            near_top = y1 < height * EDGE_BAND
            near_bottom = y0 > height * (1 - EDGE_BAND)
            if (near_top or near_bottom) and _shape(text) in repeated:
                add(SKIP, "running header" if near_top
                    else "footer, repeated on every page")
                continue
            if y1 < HEADER_BAND:
                add(SKIP, "running header")
                continue
            if y0 > height - FOOTER_BAND:
                add(SKIP, "footer and page number")
                continue
            if stopped:
                add(SKIP, "after the end of the article body")
                continue
            # A back-matter heading shares a block with the paragraph under
            # it, so match the start of the block rather than all of it.
            if any(low.startswith(h) for h in STOP_HEADINGS):
                stopped = True
                add(STOP, "back matter starts here: reading ends")
                continue

            if pno == 0:
                if size == title_size:
                    after_title = True
                    add(READ, "title", text.rstrip(".") + ".")
                    continue
                if not after_title:
                    add(SKIP, "article type label")
                    continue
                if ";" in text and re.search(DEGREES, text):
                    add(READ, "authors, without degrees or affiliation marks",
                        _authors(text))
                    continue
                if low.startswith("corresponding author"):
                    skip_contact = True
                    add(SKIP, "contact details")
                    continue
                if skip_contact or "email:" in low or "phone:" in low:
                    skip_contact = False
                    add(SKIP, "contact details: address, phone, email")
                    continue
                if "doi:" in low or re.search(r"\b\d{4};\d+:e?\d+", text):
                    add(SKIP, "citation line and DOI")
                    continue

            if size < (SMALL_PRINT_FRONT if pno == 0 else SMALL_PRINT_BODY):
                add(SKIP, "small print: affiliations or footnotes")
                continue
            if re.match(r"^Table \d+\.", text):
                in_table = True
                add(READ, "table caption; rows skipped",
                    text.split(".")[0] + ", skipped.")
                continue
            if in_table and size <= BODY_SIZE and len(text) < 90:
                add(SKIP, "table row")
                continue
            in_table = False

            role = ("heading" if size >= HEADING_SIZE
                    else "subheading" if size >= SUBHEADING_SIZE else "text")
            spoken = CITATION.sub("", text)
            if role != "text" and not spoken.endswith((".", ":", "?")):
                spoken += "."          # a pause after a heading
            add(READ, role, spoken)
    return rows


def open_document(path):
    return pymupdf.open(path)


def read_lines(doc):
    """Only the blocks to be read aloud, in order."""
    return [r for r in plan(doc) if r.action == READ]


def draw(doc, rows, page_number, out_png, dpi=110):
    """Mark the plan onto a copy of one page: green read, grey skipped, red stop.

    For checking a new publisher's layout by eye, which is the only way to
    know whether these rules fit it.
    """
    marked = pymupdf.open()
    marked.insert_pdf(doc, from_page=page_number, to_page=page_number)
    page = marked[0]
    order = 0
    for row in rows:
        if row.page != page_number:
            continue
        x0, y0, x1, y1 = row.bbox
        colour = {READ: (0.13, 0.62, 0.35), SKIP: (0.55, 0.55, 0.55),
                  STOP: (0.85, 0.2, 0.2)}[row.action]
        page.draw_rect(pymupdf.Rect(x0 - 2, y0 - 1, x1 + 2, y1 + 1),
                       color=colour, fill=colour, fill_opacity=0.14, width=1.2)
        if row.action == READ:
            order += 1
            label = f"{order}. {row.reason}"
        else:
            label = f"{row.action}: {row.reason}"
        page.insert_text((x0, max(9, y0 - 2.5)), label, fontsize=6.5,
                         color=colour)
    page.get_pixmap(dpi=dpi).save(out_png)
    return out_png
