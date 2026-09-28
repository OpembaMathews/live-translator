"""Updating the app in place, so a tester never reinstalls.

The app is Python, not a compiled binary, so an update is the source folder
and nothing else: about 200 KB against the installer's 13 MB. The private
runtime and the models -- the parts that take minutes to fetch -- are never
touched.

There is no server. A release on GitHub carries a small manifest and a zip,
both over HTTPS, and that is the whole distribution mechanism. What the
manifest says is trusted because of where it came from; the checksum in it
guards against a damaged download, not against a forged manifest, and it is
worth being clear about the difference.

Nothing is replaced while the app is running from it. The new files are
unpacked beside the old ones, checked, and swapped in at the next start,
with the previous version kept so a bad update can be undone.
"""
import hashlib
import json
import os
import shutil
import sys
import urllib.request
import zipfile

from . import __version__
from .log import log
from .paths import APP_DIR, ROOT

# Where releases are published. "latest" follows whatever was released last,
# so the app needs no knowledge of version numbers to find its successor.
MANIFEST_URL = ("https://github.com/OpembaMathews/live-translator/releases/"
                "latest/download/update.json")
AGENT = {"User-Agent": "LiveTranslator-updater"}
STAGE = os.path.join(APP_DIR, "update")
BACKUP = os.path.join(APP_DIR, "previous")
# Only these come down in an update. The runtime and the models stay put.
PARTS = ("livetranslator", "data", "assets", "docs",
         "requirements.txt", "README.md")


