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
import urllib.request
import zipfile

from .log import log
from .paths import PACK_DIR, TTS_DIR

# argos-net.com answers 403 to urllib's own user agent, so it is told a
# browser's. The index is on GitHub and does not care, but sends the same.
AGENT = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
INDEX = ("https://raw.githubusercontent.com/argosopentech/"
         "argospm-index/main/index.json")
KOKORO = ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
          "model-files-v1.0/")
# (from, to) for the packs the app offers. English is the pivot, so these
# four reach every combination the language menu lists.
WANTED = (("en", "zt"), ("zt", "en"), ("en", "sw"), ("sw", "en"))
VOICE_FILES = (("kokoro-v1.0.onnx", 325_522_944),
               ("voices-v1.0.bin", 28_182_016))
CHUNK = 1 << 16


def voice_dir():
    return os.path.join(TTS_DIR, "kokoro")


def voice_missing():
    return [name for name, _size in VOICE_FILES
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
    size = sum(s for n, s in VOICE_FILES if n in voices)
    size += 75_000_000 * len(packs)          # the four packs are 71-76 MB
    return voices, packs, size


def fetch(url, dest, on_progress=None, expected=0):
    """Download one file, and only put it in place once it is whole."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    request = urllib.request.Request(url, headers=AGENT)
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length", 0)) or expected
        done = 0
        with open(part, "wb") as out:
            while True:
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


def install_pack(url, dest_root):
    """An .argosmodel is a zip holding one folder; unpack it as it is."""
    name = url.rsplit("/", 1)[-1]
    archive = os.path.join(dest_root, name)
    fetch(url, archive)
    try:
        with zipfile.ZipFile(archive) as zf:
            inside = {n.split("/")[0] for n in zf.namelist() if "/" in n}
            zf.extractall(dest_root)
    finally:
        os.remove(archive)
    return sorted(inside)


def install(on_progress=None, on_step=None):
    """Fetch whatever is missing. Safe to run again; it skips what is there."""
    step = on_step or (lambda text: None)
    voices, packs, _size = missing()

    for name in voices:
        step(f"Downloading the voice: {name}")
        size = dict(VOICE_FILES)[name]
        fetch(KOKORO + name, os.path.join(voice_dir(), name),
              on_progress, expected=size)
        log(f"models: fetched {name}")

    if packs:
        step("Looking up the translation packs")
        links = pack_links()
        for pair in packs:
            url = links.get(pair)
            if not url:
                log(f"models: no download listed for {pair[0]}->{pair[1]}")
                continue
            step(f"Downloading translation: {pair[0]} to {pair[1]}")
            install_pack(url, PACK_DIR)
            log(f"models: installed pack {pair[0]}->{pair[1]}")

    left_voices, left_packs, _ = missing()
    return not left_voices and not left_packs


def main():
    """python -m livetranslator.models -- fetch everything, with a bar."""
    import sys

    voices, packs, size = missing()
    if not voices and not packs:
        print("Everything is already downloaded.")
        return 0
    print(f"About {size / 1e6:.0f} MB to download "
          f"({len(voices)} voice files, {len(packs)} translation packs).")

    width = shutil.get_terminal_size((70, 20)).columns - 34

    def bar(name, done, total):
        if not total:
            return
        filled = int(width * done / total)
        sys.stdout.write(
            f"\r  {name[:26]:26} [{'#' * filled}{'.' * (width - filled)}] "
            f"{100 * done / total:3.0f}%")
        sys.stdout.flush()

    def step(text):
        sys.stdout.write(f"\n{text}\n")

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
