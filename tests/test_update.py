"""Updating in place, so a tester never reinstalls.

An updater that can break the app it is updating is worse than no updater,
so most of this is about what happens when something goes wrong.
"""
import json
import os
import zipfile

import pytest

from livetranslator import update


def an_app(root):
    """The shape apply() insists on before it will replace anything."""
    package = root / "livetranslator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "1.0.0"\n',
                                         encoding="utf-8")
    (package / "engine.py").write_text("# the engine\n", encoding="utf-8")
    return root


# --- knowing what is newer ------------------------------------------------
def test_ten_comes_after_nine():
    """Comparing version strings puts 1.2.10 before 1.2.9."""
    assert update.newer("1.2.10", "1.2.9")
    assert not update.newer("1.2.9", "1.2.10")


def test_the_same_version_is_not_an_update():
    assert not update.newer("1.0.0", "1.0.0")


def test_an_older_release_is_refused():
    assert not update.newer("0.9.9", "1.0.0")


def test_an_odd_version_does_not_raise():
    assert not update.newer("", "1.0.0")
    assert update.newer("2.0", "1.9.9")


# --- checking ------------------------------------------------------------
def test_no_internet_is_a_shrug_not_a_crash(monkeypatch):
    """The app has to start on a machine that is offline."""
    def refuse(*a, **k):
        raise OSError("no route to host")

    monkeypatch.setattr(update.urllib.request, "urlopen", refuse)
    assert update.look() is None


def test_a_manifest_that_makes_no_sense_is_refused(monkeypatch):
    class Reply:
        def read(self):
            return b'["not", "a", "manifest"]'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda *a, **k: Reply())
    assert update.look() is None


# --- the package ----------------------------------------------------------
def test_an_update_carries_the_app_and_not_the_heavy_parts(tmp_path):
    source = an_app(tmp_path / "src")
    (source / "runtime").mkdir()
    (source / "runtime" / "python.exe").write_bytes(b"x" * 1000)
    (source / "livetranslator" / "__pycache__").mkdir()
    (source / "livetranslator" / "__pycache__" / "a.pyc").write_bytes(b"junk")

    archive, _mp, manifest = update.build(source=str(source),
                                          out_dir=str(tmp_path / "out"),
                                          version="1.0.1")
    inside = zipfile.ZipFile(archive).namelist()
    assert any(n.endswith("engine.py") for n in inside)
    assert not any("runtime" in n for n in inside), \
        "the private Python must never be in an update"
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in inside)
    assert manifest["sha256"] and manifest["size"] > 0


def test_the_manifest_is_readable_json(tmp_path):
    source = an_app(tmp_path / "src")
    _a, manifest_path, _m = update.build(source=str(source),
                                         out_dir=str(tmp_path / "out"),
                                         version="1.0.1")
    body = json.loads(open(manifest_path, encoding="utf-8").read())
    assert body["version"] == "1.0.1"