def as_numbers(version):
    """1.2.10 sorts after 1.2.9, which comparing strings would get wrong."""
    out = []
    for piece in str(version).split("."):
        digits = "".join(c for c in piece if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def newer(there, here=__version__):
    return as_numbers(there) > as_numbers(here)


def look(url=MANIFEST_URL, timeout=20):
    """What the latest release says about itself, or None if unreachable.

    A machine with no internet, or GitHub having a bad day, must not stop
    the app starting, so every failure here is a shrug and a log line.
    """
    try:
        request = urllib.request.Request(url, headers=AGENT)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            manifest = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        log(f"update: could not check ({type(e).__name__}: {e})")
        return None
    if not isinstance(manifest, dict) or "version" not in manifest:
        log("update: the manifest made no sense")
        return None
    return manifest


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            sha.update(block)
    return sha.hexdigest()


def download(manifest, on_progress=None, stop=None):
    """Fetch the update zip and check it is the one the manifest described."""
    from .models import fetch

    url = manifest.get("url")
    if not url:
        raise ValueError("the manifest names no download")
    os.makedirs(STAGE, exist_ok=True)
    archive = os.path.join(STAGE, "update.zip")
    if os.path.exists(archive):
        os.remove(archive)          # never resume an update: it may differ
    fetch(url, archive, on_progress, expected=manifest.get("size", 0),
          stop=stop)

    wanted = (manifest.get("sha256") or "").lower()
    if wanted:
        got = digest(archive)
        if got != wanted:
            os.remove(archive)
            raise ValueError(f"the download was damaged: {got[:12]} came "
                             f"instead of {wanted[:12]}")
    return archive


def unpack(archive, into=None):
    """Unpack to a staging folder, refusing anything that escapes it."""
    into = into or os.path.join(STAGE, "new")
    if os.path.exists(into):
        shutil.rmtree(into, ignore_errors=True)
    os.makedirs(into, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            # A zip may name ..\..\somewhere-else. Python 3.12 blocks the
            # worst of it, but refusing outright is clearer than relying on
            # what a library happens to do.
            place = os.path.normpath(os.path.join(into, member))
            if not place.startswith(os.path.normpath(into) + os.sep) \
                    and place != os.path.normpath(into):
                raise ValueError(f"the update tried to write outside itself: "
                                 f"{member}")
        zf.extractall(into)
    return into


def sound(folder):
    """Refuse an update that is not actually the app."""
    package = os.path.join(folder, "livetranslator")
    return (os.path.isdir(package)
            and os.path.isfile(os.path.join(package, "__init__.py"))
            and os.path.isfile(os.path.join(package, "engine.py")))


def apply(folder, target=None):
    """Swap the new files in, keeping the old ones to fall back on."""
    target = target or ROOT
    if not sound(folder):
        raise ValueError("the update does not look like the app")

    if os.path.exists(BACKUP):
        shutil.rmtree(BACKUP, ignore_errors=True)
    os.makedirs(BACKUP, exist_ok=True)

    moved = []
    try:
        for part in PARTS:
            new = os.path.join(folder, part)
            if not os.path.exists(new):
                continue            # a release need not carry every part
            old = os.path.join(target, part)
            if os.path.exists(old):
                shutil.move(old, os.path.join(BACKUP, part))
                moved.append(part)
            shutil.move(new, old)
    except Exception:
        # Put back whatever was moved, so a failure halfway leaves a
        # working app rather than a half-updated one.
        for part in moved:
            kept = os.path.join(BACKUP, part)
            live = os.path.join(target, part)
            if os.path.exists(kept):
                if os.path.exists(live):
                    shutil.rmtree(live, ignore_errors=True) \
                        if os.path.isdir(live) else os.remove(live)
                shutil.move(kept, live)
        log("update: failed and was rolled back")
        raise
    shutil.rmtree(os.path.join(STAGE, "new"), ignore_errors=True)
    return True


def needs_libraries(manifest):
    """Whether this update changes what pip has to have installed."""
    return bool(manifest.get("requirements_changed"))


def relaunch():
    """Start the new copy and leave. Windows will not overwrite a running exe,
    but nothing here is an exe: the swap has already happened on disk, so
    this only puts the user back where they were."""
    try:
        os.execv(sys.executable, [sys.executable, "-m", "livetranslator"])
    except Exception as e:
        log(f"update: could not restart ({type(e).__name__}: {e})")
        return False
    return True


def stage_is_ready():
    """An update downloaded earlier and waiting to be put in place."""
    folder = os.path.join(STAGE, "new")
    return folder if os.path.isdir(folder) and sound(folder) else None


def clean_up():
    for path in (STAGE, BACKUP):
        shutil.rmtree(path, ignore_errors=True)


def build(source=None, out_dir=None, version=__version__, base_url=""):
    """Make the update zip and its manifest. Used by the release script."""
    source = source or ROOT
    out_dir = out_dir or os.path.join(source, "dist")
    os.makedirs(out_dir, exist_ok=True)
    name = f"live-translator-{version}.zip"
    archive = os.path.join(out_dir, name)

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for part in PARTS:
            here = os.path.join(source, part)
            if os.path.isfile(here):
                zf.write(here, part)
            elif os.path.isdir(here):
                for folder, _dirs, files in os.walk(here):
                    if "__pycache__" in folder:
                        continue
                    for filename in files:
                        if filename.endswith((".pyc", ".pyo")):
                            continue
                        full = os.path.join(folder, filename)
                        zf.write(full, os.path.relpath(full, source))

    manifest = {
        "version": version,
        "url": f"{base_url.rstrip('/')}/{name}" if base_url else name,
        "size": os.path.getsize(archive),
        "sha256": digest(archive),
        "requirements_changed": False,
    }
    manifest_path = os.path.join(out_dir, "update.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return archive, manifest_path, manifest


def main():
    """python -m livetranslator.update -- check, and say what is there."""
    manifest = look()
    if manifest is None:
        print("Could not reach the update server.")
        return 1
    there = manifest.get("version", "?")
    if not newer(there):
        print(f"Up to date ({__version__}).")
        return 0
    print(f"Version {there} is available; this is {__version__}.")
    archive = download(manifest)
    folder = unpack(archive)
    apply(folder)
    print("Updated. Restart the app to use it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
