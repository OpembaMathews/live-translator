"""Transcripts: everything heard and shown in a session, kept for later.
"""

import os
import threading
import time
import json as _json
from .config import LANG_SHORT
from .log import log
from .paths import SESSION_DIR


class SessionLog:
    """Everything said and shown in one run, written as it happens.

    Two files per session. The .jsonl is appended a line at a time and
    flushed, so a session survives the app being closed or crashing; the
    .html is rebuilt from it and is the one to actually read. Nothing is
    created until the first line is spoken, so launching the app and closing
    it again leaves no clutter.
    """

    def __init__(self, folder=SESSION_DIR):
        self.folder = folder
        self.started = time.time()
        self.stamp = time.strftime("%Y-%m-%d %H-%M")
        self.rows = []
        self.lock = threading.Lock()
        self.jsonl = os.path.join(folder, f"session {self.stamp}.jsonl")
        self.html = os.path.join(folder, f"session {self.stamp}.html")
        self.note = ""          # device and languages, filled in at start

    def describe(self, note):
        self.note = note

    def add(self, heard, translated, source, target):
        """One finished utterance. Safe to call from any thread."""
        row = {
            "at": time.strftime("%H:%M:%S"),
            "seconds": round(time.time() - self.started, 1),
            "from": source or "",
            "to": target or "",
            "heard": heard,
            "translated": translated,
        }
        try:
            with self.lock:
                self.rows.append(row)
                os.makedirs(self.folder, exist_ok=True)
                with open(self.jsonl, "a", encoding="utf-8") as fh:
                    fh.write(_json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                first = len(self.rows) == 1
            if first:
                log(f"transcript: {self.html}")
        except OSError as e:
            log(f"could not write the transcript: {e}")

    def save(self):
        """Rebuild the readable copy. Returns its path, or None if empty."""
        with self.lock:
            rows = list(self.rows)
        if not rows:
            return None
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(self.html, "w", encoding="utf-8") as fh:
                fh.write(self._render(rows))
            return self.html
        except OSError as e:
            log(f"could not write the transcript: {e}")
            return None

    def _render(self, rows):
        def esc(text):
            return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;"))

        body = []
        for r in rows:
            pair = f"{LANG_SHORT.get(r['from'], r['from'])}"
            if r["to"] and r["to"] != r["from"]:
                pair += f" &rarr; {LANG_SHORT.get(r['to'], r['to'])}"
            body.append(
                f"<tr><td class=t>{esc(r['at'])}</td>"
                f"<td class=d>{pair}</td>"
                f"<td class=h>{esc(r['heard'])}</td>"
                f"<td class=x>{esc(r['translated'])}</td></tr>")
        minutes = (time.time() - self.started) / 60
        return f"""<!doctype html>
<meta charset="utf-8">
<title>Live Translator - {esc(self.stamp)}</title>
<style>
 body {{ background:#0D1B2A; color:#E8F0F8; margin:0; padding:32px;
        font:15px/1.6 "Segoe UI",system-ui,sans-serif; }}
 h1 {{ font-size:20px; margin:0 0 4px; }}
 .sub {{ color:#6E8CAB; font-size:13px; margin-bottom:22px; }}
 table {{ border-collapse:collapse; width:100%; max-width:1100px; }}
 th {{ text-align:left; font-size:11px; letter-spacing:.08em;
       text-transform:uppercase; color:#63D2FF; font-weight:600;
       border-bottom:1px solid #2A3E52; padding:0 12px 8px 0; }}
 td {{ vertical-align:top; padding:10px 12px 10px 0;
       border-bottom:1px solid #16293D; }}
 .t {{ color:#6E8CAB; white-space:nowrap; font-variant-numeric:tabular-nums; }}
 .d {{ color:#A78BFA; white-space:nowrap; font-size:12px; }}
 .h {{ color:#FAFCFF; }}
 .x {{ color:#A9D8F2; }}
 @media print {{ body {{ background:#fff; color:#000; }}
                 .h,.x,.t,.d {{ color:#000; }} }}
</style>
<h1>Live Translator transcript</h1>
<div class=sub>{esc(self.stamp)} &middot; {len(rows)} lines &middot;
 {minutes:.0f} min{(' &middot; ' + esc(self.note)) if self.note else ''}</div>
<table>
 <tr><th>Time</th><th></th><th>Heard</th><th>Shown</th></tr>
 {''.join(body)}
</table>
"""
