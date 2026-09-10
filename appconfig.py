"""Small persistent settings store, shared by the app and the installer.

Only the AI provider settings live here for now. The API key is encrypted with
Windows DPAPI so it is readable only by the same user on the same machine, and
never sits in plain text on disk. Everything else the app remembers is
transient or lives in code.

No third-party packages - the point of the AI feature is that a user with a
Claude or Gemini subscription does not have to install or download anything.
"""

import base64
import ctypes
import ctypes.wintypes
import json
import os
import sys

PROVIDERS = ("off", "claude", "gemini")
DEFAULTS = {
    "ai_provider": "off",      # "off" | "claude" | "gemini"
    "ai_key": "",              # stored encrypted; blank means "not set"
    "ai_covers_speech": False,  # Gemini only - use it for transcription too
}


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path(app_dir=None):
    return os.path.join(app_dir or _app_dir(), "settings.json")


# --- DPAPI: encrypt the key so it is not plain text on disk -----------------
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(fn_name, data):
    blob_in = _BLOB(len(data), ctypes.cast(
        ctypes.create_string_buffer(data, len(data)),
        ctypes.POINTER(ctypes.c_char)))
    blob_out = _BLOB()
    fn = getattr(ctypes.windll.crypt32, fn_name)
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"{fn_name} failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def encrypt(text):
    if not text:
        return ""
    try:
        return "dpapi:" + base64.b64encode(
            _dpapi("CryptProtectData", text.encode("utf-8"))).decode("ascii")
    except Exception:
        # Non-Windows or DPAPI unavailable: base64 is not security, just
        # keeps the raw key from being greppable at a glance
        return "b64:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


def decrypt(stored):
    if not stored:
        return ""
    try:
        if stored.startswith("dpapi:"):
            return _dpapi("CryptUnprotectData",
                          base64.b64decode(stored[6:])).decode("utf-8")
        if stored.startswith("b64:"):
            return base64.b64decode(stored[4:]).decode("utf-8")
    except Exception:
        return ""
    return ""


# --- load / save -----------------------------------------------------------
def load(app_dir=None):
    """Return the settings dict, with the key already decrypted."""
    data = dict(DEFAULTS)
    try:
        with open(config_path(app_dir), encoding="utf-8") as fh:
            raw = json.load(fh)
        for k in DEFAULTS:
            if k in raw:
                data[k] = raw[k]
        data["ai_key"] = decrypt(raw.get("ai_key", ""))
    except (OSError, ValueError):
        pass
    if data["ai_provider"] not in PROVIDERS:
        data["ai_provider"] = "off"
    return data


def save(provider=None, key=None, covers_speech=None, app_dir=None):
    """Update whichever fields are given, leave the rest as they are."""
    current = load(app_dir)
    if provider is not None:
        current["ai_provider"] = provider if provider in PROVIDERS else "off"
    if key is not None:
        current["ai_key"] = key
    if covers_speech is not None:
        current["ai_covers_speech"] = bool(covers_speech)

    on_disk = {
        "ai_provider": current["ai_provider"],
        "ai_key": encrypt(current["ai_key"]),
        "ai_covers_speech": current["ai_covers_speech"],
    }
    tmp = config_path(app_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(on_disk, fh, indent=2)
    os.replace(tmp, config_path(app_dir))
    return current


def ai_enabled():
    c = load()
    return c["ai_provider"] != "off" and bool(c["ai_key"])
