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
from .voice import DEFAULT_VOICE

# Which Kokoro voice reads which language, and what to phonemise it as.
# Kokoro ships eight Mandarin voices; espeak-ng calls the language "cmn".
SPEAKS = {
    "en": (DEFAULT_VOICE, "en-us"),
    "zt": ("zf_xiaoxiao", "cmn"),
}


def can_speak(language):
    return language in SPEAKS


class Translation:
    """Turns each passage into the sentence to speak, in another language.

    A failure here is not allowed to stop the reading: if a sentence will not
    translate -- no route, no network, a rate limit -- the English is spoken
    instead and the reason is logged once.
    """

    def __init__(self, target, engine, source="en"):
        self.target = target
        self.engine = engine
        self.source = source
        self.voice_name, self.lang = SPEAKS[target]
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
