"""Installer for Live Translator.

Ships the application folder inside itself, unpacks it once to a private
location, makes the shortcuts, and pre-downloads the speech model. After
this runs, launching the app is the fast path - no archive to unpack on
every start, and no folder the user can accidentally break by deleting a
file out of it.
"""

import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk

APP_NAME = "Live Translator"
APP_VERSION = "1.0.0"
PUBLISHER = "Live Translator"
# Per-user hive, so no administrator rights are needed and the entry
# belongs to whoever installed it rather than the whole machine.
UNINSTALL_KEY = chr(92).join([
    "Software", "Microsoft", "Windows", "CurrentVersion",
    "Uninstall", "LiveTranslator",
])
FOLDER_NAME = "LiveTranslator"
EXE_NAME = "LiveTranslator.exe"

BG = "#0D1B2A"
FG = "#FAFCFF"
DIM = "#6E8CAB"
ACCENT = "#63D2FF"
TRACK = "#1B3149"


def install_root():
    """Per-user location, so no administrator rights are needed."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", FOLDER_NAME)


def payload_dir():
    bundled = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(bundled, "payload")


def shell_folder(name):
    """Ask Windows where a known folder actually is.

    Expanding ~/Desktop is wrong whenever OneDrive redirects the profile,
    which is common - the real path here was <user>/OneDrive/Desktop, so the
    desktop shortcut was silently skipped.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"[Environment]::GetFolderPath('{name}')"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        return out if out and os.path.isdir(out) else None
    except (OSError, subprocess.SubprocessError):
        return None


def make_shortcut(target, link_path, icon, workdir):
    """Uses WScript.Shell through PowerShell so nothing extra is required."""
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        "$s.TargetPath = '{target}';"
        "$s.Arguments = '';"
        "$s.WorkingDirectory = '{workdir}';"
        "$s.IconLocation = '{icon}';"
        "$s.Description = 'Live speech translation overlay';"
        "$s.Save()"
    ).format(link=link_path, target=target, workdir=workdir, icon=icon)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def register_in_settings(target, exe, icon, size_bytes):
    """Add the entry that Windows Settings > Apps reads.

    Without it the only way to remove the app is finding uninstall.bat
    inside the install folder, which nobody will think to do.
    """
    import winreg

    uninstaller = os.path.join(target, "uninstall.bat")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                            winreg.KEY_WRITE) as key:
        text = {
            "DisplayName": APP_NAME,
            "DisplayVersion": APP_VERSION,
            "Publisher": PUBLISHER,
            "DisplayIcon": icon,
            "InstallLocation": target,
            "UninstallString": 'cmd.exe /c ""' + uninstaller + '""',
        }
        for name, value in text.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("EstimatedSize", size_bytes // 1024),
                            ("NoModify", 1), ("NoRepair", 1)):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


