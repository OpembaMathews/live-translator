# Live Translator

A floating caption bar that listens, transcribes and translates in real time.
Everything runs locally by default: no API keys, no per-use cost, and audio
never leaves the machine.

Built for two situations:

* **Presenting.** It hears you through a microphone and captions you in
  another language for the audience.
* **Watching or listening.** It taps system audio, so a video, podcast or call
  gets captioned without a microphone at all.

Languages are English, Traditional Chinese and Kiswahili, in any direction.
English is the pivot, so Chinese to Kiswahili works without a dedicated model.

## Documentation

| Document | Read it for |
|---|---|
| [How it works](docs/architecture.md) | The pipeline, the modules, threading, and how streaming captions decide what to show |
| [Troubleshooting](docs/troubleshooting.md) | Captions not appearing, garbled words, Smart App Control, microphone level |
| [Improving accuracy](docs/improving-accuracy.md) | Where the errors come from, and what Google Cloud and other services would change |

## Two front ends

The same engine runs behind either window. Pick one.

| | Start it with | Window |
|---|---|---|
| **Qt** (recommended) | `Start Live Translator (Qt).bat` or `pythonw qt_app.py` | Pill design with a level ring, status pills, and the heard line above the translated line |
| **Tk** (original) | `Start Live Translator.bat` or `pythonw translation.py` | Single caption line with a radio-wave meter |

Only the window differs. Capture, recognition, translation, settings storage
and transcripts are shared code.

## Features

* **Two capture modes.** *Phrase* waits for a pause and then captions the
  whole phrase. *Streaming* shows words as they are spoken, with unconfirmed
  words dimmed, and roughly halves the delay. Choose under **Captions** in the
  menu.
* **Session transcripts.** Everything heard and shown is saved as you go, in a
  `transcripts` folder beside the app, and survives a crash.
* **Microphone or system audio**, switchable from the panel.
* **Auto-detected or pinned languages**, for both what is spoken and what is
  shown.
* **Optional cloud AI.** Add a Claude or Gemini key and translation runs
  through it, with the local models as an automatic fallback.
* **Sharp on high-DPI displays.** Both windows render at the display's real
  resolution instead of being stretched by Windows.

## Running from source

```
pip install -r requirements.txt
pythonw qt_app.py
```

`PySide6-Essentials` in the requirements is only needed for the Qt window.
`translation.py` runs without it.

Optional startup flags, for either front end:

* `--device <text>` picks an input by name, microphone or system audio
* `--input auto|en|zt|sw` sets the spoken language
* `--target auto|en|zt|sw` sets what gets displayed
* `--setup` downloads the speech model and verifies translation, then exits

## Using it (Qt window)

| Control | Does |
|---|---|
| The ring on the left | Shows the input level. Click it to stop or start listening; the microphone turns red when stopped |
| Microphone icon | Switches between microphone and system audio |
| Gear | Opens settings |
| X, or Escape | Quits and saves the transcript |
| Drag anywhere | Moves the window |
| Bottom-right corner | Resizes; the layout follows the size |
| Double-click | Cycles Small, Medium and Large |

The gear menu covers audio input, spoken language, display language, capture
mode, response speed, sensitivity, speech engine, size, opacity, and the
transcript actions.

## Transcripts

Each session writes two files into `transcripts/`, named by date and time:

* `session <date> <time>.html` is the one to read: time, language direction,
  what was heard and what was shown. It prints legibly.
* `session <date> <time>.jsonl` is appended a line at a time and flushed, so
  it holds everything up to the moment the app closed or crashed.

Nothing is written until something is said. **Save transcript and open it** in
the menu works mid-session; closing the app writes the readable copy
automatically.

## Optional: your own AI key

With a Claude or Gemini key, translation runs through that model instead of the
local ones. It is noticeably better, especially for Kiswahili, and needs
nothing extra installed. Gemini can also do speech-to-text; Claude is text
only.

The key is encrypted with Windows DPAPI in `settings.json` beside the app. It
is never stored in plain text and only leaves the machine in the API calls you
are paying for. If a cloud call fails mid-session, the local models take over
and the caption says so.

## Building

```
python build.py              # folder build
python build.py --onefile    # single exe, slower to start
python build.py --installer  # installer, the one to distribute
```

The installer unpacks to `%LOCALAPPDATA%\Programs`, creates shortcuts,
registers in Windows Settings, and pre-downloads the speech model. Translation
packs are bundled, so translation works offline immediately.

The build currently packages the Tk front end. Packaging the Qt window is not
done yet.

## Known limitations

* **Accuracy depends heavily on the microphone.** A built-in laptop microphone
  at a distance produces most of the errors seen in testing. See
  [Improving accuracy](docs/improving-accuracy.md).
* **Kiswahili speech recognition is poor offline.** Whisper garbles it at every
  model size fast enough for live use. Use a cloud engine for Kiswahili.
* **Kiswahili idiom translates weakly offline.** "Habari za asubuhi" becomes
  "News in the morning".
* **Unsigned builds are blocked by Windows Smart App Control.** Running from
  source works, since the Python interpreter is signed.
* **Streaming captions need the local speech model.** With Google or Gemini as
  the speech engine, capture falls back to phrase mode.
* The Tk window has no menu entry for streaming captions yet.

## Logs

`translator.log` sits beside the app and records every phrase, transcript,
translation and failure with timing. It is the first place to look when
captions stop appearing.
