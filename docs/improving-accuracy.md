# Improving accuracy

What limits the captions today, what would improve them, and what integrating
Google Cloud would involve.

## Where the errors actually come from

A caption can be wrong at two stages, and they need different fixes.

```
audio ──► speech to text ──► translation ──► caption
          (mishears)         (mistranslates)
```

Live testing points at the first stage. Reading a known passage aloud, the
recogniser produced "lancet park" for "living product" and "student ST" for
"graduate student at NTUST". Translation then faithfully translated the wrong
words. No translation service can recover a word the recogniser never heard.

The recogniser itself is capable. The same model, with the same settings,
transcribed a clean close-up recording of the same voice word for word. What
changed was the audio:

| Recording | Level | Result |
|---|---|---|
| Close to the laptop | usable | "Good morning and let's try and see what is happening in Kenya today." |
| Reading at normal distance | peak around 9% of full scale | fragments and misheard words |

So improvements, in the order they pay off:

1. **Better audio in.** A headset or external microphone.
2. **Better speech recognition.** A larger local model, or a cloud recogniser.
3. **Better translation.** A cloud translator, which matters most for
   Kiswahili.

## Option 1: better audio

Costs nothing in engineering and is the largest single improvement.

* A headset or lapel microphone, which keeps the voice loud relative to the
  room.
* Raising the input level in Windows, and microphone boost if offered.
* Pinning the spoken language, so short phrases are not misdetected.

## Option 2: better speech recognition

| Choice | Accuracy | Speed on this laptop | Cost | Offline |
|---|---|---|---|---|
| Whisper `base` (current) | good on clean audio | 0.6 s for 4 s of speech | free | yes |
| Whisper `small` | better, especially noisy audio and Kiswahili | an estimated 3 times slower, from its size; likely too slow for streaming | free, about 480 MB download | yes |
| Google Cloud Speech-to-Text | strong, with native streaming | network bound | see below | no |
| Gemini, already supported | strong | network bound | your Gemini plan | no |

Google Cloud Speech-to-Text is the natural match for streaming captions,
because the service itself streams: it returns interim words as you speak and
final words when it is sure, which is exactly what the local streaming mode
reconstructs by re-reading a buffer.

## Option 3: better translation, with Google Translate

**Yes, Google Translate would improve translation**, particularly for
Kiswahili, where the offline models are weakest. It will not fix misheard
words, so it pays off most once the audio is good.

### Why the old Google option was removed

The app used to contain a Google Translate engine built on `googletrans`, an
unofficial library that scrapes Google's website. It had stopped importing,
because of an incompatibility with a newer version of one of its
dependencies, and scraped endpoints also break without notice and are not
permitted for products. It was removed; the official API below is the
replacement.

### What you need for the official service

1. **A Google Cloud account and a project.** New accounts get $300 of trial
   credit.
2. **Billing enabled on the project.** Required even to use the free tier; a
   card must be on file.
3. **The Cloud Translation API enabled** in that project.
4. **An API key**, restricted to the Cloud Translation API only, so a leaked
   key cannot be used for anything else.

That is all the app needs. The key would be stored the same way the Claude and
Gemini keys are today, encrypted with Windows DPAPI.

### What it costs

Official prices, checked September 2026:

| Service | Free each month | Then |
|---|---|---|
| Cloud Translation, Basic or Advanced | first 500,000 characters, shared between the two | $20 per million characters |
| Speech-to-Text, standard real-time | first 60 minutes | $0.016 per minute |

Rough estimate for live speech, at about 150 words per minute translated into
one language:

| | Per hour of speech | Free allowance covers |
|---|---|---|
| Translation | about 54,000 characters, about $1.08 beyond the free tier | roughly 9 hours a month |
| Speech-to-Text | 60 minutes, about $0.96 | 1 hour a month |

Two things raise the translation figure. Each extra display language multiplies
it. And streaming mode re-translates the sentence in progress every step, so
with a paid translator it should only send finished sentences, or it would use
two to three times as many characters.

### How it would fit into the app

* A new translation engine calling the Cloud Translation REST API with the key.
  Like the existing Claude and Gemini engines, it needs no extra library.
* Language codes mapped at the edge: the app's `zt` becomes Google's `zh-TW`.
  Kiswahili is `sw` in both. Both are officially supported.
* An entry alongside the AI key setting, so it can be switched on and off
  mid-session.
* The local models kept as the automatic fallback, as they are for Claude and
  Gemini: if a call fails, captions continue offline and the panel says so.
* For streaming, translate a sentence once it is finished, and cache it.

Google Cloud Speech-to-Text would be a separate, larger piece of work. Its
streaming interface runs over gRPC through Google's client library and is
normally authenticated with a service account rather than a plain key.

## Recommendation

1. **Fix the audio first.** A headset changes more than any service.
2. **Add Google Cloud Translation** as an engine. Small, contained, cheap,
   and a clear win for Kiswahili.
3. **Then decide on recognition.** If misheard words remain with good audio,
   compare Whisper `small` in phrase mode against Google Speech-to-Text on the
   same recording, word for word, before committing to either.

## Sources

* [Cloud Translation pricing](https://cloud.google.com/products/translate/pricing)
* [Speech-to-Text pricing](https://cloud.google.com/speech-to-text/pricing)
* [Cloud Translation supported languages](https://docs.cloud.google.com/translate/docs/languages)
