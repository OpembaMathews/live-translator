# How Live Translator works

This is the map of the code: what each module does, how audio becomes a
caption, which thread does what, and why the less obvious decisions were made.

## The pipeline

```
 microphone or           speech to text        translation          window
 system audio   ──►      Whisper, local   ──►  Argos models    ──►  Qt window
                         or Google/Gemini      on CTranslate2,
                                               or Claude/Gemini
                                                      │
                                                      ▼
                                               transcript files
```

Every stage has a local option, so the app works with no network and no
account. Cloud services are opt-in replacements for individual stages, never a
requirement.

## The package

Everything is in `livetranslator/`, started with `python -m livetranslator`.

| Module | Lines | Role |
|---|---|---|
| `config.py` | ~140 | Languages, and every tunable threshold, each with the reason for its value |
| `paths.py` | ~15 | Where the log, `settings.json` and transcripts are written |
| `log.py` | ~35 | The log file |
| `text.py` | ~45 | Which script a text is in, and the other language |
| `settings.py` | ~120 | Reads and writes `settings.json`, encrypting the AI key with Windows DPAPI |
| `audio.py` | ~250 | Microphones, system-audio loopback, and the tap that feeds the level meter |
| `speech.py` | ~290 | Speech to text: `WhisperSTT` locally, `GoogleSTT` and `GeminiSTT` online |
| `translate.py` | ~240 | `LeanEngine` locally, `ClaudeEngine` and `GeminiEngine` with the user's key |
| `session.py` | ~120 | `SessionLog`, which writes the transcripts |
| `engine.py` | ~915 | `LiveTranslator`: the whole app except the window |
| `ui/app.py` | ~345 | `QtTranslator`: the engine wearing the window, plus the settings menu |
| `ui/caption.py` | ~790 | The caption window: painting, layout and input. Knows nothing about audio |
| `ui/ai_dialog.py` | ~130 | The AI key dialog |

The dependency runs one way. `engine.py` uses the modules above it and knows
nothing about Qt; `ui/` uses the engine. `LiveTranslator` leaves a few hooks
for a front end to override (`build_ui`, `on_ui`, `show_pair`, `show_stream`,
`show_status`, `run`, `close`), and its defaults only record state, so the
engine can run with no window at all, which is how it is tested.

## Why the local translation engine is "lean"

Argos Translate's own library imports stanza for sentence splitting, stanza
requires torch, and torch alone is about 526 MB. `LeanEngine` loads the same
Argos model files and drives them through CTranslate2 and SentencePiece
directly, with identical output. That removes roughly 760 MB of dependencies and
sidestepped Smart App Control, which blocks torch on this machine.

A pair without a direct model is routed through English, so Chinese to
Kiswahili is two hops.

## Threads

| Thread | Does |
|---|---|
| UI | Paints the window and handles clicks. Nothing slow runs here |
| Capture | Owns the audio device. Runs `listen_loop()`, which reopens the device whenever the selection changes |
| Recognition worker | Phrase mode only. Takes captured phrases from a queue, transcribes and translates them |

Worker threads never touch the window directly. They call `on_ui()`, which in
the Qt front end emits a queued signal through `Bridge`, because Qt widgets are
not safe to call from other threads.

## Two capture modes

### Phrase mode (default)

Waits for the speaker to pause, then sends the whole phrase for recognition.

* **Microphone:** energy-gated phrase detection. The speech threshold is
  calibrated from one second of room noise when the device opens, then scaled
  by the **Sensitivity** setting.
* **System audio:** media has no pauses to cut on, so it reads fixed windows
  that overlap, and every moment of audio appears whole in some window.

Captured phrases go on a queue for the recognition worker, so the microphone
keeps listening while the previous phrase is transcribed. The queue holds
three; when full, the oldest phrase is dropped rather than letting captions
fall further and further behind.

The **Response** setting trades delay against accuracy:

| Response | Pause that ends a phrase | Longest phrase | Media window |
|---|---|---|---|
| Fast | 0.45 s | 3.0 s | 2.5 s |
| Balanced | 0.65 s | 4.0 s | 3.0 s |
| Accurate | 0.90 s | 5.0 s | 5.0 s |

### Streaming mode

Shows words while the speaker is still talking. It exists because recognition
was never the bottleneck: the base model transcribes four seconds of speech in
about 0.6 s on this laptop. The delay came from waiting for the pause.

It keeps a rolling buffer and re-reads it every 0.9 s. A word is shown only
once **two consecutive reads agree on it**. Whisper constantly revises the end
of a buffer but almost never changes the start, so agreement is what makes an
early word safe to show. Words not yet agreed are drawn dimmed after the
confirmed ones.

Measured on the same 14.5 second clip:

| | Median delay behind the speaker | Words kept |
|---|---|---|
| Phrase | 4.22 s | cut at phrase limits |
| Streaming | 2.11 s | all 46 |

Each rule below was added because live testing broke without it:

| Rule | Why |
|---|---|
| Resample to 16 kHz | Microphones commonly run at 44.1 kHz. Unconverted audio reaches Whisper stretched to nearly three times its length and comes back empty |
| Wait for 2 s before the first read | The first read picks the language and sets the prefix everything else agrees against |
| Trim at each finished sentence, and past 8 s at the last confirmed word | Otherwise a speaker who never pauses makes each read slower than real time |
| Ignore words at or before the last confirmed one | Word end times are placed slightly early, so the tail of a word survives a trim and would be heard twice |
| End an utterance after 1 s of measured silence | Ending it when no new word was confirmed chopped sentences apart, because a quiet microphone makes reads disagree |
| Never send silence to the model | Whisper invents words from digital silence |
| Translate sentence by sentence, freezing finished ones | Re-translating the whole prefix rewrote earlier sentences under the reader |
| Fall back to the text's script when no language is detected | Short buffers often have no confident language, and the line was shown untranslated |

Streaming needs a recogniser that can be re-read cheaply, so it only runs with
local Whisper. With a cloud speech engine, capture falls back to phrase mode.

## Speech recognition guards

Whisper will return confident-looking text for audio that contains no speech,
or speech in another language. These thresholds reject that before it reaches
the screen:

| Guard | Rejects |
|---|---|
| Language probability under 0.55 | A detection that is a coin toss |
| No-speech probability over 0.60 | A segment that is probably silence |
| Average log probability under -1.0 | A segment that is probably invented |

Quiet input is lifted before recognition, by up to 15 times. Streaming lifts
by loudness rather than peak, so one loud transient does not leave the speech
itself too quiet.

## Settings and secrets

`settings.json` sits beside the app. The optional AI key is encrypted with
Windows DPAPI, which ties it to the Windows user account; the file cannot be
copied to another machine and read.

## Transcripts

`SessionLog` writes each finished utterance as a JSON line and flushes it
immediately, then rebuilds a readable HTML page from those lines on request
and on exit. Transcript text is HTML-escaped, since it is content the app did
not write.

## High-DPI displays

Qt renders at the display's real resolution itself, so geometry in the window
code is in logical units and nothing needs scaling by hand. (The retired
tkinter window had to declare DPI awareness and scale every coordinate, or
Windows drew it at 96 DPI and stretched the bitmap, blurring every glyph on a
125% display.)

## Things that look odd but are deliberate

* **A placeholder `av` module.** faster-whisper imports PyAV at package level
  but only calls it to decode audio files, which this app never does. Smart App
  Control blocks one of PyAV's DLLs, which took the whole speech engine down, so
  a placeholder stands in when the real module cannot load.
* **`<unk>` is removed from translations.** The local model emits it for a
  piece it has no word for, which is common on a garbled transcript.
* **The level meter rises fast and falls slowly.** Speech is bursty; a meter
  that falls as fast as it rises drops to zero between syllables and looks like
  the microphone cutting out.