class Installer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"Install {APP_NAME}")
        self.root.configure(bg=BG)
        self.root.geometry("520x260")
        self.root.resizable(False, False)

        tk.Label(self.root, text=APP_NAME, bg=BG, fg=FG,
                 font=("Segoe UI", 20, "bold")).pack(pady=(28, 2))
        tk.Label(self.root, text="Live speech translation, running on your PC",
                 bg=BG, fg=DIM, font=("Segoe UI", 10)).pack()

        self.status = tk.Label(self.root, text="Ready to install", bg=BG, fg=FG,
                               font=("Segoe UI", 10), wraplength=460)
        self.status.pack(pady=(26, 8))

        self.canvas = tk.Canvas(self.root, width=420, height=6, bg=BG,
                                highlightthickness=0)
        self.canvas.pack()
        self.canvas.create_line(0, 3, 420, 3, fill=TRACK, width=5)
        self.bar = self.canvas.create_line(0, 3, 0, 3, fill=ACCENT, width=5)

        self.button = tk.Button(self.root, text="Install", command=self.start,
                                bg=ACCENT, fg=BG, font=("Segoe UI", 11, "bold"),
                                relief="flat", padx=26, pady=7,
                                activebackground=FG, cursor="hand2")
        self.button.pack(pady=22)

    def say(self, text, progress=None):
        def apply():
            self.status.config(text=text)
            if progress is not None:
                self.canvas.coords(self.bar, 0, 3, max(2, 420 * progress), 3)
        self.root.after(0, apply)

    def start(self):
        self.button.config(state="disabled", text="Installing...")
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            target = install_root()
            self.say("Copying application files...", 0.05)
            # Anything still running holds locks on files we are replacing
            subprocess.run(["taskkill", "/IM", EXE_NAME, "/F"],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

            # Copy beside the target then swap, so a failure part way through
            # leaves the old install intact instead of a folder missing files
            staging = target + ".new"
            shutil.rmtree(staging, ignore_errors=True)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copytree(payload_dir(), staging)

            expected = sum(len(f) for _r, _d, f in os.walk(payload_dir()))
            copied = sum(len(f) for _r, _d, f in os.walk(staging))
            if copied < expected:
                raise RuntimeError(
                    f"copied {copied} of {expected} files; install aborted")

            previous = target + ".old"
            shutil.rmtree(previous, ignore_errors=True)
            if os.path.isdir(target):
                os.replace(target, previous)
            os.replace(staging, target)
            shutil.rmtree(previous, ignore_errors=True)

            exe = os.path.join(target, EXE_NAME)
            icon = os.path.join(target, "translator.ico")
            if not os.path.exists(icon):
                icon = exe

            self.say("Creating shortcuts...", 0.35)
            made = 0
            for folder in (shell_folder("Desktop"), shell_folder("Programs")):
                if folder:
                    make_shortcut(exe, os.path.join(folder, f"{APP_NAME}.lnk"),
                                  icon, target)
                    made += 1
            if not made:
                raise RuntimeError("could not locate Desktop or Start Menu")

            self.write_uninstaller(target)

            self.say("Registering with Windows...", 0.45)
            try:
                size = sum(os.path.getsize(os.path.join(r, f))
                           for r, _d, fs in os.walk(target) for f in fs)
                register_in_settings(target, exe, icon, size)
            except OSError as exc:
                # Not fatal: uninstall.bat still works without the entry
                print("registry entry skipped:", exc)

            self.say("Downloading the speech model (about 145 MB). "
                     "This happens once.", 0.5)
            try:
                # Bounded: if this build predates --setup it would open the
                # app instead and block here forever.
                proc = subprocess.run(
                    [exe, "--setup"], capture_output=True, text=True, timeout=900,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                code = proc.returncode
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/IM", EXE_NAME, "/F"],
                               capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                code = 1
            if code != 0:
                # Not fatal: the app downloads it on first launch instead
                self.say("Installed. The speech model will download when you "
                         "first open the app.", 1.0)
            else:
                self.say("Done. Live Translator now works offline.", 1.0)

            self.finish(exe)
        except Exception as exc:
            import traceback

            report = os.path.join(
                os.environ.get("TEMP", "."), "LiveTranslator-install.log")
            try:
                with open(report, "w", encoding="utf-8") as handle:
                    handle.write(traceback.format_exc())
            except OSError:
                pass
            self.say(f"Install failed: {exc} (details: {report})")
            self.root.after(0, lambda: self.button.config(
                state="normal", text="Retry"))

    def write_uninstaller(self, target):
        desktop = shell_folder("Desktop") or ""
        programs = shell_folder("Programs") or ""
        script = "\n".join([
            "@echo off",
            f'echo Removing {APP_NAME}...',
            f'del /q "{os.path.join(desktop, APP_NAME)}.lnk" 2>nul',
            f'del /q "{os.path.join(programs, APP_NAME)}.lnk" 2>nul',
            # cd out first: rmdir cannot remove the directory it is run from,
            # and timeout fails outright when stdin is redirected
            'cd /d "%TEMP%"',
            'ping -n 2 127.0.0.1 >nul',
            'reg delete "HKCU' + chr(92) + UNINSTALL_KEY + '" /f >nul 2>&1',
            f'rmdir /s /q "{target}"',
        ])
        with open(os.path.join(target, "uninstall.bat"), "w",
                  encoding="utf-8") as handle:
            handle.write(script)

    def finish(self, exe):
        def apply():
            self.button.config(state="normal", text="Open now",
                               command=lambda: self.launch(exe))
        self.root.after(0, apply)

    def launch(self, exe):
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
        self.root.destroy()

    def run_ui(self):
        self.root.mainloop()


if __name__ == "__main__":
    Installer().run_ui()
