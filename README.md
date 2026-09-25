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
| [How it works](docs/architecture.md) | The package layout, the pipeline, threading, and how streaming captions decide what to show |
| [Troubleshooting](docs/troubleshooting.md) | Captions not appearing, garbled words, Smart App Control, microphone level |
| [Improving accuracy](docs/improving-accuracy.md) | Where the errors come from, and what Google Cloud and other services would change |
| [Offline voices research](docs/research/offline-tts-for-pdf-reader.md) | Natural text-to-speech for the planned PDF reader |

## Running it

The app runs from source. There is no installer.

```
pip install -r requirements.txt
python -m livetranslator
```

Then use any of these to start it:

* the **Live Translator** shortcut on the Desktop or in the Start menu
* `run.bat` in this folder, which starts it without a console window
* `python -m livetranslator`, which keeps a console open for tracebacks

Optional startup flags, which `run.bat` passes through:

* `--device <text>` picks an input by name, microphone or system audio
* `--input auto|en|zt|sw` sets the spoken language
* `--target auto|en|zt|sw` sets what gets displayed

The speech model downloads on first run, about 145 MB, and after that the app
works offline.

## Using it

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
mode, response speed, sensitivity, speech engine, AI translation, size,
opacity, and the transcript actions.

## Features

* **Two capture modes.** *Phrase* waits for a pause and then captions the
  whole phrase. *Streaming* shows words as they are spoken, with unconfirmed
  words dimmed, and roughly halves the delay. Choose under **Captions**.
* **Session transcripts**, saved as you go and kept if the app crashes.
* **Microphone or system audio**, switchable from the panel.
* **Auto-detected or pinned languages**, for both what is spoken and what is
  shown.
* **Optional cloud AI.** Add a Claude or Gemini key under **AI translation**
  and translation runs through it, with the local models as an automatic
  fallback.

## Transcripts

Each session writes two files into `transcripts/`, named by date and time:

* `session <date> <time>.html` is the one to read: time, language direction,
  what was heard and what was shown. It prints legibly.
* `session <date> <time>.jsonl` is appended a line at a time and flushed, so
  it holds everything up to the moment the app closed or crashed.

Nothing is written until something is said. **Save transcript and open it** in
the menu works mid-session; closing the app writes the readable copy.

## Optional: your own AI key

With a Claude or Gemini key, translation runs through that model instead of the
local ones. It is noticeably better, especially for Kiswahili. Gemini can also
do speech-to-text; Claude is text only.

The key is encrypted with Windows DPAPI in `settings.json`. It is never stored
in plain text, is never shown back in the app, and only leaves the machine in
the API calls you are paying for. If a cloud call fails mid-session, the local
models take over and the caption says so.

## What the app writes

All of it sits in this folder and none of it is committed:

| File | Holds |
|---|---|
| `translator.log` | Every phrase, transcript, translation and failure, with timing. The first place to look when captions stop |
| `settings.json` | The AI provider and encrypted key |
| `transcripts/` | Session transcripts |

## Known limitations

* **Accuracy depends heavily on the microphone.** A built-in laptop microphone
  at a distance produces most of the errors seen in testing. See
  [Improving accuracy](docs/improving-accuracy.md).
* **Kiswahili speech recognition is poor offline.** Whisper garbles it at every
  model size fast enough for live use. Use a cloud engine for Kiswahili.
* **Kiswahili idiom translates weakly offline.** "Habari za asubuhi" becomes
  "News in the morning".
* **Streaming captions need the local speech model.** With Google or Gemini as
  the speech engine, capture falls back to phrase mode.
* **There is no installer yet.** Sharing the app with other people is a
  separate piece of work.
