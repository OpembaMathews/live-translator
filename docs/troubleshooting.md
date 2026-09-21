# Troubleshooting

Start with `translator.log` beside the app. It records when the device opened,
every phrase captured, what was heard, what was translated, and every failure.
Most problems below are recognisable from it within a few lines.

## It says "Listening" but no captions appear

**Check the startup threshold.** Each time the microphone opens, the log shows
a line like this:

```
listening on System default (threshold=107, meter scale=644)
```

The threshold is calibrated from one second of room noise at startup. In a
quiet room it sits roughly between 35 and 110. If it is in the thousands, the
room was loud during that second, so ordinary speech never crosses it. Restart
the app in a quiet moment and it recalibrates.

**Check the input is the one you are speaking into.** The same line names the
device. Switch with the microphone icon, or pick one under **Audio input**.

**Check for "phrase captured" lines.** If phrases are being captured but
nothing is heard, the audio is arriving but is not recognisable as speech. See
the next section.

## Captions appear but the words are wrong

This is almost always the audio, not the software. In testing, a clean
close-up recording of a voice was transcribed word for word, while the same
voice and the same code read from further away produced "lancet park" for
"living product".

In order of impact:

1. **Use a headset or external microphone.** Nothing else helps as much.
2. **Raise the input level** in Windows: Settings, System, Sound, your input
   device, Input volume. Turn on microphone boost if the device offers it.
3. **Speak closer, and keep hands off the keyboard and palm rest.** Low
   frequency handling and fan noise competes directly with speech.
4. **Raise Sensitivity to "Very far"** in the menu. The default is already
   "Far (presentation)".
5. **Pin the spoken language** under **Speaking** if you know it. Detection
   costs time and can pick the wrong language on short phrases.

To measure the level rather than guess, look at how the ring moves. It should
pulse clearly while you talk. If it barely moves, the level is too low for
reliable recognition.

## The ring flickers on and off

It is a level meter, not an on/off indicator. The microphone is only released
when you stop listening, which turns the microphone glyph red. The log records
every stop and start, and shows `paused` or `resumed`.

## Captions come out in the wrong language

If **Speaking** is pinned to one language and someone speaks another, the
log records `not en; sounded like ...` and the caption suggests switching to
auto detect. If it is on auto detect and keeps guessing wrong on short
phrases, pin it to the language actually being spoken.

## Kiswahili is poor

Offline recognition of Kiswahili is weak at every model size that is fast
enough for live use. Use the Google or Gemini speech engine for Kiswahili, and
add an AI key for translation.

## Windows blocked the app

Smart App Control blocks unsigned programs, including a built `.exe` from this
project, and offers no override. Run from source instead, since the Python
interpreter is signed:

```
pythonw qt_app.py
```

The lasting fix is code signing, which needs a certificate.

## "PyAV unavailable" in the log

Harmless. Smart App Control blocks one of PyAV's DLLs. The app does not need
PyAV for live audio and carries on without it. It would only matter for
decoding audio files, which the app does not do.

## Captions lag badly or skip phrases

* Run only one copy of the app. Two copies each load a speech model and
  compete for the processor; one test went from 1.5 s to 36 s per sentence.
* Try **Captions: Streaming**, which shows words while they are spoken.
* Try **Response: Fast** in phrase mode.
* `(behind - dropped an older phrase)` in the log means recognition fell
  behind and the oldest waiting phrase was discarded.

## The transcript is missing

Transcripts are only created once something is said. Look in the
`transcripts` folder beside the app, or use **Open the transcript folder** in
the menu. The `.jsonl` file is always up to date; the `.html` copy is written
when you choose **Save transcript and open it** or when the app closes.
