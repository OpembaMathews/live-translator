# Running on macOS and Linux

**On Windows, none of this applies.** Use the installer, or `run.bat` and
`read.bat` to run from source. `./run.sh` in PowerShell answers "command not
found", because a shell script is not something Windows knows how to run.

The app runs from source on all three systems. There is no Mac installer:
building a `.app` needs a Mac, and shipping one without a $99-a-year Apple
Developer account means every user meets *"cannot be opened because the
developer cannot be verified"*. Running from source skips all of that.

## On a Mac

```sh
brew install portaudio             # PyAudio builds against this
pip3 install -r requirements.txt
python3 -m livetranslator.models   # the voice and the language packs
chmod +x run.sh read.sh            # a zip does not carry the executable bit
./run.sh
```

If `./run.sh` says "permission denied", the `chmod` line was missed. If it
says "no such file", you are not in the folder that was unzipped.

`./read.sh` opens the paper reader on its own, and takes a PDF path.

`brew install portaudio` is the one extra step. PyAudio publishes no wheel
for Apple silicon, so pip builds it, and the build needs PortAudio's headers.
Everything else installs as a wheel.

## On Linux

```sh
sudo apt install portaudio19-dev    # or the equivalent
pip3 install -r requirements.txt
python3 -m livetranslator.models
chmod +x run.sh read.sh
./run.sh
```

## What works, and what does not

| | Windows | macOS | Linux |
|---|---|---|---|
| Captions from a microphone | yes | yes | yes |
| Captions from system audio | yes | **needs BlackHole** | yes |
| The paper reader | yes | yes | yes |
| Translation, the voice, transcripts | yes | yes | yes |
| The API key kept out of plain text | DPAPI | Keychain | **encoded only** |

### System audio on a Mac

macOS offers no way for a program to capture what the speakers are playing.
Apple simply does not expose it, which is why `PyAudioWPatch` is marked as a
Windows-only dependency and the app finds no system-audio devices on a Mac.

The way round it is [BlackHole](https://github.com/ExistentialAudio/BlackHole),
a free virtual audio device. Once installed it appears as an ordinary *input*,
so the app treats it like a microphone and needs no special handling. Sound
has to be routed to it, usually through a Multi-Output Device in Audio MIDI
Setup so you can still hear what is playing.

### The API key on Linux

Windows encrypts it with DPAPI and macOS keeps it in the Keychain. Linux has
no store the app can rely on without adding a dependency, so the key is only
base64 there -- which is not security, just a guard against reading it by
accident. The app writes a line in the log saying so rather than letting
anyone assume otherwise.

## Where files are kept

| | |
|---|---|
| Windows | `%LOCALAPPDATA%\LiveTranslator` |
| macOS | `~/Library/Application Support/LiveTranslator` |
| Linux | `$XDG_DATA_HOME/LiveTranslator`, or `~/.local/share/LiveTranslator` |

Models, settings, transcripts, the translation memory and the corrections
folder all live there.

## If it will not start

Run it without the launcher so the traceback is visible:

```sh
python3 -m livetranslator
```

`python3 -m pytest tests/ -q` runs the test suite, which includes a check
that no Windows-only module is imported when a file is read. That check
exists because `settings.py` once imported `ctypes.wintypes` at the top of
the file: on a Mac that raises, so the app did not fail at the AI-key
feature, it failed at `import livetranslator` and never opened a window.
