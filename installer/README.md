# Building the Windows installer

```powershell
winget install JRSoftware.InnoSetup      # once
.\installer\build.ps1
```

The installer lands in `dist\LiveTranslator-<version>-setup.exe`.

## Why the installer is small

It carries the app and a private Python, and nothing else.

| | size | when |
|---|---|---|
| The installer | about 20 MB | you ship it |
| Libraries from PyPI | about 200 MB | while the installer runs |
| Voice and translation packs | about 650 MB | first run, with a progress window |
| Whisper speech model | 145 MB | first time it listens |

Bundling the models would make a 1.9 GB download, which most people abandon.
Splitting it this way also lets someone install now and fetch the models on
better wifi later: the first-run window has a **Not now** button.

## What `build.ps1` does

1. Downloads the embeddable Python build and unpacks it into `payload\runtime`.
2. Turns on `import site` in its `._pth` file, without which pip installs
   packages the interpreter cannot see.
3. Installs pip with `get-pip.py`, because the embeddable build has no
   `ensurepip`.
4. Writes the three launchers.
5. Runs Inno Setup.

Run it with `-SkipCompile` to prepare the payload without building the
installer, which is useful for testing the runtime on its own.

## Installing without an administrator

`PrivilegesRequired=lowest`, so it installs per-user and needs no password.
A student on a managed or shared laptop can install it without asking IT.

## Where things end up

| | |
|---|---|
| The app, the private Python, the installed libraries | the install folder |
| Models, settings, transcripts, translation memory, corrections | `%LOCALAPPDATA%\LiveTranslator` |

Uninstalling removes the first and leaves the second. Someone who
reinstalls keeps their transcripts and does not download the models again.

## Unsigned, and what that means

The installer is not code-signed, so Windows SmartScreen shows
"Windows protected your PC" until the file builds a reputation. A user gets
past it with **More info -> Run anyway**, which is worth saying in whatever
note ships with the download.

Signing needs a certificate (roughly $200-400 a year) and even then warns
until reputation accrues. Publishing through the Microsoft Store avoids the
warning entirely for a one-off $19 developer account, and is the better
route if this goes beyond a handful of testers.
