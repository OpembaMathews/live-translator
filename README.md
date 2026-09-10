# Live Translator

A floating caption bar that listens, transcribes and translates in real time.
Everything runs locally by default - no API keys, no per-use cost, and audio
never leaves the machine.

Built for two situations:

* **Presenting** - it hears you through a microphone and captions you in
  another language for the audience.
* **Watching or listening** - it taps system audio, so a video, podcast or
  call gets captioned without a microphone at all.

## Languages

English, Traditional Chinese and Kiswahili, in any direction. English is the
pivot, so Chinese to Kiswahili works without a dedicated model.

## Optional: your own AI key

If you have a Claude or Gemini subscription, add the key and translation runs
through that instead of the local models - noticeably better, especially for
Kiswahili. Nothing extra to install or download.

Add it during setup, or any time from the app menu (**Translation → Use my AI
key**). Turn it off the same way; the local models are always there as a
fallback and take over automatically if a cloud call fails.

Gemini can also handle speech-to-text, so a Gemini user can skip the local
speech model entirely. Claude is text only, so with Claude the speech step
still runs locally or through Google.

The key is encrypted with Windows DPAPI in `settings.json` beside the app - it
is never stored in plain text and never leaves the machine except in the API
calls you are paying for.

## How it works

```
audio  ->  speech to text  ->  translation  ->  caption
           Whisper (local)     Argos models
           or Google           on CTranslate2
```

Translation runs Argos's language packs directly on CTranslate2 rather than
through `argostranslate`, which pulls in stanza and torch. Skipping that chain
removes about 760MB from a build and avoids torch entirely.

## Running from source

```
pip install -r requirements.txt
pythonw translation.py
```

Optional startup flags, useful for scripting:

```
pythonw translation.py --device "system audio (" --input auto --target en
```

* `--device <text>` picks an input by name (microphone or system audio)
* `--input auto|en|zt|sw` sets the spoken language
* `--target auto|en|zt|sw` sets what gets displayed
* `--setup` downloads the speech model and verifies translation, then exits

The AI key lives in `settings.json` (encrypted); set it through the app rather
than editing the file.

## Using it

Controls sit on the panel itself: record/stop, menu, close. The menu covers
audio input, speech engine, spoken language, display language, sensitivity,
response speed, size and opacity.

The panel is draggable, resizable from the bottom-right corner, and closes
with Escape.

## Building

```
python build.py              # folder build
python build.py --onefile    # single exe, slower to start
python build.py --installer  # installer, this is the one to distribute
```

The installer unpacks to `%LOCALAPPDATA%\Programs`, creates shortcuts,
registers in Windows Settings, and pre-downloads the speech model. Translation
packs are bundled, so translation works offline immediately.

## Known limitations

* **Kiswahili speech recognition is poor offline.** Whisper garbles it at every
  model size that is fast enough for live captioning. Use the Google engine for
  Kiswahili; English and Chinese are accurate locally.
* Greetings and idiom translate weakly in Kiswahili - "Habari za asubuhi"
  becomes "News in the morning". Swahili clock time (`saa nne` is 10am, not
  4am) is not converted.
* Unsigned builds are blocked by Windows Smart App Control. Running from source
  works, since the Python interpreter is signed.

## Logs

`translator.log` sits beside the app and records every phrase, transcript,
translation and failure with timing. It is the first place to look when
captions stop appearing.
