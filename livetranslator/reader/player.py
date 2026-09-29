"""Reading a document aloud: make the audio ahead, play it, say where it is.

Two threads. One synthesises sentences into a small queue, keeping a little
ahead of playback; the other writes audio to the sound card. Kokoro runs
about 2.5 times faster than real time here, so the queue stays full and the
reader never waits after the first sentence.

Nothing here knows about a window. The window asks `position()` on a timer,
which is the same way the caption panel follows the microphone level.
"""
import queue
import threading

import numpy as np

from ..log import log
from .speakable import defined_abbreviations, speakable
from .voice import SAMPLE_RATE, Spoken, sentences

AHEAD = 2           # sentences to keep ready
SILENCE = np.zeros(0, dtype=np.float32)
CHUNK = 1024        # frames per write: about 43 ms, so pausing feels immediate


class Passage:
    """One sentence of the document, and where it came from on the page."""

    def __init__(self, index, text, source, page, bbox, reason):
        self.index = index
        self.text = text          # what the voice is given, after speakable()
        # The sentence as it is printed. The spoken form says "S-O-C", which
        # would never be found on the page, so the highlight searches this.
        self.source = source
        self.page = page
        self.bbox = bbox
        self.reason = reason      # title, heading, text, quote...
        self.spoken = None        # filled in when synthesised


def passages(lines):
    """Split the reading plan into sentences, keeping each one's place.

    Abbreviations are collected from the whole document first, so a paper
    that defines one on page 2 says it the same way on page 7.
    """
    known = defined_abbreviations(" ".join(line.text for line in lines))
    out = []
    for line in lines:
        for sentence in sentences(line.text):
            out.append(Passage(len(out), speakable(sentence, known), sentence,
                               line.page, line.bbox, line.reason))
    return out


class Player:
    """Plays a list of passages, and can be asked where it is."""

    def __init__(self, voice, items, speed=1.0, on_change=None, into=None,
                 voice_name=None):
        self.voice = voice
        self.items = items
        self.speed = speed
        self.voice_name = voice_name      # None reads in the voice's default
        self.on_change = on_change or (lambda index: None)
        # None reads the paper as written; a Translation reads it in another
        # language, in that language's voice.
        self.into = into

        self._ready = queue.Queue(maxsize=AHEAD)
        self._stop = threading.Event()
        self._playing = threading.Event()
        self._index = 0            # what is being spoken now
        self._elapsed = 0.0        # seconds into that passage
        self._lock = threading.Lock()
        self._make = self._play = None
        self._stream = self._audio = None

    # -- what the window asks ---------------------------------------------
    def position(self):
        """(index of the passage being read, seconds into it)."""
        with self._lock:
            return self._index, self._elapsed

    @property
    def playing(self):
        return self._playing.is_set() and not self._stop.is_set()

    def start(self, index=0):
        self.stop()
        self._stop.clear()
        with self._lock:
            self._index, self._elapsed = index, 0.0
        self._next = index         # the next one to synthesise
        self._make = threading.Thread(target=self._synthesise, daemon=True)
        self._play = threading.Thread(target=self._playback, daemon=True)
        self._playing.set()
        self._make.start()
        self._play.start()

    def pause(self):
        self._playing.clear()

    def resume(self):
        self._playing.set()

    def toggle(self):
        self.pause() if self.playing else self.resume()

    def stop(self):
        self._stop.set()
        self._playing.set()        # let a paused playback thread notice
        for t in (self._make, self._play):
            if t and t.is_alive():
                t.join(timeout=2.0)
        self._drain()
        self._close_stream()
        self._playing.clear()

    def jump(self, index):
        """Start again from one passage, keeping play or pause as it was."""
        was_playing = self.playing
        self.start(max(0, min(index, len(self.items) - 1)))
        if not was_playing:
            self.pause()

    # -- the two threads ---------------------------------------------------
    def _synthesise(self):
        while not self._stop.is_set() and self._next < len(self.items):
            item = self.items[self._next]
            try:
                item.spoken = (
                    self.voice.say(item.text, speed=self.speed,
                                   voice=self.voice_name)
                    if self.into is None
                    else self.into.say(self.voice, item, self.speed))
            except Exception as e:
                log(f"reader: could not speak {item.text[:40]!r}: "
                    f"{type(e).__name__}: {e}")
                # A sentence that will not synthesise is skipped rather than
                # stopping the reading; silence would look like a crash.
                item.spoken = Spoken(item.text, SILENCE, [])
            self._next += 1
            while not self._stop.is_set():
                try:
                    self._ready.put(item, timeout=0.2)
                    break
                except queue.Full:
                    continue

    def _playback(self):
        while not self._stop.is_set():
            try:
                item = self._ready.get(timeout=0.3)
            except queue.Empty:
                if self._next >= len(self.items) and self._ready.empty():
                    break          # everything made and played
                continue
            with self._lock:
                self._index, self._elapsed = item.index, 0.0
            self.on_change(item.index)
            self._write(item.spoken)
        self._close_stream()
        self._playing.clear()

    def _write(self, spoken):
        audio = spoken.audio
        stream = self._open_stream()
        if stream is None:
            return
        for at in range(0, len(audio), CHUNK):
            if self._stop.is_set():
                return
            while not self._playing.is_set() and not self._stop.is_set():
                threading.Event().wait(0.05)
            block = audio[at:at + CHUNK]
            try:
                stream.write(block.astype("float32").tobytes())
            except Exception as e:
                log(f"reader: playback failed: {type(e).__name__}: {e}")
                return
            with self._lock:
                self._elapsed = (at + len(block)) / SAMPLE_RATE

    # -- the sound card ----------------------------------------------------
    def _open_stream(self):
        if self._stream is not None:
            return self._stream
        try:
            import pyaudio

            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=pyaudio.paFloat32, channels=1, rate=SAMPLE_RATE,
                output=True, frames_per_buffer=CHUNK)
        except Exception as e:
            log(f"reader: no audio output: {type(e).__name__}: {e}")
            self._stream = None
        return self._stream

    def _close_stream(self):
        for close, obj in ((lambda s: (s.stop_stream(), s.close()), self._stream),
                           (lambda a: a.terminate(), self._audio)):
            if obj is not None:
                try:
                    close(obj)
                except Exception:
                    pass
        self._stream = self._audio = None

    def _drain(self):
        while not self._ready.empty():
            try:
                self._ready.get_nowait()
            except queue.Empty:
                break

