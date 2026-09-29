"""Fetching the models the app needs, so a user never has to.

The installer is small because none of this ships inside it: the voice, the
translation packs and the speech model together are most of a gigabyte, and
bundling them would make a download that most people abandon. They are
fetched once, on first run, into the user's own app-data folder.

Whisper is not here. faster-whisper downloads its own model through Hugging
Face the first time it is asked to transcribe, and re-implementing that
would only add a second thing to go wrong.

Everything is written to a neighbouring ".part" file and renamed only once
the whole file has arrived, so a download cut off halfway leaves nothing
that looks finished. A run that is interrupted simply starts that file
again.
"""
import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile

from .log import log
from .paths import PACK_DIR, TTS_DIR

# argos-net.com answers 403 to urllib's own user agent, so it is told a
# browser's. The index is on GitHub and does not care, but sends the same.
AGENT = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
INDEX = ("https://raw.githubusercontent.com/argosopentech/"
         "argospm-index/main/index.json")
# model-files-v1.1, not v1.0. Both releases carry a file called
# kokoro-v1.0.onnx and they are not the same file: only the v1.1 build
# reports how long each sound lasts, and without those the reader cannot
# highlight the words it is reading, so it refuses to start at all.
KOKORO = ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
          "model-files-v1.1/")
# (from, to) for the packs the app offers. English is the pivot, so these
# four reach every combination the language menu lists.
WANTED = (("en", "zt"), ("zt", "en"), ("en", "sw"), ("sw", "en"))
# Name, exact size, and checksum of the file that release serves. The size
# was approximate before and the two releases differ by 27 KB in a 325 MB
# file, which nothing would have noticed. Pinned like this, fetching the
# wrong one fails loudly at download instead of quietly at first use.
VOICE_FILES = (
    ("kokoro-v1.0.onnx", 325_505_369,
     "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"),
    ("voices-v1.0.bin", 28_214_398,
     "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
)
CHUNK = 1 << 16
# The four packs are 71-76 MB. Their exact size is only known once the index
# has been fetched, and the estimate only has to be close enough for a bar.
PACK_SIZE = 75_000_000


def watching():
    """Whether a person is looking at this, or a log file is collecting it."""
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def expected_for(name):
    """The size and checksum the release should serve for this file."""
    for filename, size, sha in VOICE_FILES:
        if filename == name:
            return size, sha
    return 0, ""


def check(path, size, sha):
    """Refuse a file that is not the one that was asked for.

    Two Kokoro releases ship a kokoro-v1.0.onnx and they differ by 27 KB in
    325 MB. The wrong one installs perfectly and then fails at first use
    with a message about durations, which is a long way from the cause.
    """
    import hashlib

    got = os.path.getsize(path)
    if size and got != size:
        os.remove(path)
        raise ValueError(f"{os.path.basename(path)} is {got:,} bytes, not "
                         f"{size:,}: the wrong file was served")
    if not sha:
        return True
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != sha:
        os.remove(path)
        raise ValueError(f"{os.path.basename(path)} did not arrive intact")
    return True


def voice_dir():
    return os.path.join(TTS_DIR, "kokoro")


def voice_missing():
    return [name for name, _size, _sha in VOICE_FILES
            if not os.path.exists(os.path.join(voice_dir(), name))]


def packs_missing():
    """The language pairs that are not installed anywhere the app looks."""
    from .translate import LeanEngine

    have = LeanEngine._discover()
    return [pair for pair in WANTED if pair not in have]


def missing():
    """What still has to be fetched, and roughly how much that is."""
    voices = voice_missing()
    packs = packs_missing()
    size = sum(s for n, s, _h in VOICE_FILES if n in voices)
    size += PACK_SIZE * len(packs)
    return voices, packs, size


class Stopped(Exception):
    """The download was asked to stop. What arrived is kept."""


class Speed:
    """How fast this is going, and what that means for the time left.

    Measured over a moving window rather than from the start, because the
    first seconds of a download are not representative and a connection that
    recovers should stop being called slow.
    """

    # Below this, 650 MB takes over half an hour, which is worth saying out
    # loud before somebody sits watching a bar that looks stuck.
    SLOW = 300_000          # bytes a second
    WINDOW = 8.0            # seconds to average over

    def __init__(self, clock=time.monotonic):
        self.clock = clock          # injectable, so this can be tested
        self.marks = []
        self.rate = 0.0

    def note(self, done):
        now = self.clock()
        self.marks.append((now, done))
        while len(self.marks) > 2 and now - self.marks[0][0] > self.WINDOW:
            self.marks.pop(0)
        first, last = self.marks[0], self.marks[-1]
        seconds = last[0] - first[0]
        if seconds >= 1.0:
            self.rate = max(0.0, (last[1] - first[1]) / seconds)
        return self.rate

    @property
    def slow(self):
        return 0 < self.rate < self.SLOW

    def left(self, done, total):
        """Seconds remaining at the current rate, or None if not yet known."""
        if self.rate <= 0 or total <= done:
            return None
        return (total - done) / self.rate

    def describe(self, done, total):
        """A phrase a person can act on, not a number."""
        if self.rate <= 0:
            return ""
        speed = f"{self.rate / 1e6:.1f} MB/s" if self.rate >= 1e6             else f"{self.rate / 1e3:.0f} KB/s"
        seconds = self.left(done, total)
        if seconds is None:
            return speed
        # An estimate in days is not information. At that point the useful
        # thing to say is that it will not finish on this connection.
        if seconds > 4 * 3600:
            return f"{speed}, more than 4 hours left"
        if seconds > 5400:
            return f"{speed}, about {seconds / 3600:.1f} hours left"
        if seconds > 90:
            return f"{speed}, about {round(seconds / 60)} min left"
        return f"{speed}, about {round(seconds)} sec left"


def fetch(url, dest, on_progress=None, expected=0, stop=None):
    """Download one file, and only put it in place once it is whole.

    A part file that is already there is continued rather than started
    again. On a slow connection 650 MB is most of an hour, and asking
    somebody to pause meant nothing while pausing threw away the bytes they
    had already waited for.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0

    headers = dict(AGENT)
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        # 206 means the server honoured the range. 200 means it sent the
        # whole file regardless, so what was there is no use.
        resuming = response.status == 206 and have > 0
        if not resuming:
            have = 0
        length = int(response.headers.get("Content-Length", 0))
        total = (have + length) if length else expected
        if resuming:
            log(f"models: carrying on {os.path.basename(dest)} "
                f"from {have / 1e6:.0f} MB")
        done = have
        with open(part, "ab" if resuming else "wb") as out:
            while True:
                if stop is not None and stop.is_set():
                    out.flush()
                    raise Stopped()
                block = response.read(CHUNK)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if on_progress:
                    on_progress(os.path.basename(dest), done, total)
    os.replace(part, dest)
    return done


def pack_links():
    """Where each wanted pack can be downloaded from."""
    request = urllib.request.Request(INDEX, headers=AGENT)
    with urllib.request.urlopen(request, timeout=60) as response:
        index = json.loads(response.read().decode("utf-8"))
    links = {}
    for entry in index:
        key = (entry.get("from_code"), entry.get("to_code"))
        if key in WANTED and entry.get("links") and key not in links:
            links[key] = entry["links"][0]
    return links


def install_pack(url, dest_root, on_progress=None, expected=0, stop=None):
    """An .argosmodel is a zip holding one folder; unpack it as it is."""
    name = url.rsplit("/", 1)[-1]
    archive = os.path.join(dest_root, name)
    fetch(url, archive, on_progress, expected=expected, stop=stop)
    try:
        with zipfile.ZipFile(archive) as zf:
            inside = {n.split("/")[0] for n in zf.namelist() if "/" in n}
            zf.extractall(dest_root)
    finally:
        os.remove(archive)
    return sorted(inside)


def install(on_progress=None, on_step=None, stop=None):
    """Fetch whatever is missing. Safe to run again; it skips what is there.

    Progress is reported across the whole download, not per file. There are
    six things to fetch and a bar that ran nought to a hundred six times
    told the user nothing about how long was left -- which is the only
    question a progress bar exists to answer.
    """
    step = on_step or (lambda text: None)
    voices, packs, total = missing()
    jobs = len(voices) + len(packs)
    done = [0]          # bytes finished before the item now downloading
    at = [0]            # which item that is

    def overall(name, got, _size):
        at_now = min(done[0] + got, total)
        if on_progress:
            on_progress(f"{name} ({at[0]} of {jobs})", at_now, total)

    for name in voices:
        if stop is not None and stop.is_set():
            raise Stopped()
        at[0] += 1
        size, wanted = expected_for(name)
        step(f"Downloading the voice, {at[0]} of {jobs}: {name}")
        where = os.path.join(voice_dir(), name)
        fetch(KOKORO + name, where, overall, expected=size, stop=stop)
        check(where, size, wanted)
        done[0] += size
        log(f"models: fetched {name}")

    if packs:
        step(f"Looking up the translation packs ({at[0]} of {jobs} done)")
        links = pack_links()
        for pair in packs:
            if stop is not None and stop.is_set():
                raise Stopped()
            at[0] += 1
            url = links.get(pair)
            if not url:
                log(f"models: no download listed for {pair[0]}->{pair[1]}")
                done[0] += PACK_SIZE
                continue
            step(f"Downloading translation {at[0]} of {jobs}: "
                 f"{pair[0]} to {pair[1]}")
            install_pack(url, PACK_DIR, overall, expected=PACK_SIZE,
                         stop=stop)
            done[0] += PACK_SIZE
            log(f"models: installed pack {pair[0]}->{pair[1]}")

    left_voices, left_packs, _ = missing()
    return not left_voices and not left_packs


def main():
    """python -m livetranslator.models -- fetch everything, with a bar."""
    voices, packs, size = missing()
    if not voices and not packs:
        print("Everything is already downloaded.")
        return 0
    print(f"About {size / 1e6:.0f} MB to download "
          f"({len(voices)} voice files, {len(packs)} translation packs).")

    # A bar redrawn with \r is right in a terminal and useless in a log,
    # where it arrives as one enormous line. The installer reads this
    # through a log, so when nothing is watching it prints plain lines.
    live = watching()
    width = shutil.get_terminal_size((70, 20)).columns - 34
    last = {"percent": -100}

    def bar(name, done, total):
        if not total:
            return
        percent = int(100 * done / total)
        if live:
            filled = int(width * done / total)
            sys.stdout.write(
                f"\r  {name[:26]:26} [{'#' * filled}{'.' * (width - filled)}] "
                f"{percent:3.0f}%")
        elif percent >= last["percent"] + 5:
            last["percent"] = percent
            sys.stdout.write(f"{name}: {percent}%\n")
        sys.stdout.flush()

    def step(text):
        last["percent"] = -100
        sys.stdout.write(f"\n{text}\n" if live else f"{text}\n")
        sys.stdout.flush()

    try:
        ok = install(on_progress=bar, on_step=step)
    except Exception as e:
        print(f"\nDownload failed: {type(e).__name__}: {e}")
        print("Run this again to carry on; what arrived is kept.")
        return 1
    print("\n" + ("Ready." if ok else "Some pieces are still missing."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
