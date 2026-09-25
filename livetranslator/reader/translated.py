"""Reading a paper aloud in another language.

The page on screen does not change: the English sentence stays tinted while
the translation is spoken over it, so a student can see which sentence they
are hearing. That is the whole point of reading a journal this way, and it
is why the translation is done sentence by sentence rather than as one block
of text -- each spoken sentence has to line up with one place on the page.

Sentences are translated as they are needed, one ahead of the voice, and
kept, so stepping back over a paragraph does not pay for it twice.
"""
import threading

from ..log import log

# Kokoro names a voice for its language and its speaker: "a" is American,
# "b" British and "z" Mandarin, then "f" or "m". Three to a language is
# enough to choose between without turning this into a catalogue, and each
# is one the project grades well.
VOICES = {
    "en": (("Heart \u00b7 female", "af_heart"),
           ("Michael \u00b7 male", "am_michael"),
           ("Puck \u00b7 male", "am_puck")),
    "zt": (("Xiaoxiao \u00b7 female", "zf_xiaoxiao"),
           ("Yunxi \u00b7 male", "zm_yunxi"),
           ("Yunyang \u00b7 male", "zm_yunyang")),
}
# What espeak-ng calls each language. It has no "zh"; Mandarin is "cmn".
PHONEMES = {"en": "en-us", "zt": "cmn"}


def voices_for(language):
    return VOICES.get(language, ())


def default_voice(language):
    return VOICES[language][0][1]


def can_speak(language):
    return language in VOICES


class Translation:
    """Turns each passage into the sentence to speak, in another language.

    A failure here is not allowed to stop the reading: if a sentence will not
    translate -- no route, no network, a rate limit -- the English is spoken
    instead and the reason is logged once.
    """

    def __init__(self, target, engine, voice=None, source="en"):
        self.target = target
        self.engine = engine
        self.source = source
        self.voice_name = voice or default_voice(target)
        self.lang = PHONEMES[target]
        self._done = {}
        self._lock = threading.Lock()
        self._complained = False

    def text_for(self, passage):
        """The passage in the target language, or the original if it fails."""
        with self._lock:
            if passage.index in self._done:
                return self._done[passage.index]
        try:
            said = self.engine.translate(passage.source, self.source,
                                         self.target).strip()
        except Exception as e:
            if not self._complained:
                self._complained = True
                log(f"reader: translation unavailable, reading the original: "
                    f"{type(e).__name__}: {e}")
            said = ""
        said = said or passage.source
        with self._lock:
            self._done[passage.index] = said
        return said

    def say(self, voice, passage, speed):
        """Speak one passage, in this language's voice."""
        return voice.say(self.text_for(passage), speed=speed,
                         voice=self.voice_name, lang=self.lang)