# --- unpacking ------------------------------------------------------------
def test_a_zip_that_writes_outside_itself_is_refused(tmp_path):
    """A release is trusted, but not that far."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../escaped.txt", "gotcha")
    with pytest.raises(ValueError, match="outside itself"):
        update.unpack(str(archive), into=str(tmp_path / "stage"))


def test_something_that_is_not_the_app_is_refused(tmp_path):
    folder = tmp_path / "new"
    (folder / "livetranslator").mkdir(parents=True)
    assert not update.sound(str(folder)), "an empty package is not the app"
    with pytest.raises(ValueError, match="does not look like the app"):
        update.apply(str(folder), target=str(tmp_path / "installed"))


# --- applying -------------------------------------------------------------
def test_the_previous_version_is_kept(tmp_path, monkeypatch):
    installed = an_app(tmp_path / "installed")
    incoming = an_app(tmp_path / "new")
    (incoming / "livetranslator" / "__init__.py").write_text(
        '__version__ = "1.0.1"\n', encoding="utf-8")
    monkeypatch.setattr(update, "BACKUP", str(tmp_path / "previous"))
    monkeypatch.setattr(update, "STAGE", str(tmp_path / "stage"))

    update.apply(str(incoming), target=str(installed))
    now = (installed / "livetranslator" / "__init__.py").read_text()
    assert '1.0.1' in now
    kept = tmp_path / "previous" / "livetranslator" / "__init__.py"
    assert kept.is_file() and "1.0.0" in kept.read_text()


def test_a_failure_halfway_puts_everything_back(tmp_path, monkeypatch):
    """A half-updated app is the one outcome worth real trouble to avoid."""
    installed = an_app(tmp_path / "installed")
    (installed / "data").mkdir()
    (installed / "data" / "seed.json").write_text("{}", encoding="utf-8")
    incoming = an_app(tmp_path / "new")
    (incoming / "data").mkdir()
    (incoming / "data" / "seed.json").write_text('{"new": 1}', encoding="utf-8")

    monkeypatch.setattr(update, "BACKUP", str(tmp_path / "previous"))
    monkeypatch.setattr(update, "STAGE", str(tmp_path / "stage"))

    real_move = update.shutil.move
    calls = {"n": 0}

    def move(src, dst):
        calls["n"] += 1
        if calls["n"] == 4:           # part way through the second part
            raise OSError("the disk went away")
        return real_move(src, dst)

    monkeypatch.setattr(update.shutil, "move", move)
    with pytest.raises(OSError):
        update.apply(str(incoming), target=str(installed))

    back = (installed / "livetranslator" / "__init__.py")
    assert back.is_file(), "the app was left without its package"
    assert "1.0.0" in back.read_text(), "the old version should be restored"


def test_an_update_waiting_to_be_applied_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "STAGE", str(tmp_path / "stage"))
    assert update.stage_is_ready() is None
    an_app(tmp_path / "stage" / "new")
    assert update.stage_is_ready() is not None


# --- what a damaged download does ----------------------------------------
def test_a_damaged_download_is_thrown_away(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "STAGE", str(tmp_path / "stage"))
    from livetranslator import models

    monkeypatch.setattr(models, "fetch",
                        lambda url, dest, *a, **k:
                            open(dest, "wb").write(b"not what was promised"))
    with pytest.raises(ValueError, match="damaged"):
        update.download({"url": "https://example/u.zip",
                         "sha256": "0" * 64, "size": 21})
    assert not os.path.exists(os.path.join(update.STAGE, "update.zip"))


# --- one version, in one place -------------------------------------------
def test_the_installer_fallback_matches_the_package():
    """build.ps1 passes the real version to Inno Setup, but the script also
    compiles on its own. If that fallback drifts, an installer reports one
    version while the app it installs reports another, and the updater then
    offers a release the user already has."""
    import re

    from livetranslator import __version__

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    iss = open(os.path.join(here, "installer", "live-translator.iss"),
               encoding="utf-8").read()
    found = re.search(r'#define\s+AppVersion\s+"([^"]+)"', iss)
    assert found, "the installer names no version at all"
    assert found.group(1) == __version__, (
        f"the installer says {found.group(1)}, the package says {__version__}")


def test_the_version_looks_like_a_version():
    import re

    from livetranslator import __version__

    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


# --- the startup check must not take the app down -------------------------
def test_the_background_check_keeps_its_thread_alive_until_it_stops(monkeypatch):
    """Releasing the last reference to a running QThread destroys it, and
    destroying a running QThread aborts the process. The app started, checked
    for updates, and closed about a second later."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from livetranslator.ui import update_dialog

    monkeypatch.setattr(update_dialog.update, "look",
                        lambda *a, **k: {"version": "0.0.1"})
    update_dialog._looking = None
    update_dialog._found = None

    update_dialog.look_quietly()
    held = update_dialog._looking
    assert held is not None, "nothing is holding the thread"
    thread = held[0]

    for _ in range(200):
        QCoreApplication.processEvents()
        if update_dialog._looking is None:
            break

    assert thread.isFinished(), \
        "the reference was released while the thread was still running"
    assert update_dialog._looking is None, "the thread was never released"


def test_an_unreachable_server_is_survived_at_startup(monkeypatch):
    """A 404 from the releases URL is the normal state before the first
    release, and it must not stop the app."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from livetranslator.ui import update_dialog

    monkeypatch.setattr(update_dialog.update, "look", lambda *a, **k: None)
    update_dialog._looking = None
    update_dialog.look_quietly()
    for _ in range(200):
        QCoreApplication.processEvents()
        if update_dialog._looking is None:
            break
    assert update_dialog._looking is None
    assert update_dialog.waiting() is None
