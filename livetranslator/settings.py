"""Small persistent settings store for the app.

Only the AI provider settings live here for now. The API key never sits in
plain text on disk: Windows encrypts it with DPAPI and macOS keeps it in the
Keychain, both of which tie it to the one user on the one machine.

Anywhere else there is no store to use, and the key is merely base64 -- which
is not security, only a guard against reading it by accident. save() says so
in the log rather than letting a user assume otherwise.

No third-party packages - the point of the AI feature is that a user with a
Claude or Gemini subscription does not have to install or download anything.
"""

import base64
import ctypes
import json
import os
import subprocess
import sys

from .log import log
from .paths import SETTINGS_PATH

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
# The Keychain entry. Deleting it in Keychain Access forgets the key, which
# is where a macOS user would look to do that.
KEYCHAIN_SERVICE = "LiveTranslator"
KEYCHAIN_ACCOUNT = "ai-key"

if WINDOWS:
    import ctypes.wintypes          # fails to import anywhere else

PROVIDERS = ("off", "claude", "gemini")
DEFAULTS = {
    "ai_provider": "off",      # "off" | "claude" | "gemini"
    "ai_key": "",              # stored encrypted; blank means "not set"
    "ai_covers_speech": False,  # Gemini only - use it for transcription too
}


def config_path():
    return SETTINGS_PATH


# --- keeping the key out of plain text --------------------------------------
if WINDOWS:
    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]


def _keychain(args, text=None):
    """The macOS security tool. Present on every Mac, so nothing to install."""
    done = subprocess.run(["security"] + args, input=text, capture_output=True,
                          text=True, timeout=10)
    if done.returncode != 0:
        raise OSError(done.stderr.strip() or "security failed")
    return done.stdout


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
    if WINDOWS:
        try:
            return "dpapi:" + base64.b64encode(
                _dpapi("CryptProtectData", text.encode("utf-8"))).decode("ascii")
        except Exception as e:
            log(f"settings: DPAPI unavailable ({e}); storing the key encoded "
                f"rather than encrypted")
    elif MACOS:
        try:
            # -U updates the entry when it is already there, -w reads the
            # secret from stdin so it never appears in the process list.
            _keychain(["add-generic-password", "-U",
                       "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
                       "-w", text])
            return "keychain:"
        except Exception as e:
            log(f"settings: Keychain unavailable ({e}); storing the key "
                f"encoded rather than encrypted")
    # No store to use. base64 is not security, it only keeps the key from
    # being read at a glance, so say so once.
    log("settings: this system has no key store; the AI key is only encoded")
    return "b64:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


def decrypt(stored):
    if not stored:
        return ""
    try:
        if stored.startswith("dpapi:"):
            return _dpapi("CryptUnprotectData",
                          base64.b64decode(stored[6:])).decode("utf-8")
        if stored.startswith("keychain:"):
            # The key itself is in the Keychain; the file only records that.
            return _keychain(["find-generic-password", "-s", KEYCHAIN_SERVICE,
                              "-a", KEYCHAIN_ACCOUNT, "-w"]).strip()
        if stored.startswith("b64:"):
            return base64.b64decode(stored[4:]).decode("utf-8")
    except Exception:
        return ""
    return ""


# --- load / save -----------------------------------------------------------
def load():
    """Return the settings dict, with the key already decrypted."""
    data = dict(DEFAULTS)
    try:
        with open(config_path(), encoding="utf-8") as fh:
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


def save(provider=None, key=None, covers_speech=None):
    """Update whichever fields are given, leave the rest as they are."""
    current = load()
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
    tmp = config_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(on_disk, fh, indent=2)
    os.replace(tmp, config_path())
    return current


def ai_enabled():
    c = load()
    return c["ai_provider"] != "off" and bool(c["ai_key"])
