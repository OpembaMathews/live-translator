"""The app has to start on a Mac, not only on Windows.

settings.py imported ctypes.wintypes at the top of the file, which raises on
macOS and Linux, so the app did not fail at the AI-key feature -- it failed
at "import livetranslator" and never opened a window.
"""
import ast
import pathlib
import sys

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "livetranslator"
# Modules only Windows has. Importing any of these while a module is being
# read, rather than inside the function that needs it, breaks every other
# system.
WINDOWS_ONLY = {"ctypes.wintypes", "pyaudiowpatch", "winreg", "msvcrt",
                "win32api", "win32con"}


def modules():
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"), str(path))


def top_level_imports(tree):
    """Imports that run when the module is read, not ones inside functions."""
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.lineno, node.module
        elif isinstance(node, ast.If):
            # A guarded import is the point: `if WINDOWS: import ...`
            continue


def test_no_windows_module_is_imported_at_the_top_of_a_file():
    problems = []
    for path, tree in modules():
        for line, name in top_level_imports(tree):
            if name in WINDOWS_ONLY:
                problems.append(f"{path.name}:{line} imports {name}")
    assert not problems, (
        "these run on import and raise on macOS:\n  " + "\n  ".join(problems))


def test_the_key_store_knows_where_it_is():
    from livetranslator import settings

    assert settings.WINDOWS == (sys.platform == "win32")
    assert settings.MACOS == (sys.platform == "darwin")


def test_a_key_survives_a_round_trip_here():
    from livetranslator import settings

    secret = "sk-test-abc123"
    stored = settings.encrypt(secret)
    assert secret not in stored, "the key must not sit in the file as it is"
    assert settings.decrypt(stored) == secret


def test_an_empty_key_stays_empty():
    from livetranslator import settings

    assert settings.encrypt("") == ""
    assert settings.decrypt("") == ""
    assert settings.decrypt("nonsense") == ""


def test_the_macos_path_is_where_a_mac_keeps_application_data(monkeypatch):
    import importlib

    from livetranslator import paths

    monkeypatch.setattr(sys, "platform", "darwin")
    importlib.reload(paths)
    try:
        assert paths.APP_DIR.endswith("Library/Application Support/"
                                      "LiveTranslator")
    finally:
        monkeypatch.undo()
        importlib.reload(paths)


def test_the_linux_path_follows_the_xdg_convention(monkeypatch):
    import importlib

    from livetranslator import paths

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/share")
    importlib.reload(paths)
    try:
        # os.path.join uses the separator of the machine running the test,
        # not of the platform being emulated
        assert paths.APP_DIR.replace("\\", "/") == "/tmp/share/LiveTranslator"
    finally:
        monkeypatch.undo()
        importlib.reload(paths)


def test_loopback_is_empty_rather_than_broken_without_windows(monkeypatch):
    """macOS has no way to capture what the speakers play, so the list is
    empty and the app offers microphones only."""
    import builtins

    from livetranslator import audio

    real = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "pyaudiowpatch":
            raise ImportError("no such module")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert audio.list_loopback_devices() == []


def test_the_windows_only_package_is_marked_as_such():
    """pip on a Mac must not try to build a WASAPI wrapper."""
    text = (PACKAGE.parent / "requirements.txt").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines()
                if l.strip().startswith("PyAudioWPatch"))
    assert 'sys_platform == "win32"' in line


@pytest.mark.parametrize("script", ["run.sh", "read.sh"])
def test_there_is_a_launcher_for_unix(script):
    path = PACKAGE.parent / script
    assert path.is_file()
    assert path.read_text(encoding="utf-8").startswith("#!/bin/sh")


def test_the_unix_launchers_keep_unix_line_endings():
    """Checked out with CRLF, /bin/sh reports "bad interpreter: ^M"."""
    import subprocess

    for name in ("run.sh", "read.sh"):
        blob = subprocess.run(["git", "show", f":{name}"],
                              cwd=str(PACKAGE.parent), capture_output=True).stdout
        assert blob, f"{name} is not staged in git"
        assert b"\r" not in blob, f"{name} would be unusable on a Mac"
