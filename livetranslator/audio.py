"""Audio in: microphones, system-audio loopback, and the level meter tap.
"""

import speech_recognition as sr

try:
    import audioop  # removed from the stdlib in Python 3.13
except ImportError:  # pragma: no cover - depends on interpreter version
    audioop = None
    import numpy as _np


def chunk_rms(data, sample_width):
    """Loudness of one audio chunk, 0..32767 for 16-bit samples."""
    if audioop is not None:
        return audioop.rms(data, sample_width)
    dtype = _np.int16 if sample_width == 2 else _np.uint8
    samples = _np.frombuffer(data, dtype=dtype).astype(_np.float64)
    if samples.size == 0:
        return 0.0
    return float(_np.sqrt(_np.mean(samples ** 2)))


class LevelTap:
    """Wraps an audio stream and reports the loudness of every chunk read.

    speech_recognition owns the stream while listening, so this sits in the
    middle rather than opening a second stream on the same device.
    """

    def __init__(self, stream, sample_width, report):
        self._stream = stream
        self._width = sample_width
        self._report = report

    def read(self, size, *args, **kwargs):
        data = self._stream.read(size, *args, **kwargs)
        try:
            self._report(chunk_rms(data, self._width))
        except Exception:
            pass  # never let metering break capture
        return data

    def close(self):
        return self._stream.close()

    def __getattr__(self, name):
        return getattr(self._stream, name)

# Windows exposes the same physical microphone once per host API, so a raw
# device list is mostly duplicates. Rank the APIs and keep one entry per device.
# WDM-KS is deliberately absent: PyAudio consistently fails to open those
# endpoints (it raises during cleanup with a confusing NoneType error), so
# listing them would only offer devices that cannot work.
_API_RANK = {
    "MME": 0,
    "Windows DirectSound": 1,
    "Windows WASAPI": 2,
}
_SKIP_DEVICES = ("primary sound capture", "microsoft sound mapper", "pc speaker")
# Devices that capture what the PC is playing rather than a microphone
_LOOPBACK_HINTS = ("stereo mix", "loopback", "what u hear", "wave out")


class _MonoStream:
    """Loopback is stereo at 48kHz; speech_recognition wants mono. Downmix on
    the way through so the rest of the pipeline is unchanged."""

    def __init__(self, stream, channels):
        self._stream = stream
        self._channels = channels

    def read(self, frames, *args, **kwargs):
        kwargs.setdefault("exception_on_overflow", False)
        data = self._stream.read(frames, *args, **kwargs)
        if self._channels == 1:
            return data
        import numpy as np

        samples = np.frombuffer(data, dtype=np.int16)
        usable = (len(samples) // self._channels) * self._channels
        mixed = samples[:usable].reshape(-1, self._channels).mean(axis=1)
        return mixed.astype(np.int16).tobytes()

    def close(self):
        return self._stream.close()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class LoopbackSource(sr.AudioSource):
    """Captures what the PC is playing, via WASAPI loopback.

    Plain PyAudio cannot open loopback endpoints at all, which is why the
    Stereo Mix entries failed. PyAudioWPatch exposes them properly and needs
    no changes to Windows sound settings.
    """

    def __init__(self, device_index=None, chunk=1024):
        import pyaudiowpatch as pyaudio

        self._pyaudio = pyaudio
        self._audio = None
        self._raw_stream = None
        self.stream = None
        self.CHUNK = chunk
        self.SAMPLE_WIDTH = pyaudio.get_sample_size(pyaudio.paInt16)

        handle = pyaudio.PyAudio()
        try:
            info = (
                handle.get_device_info_by_index(device_index)
                if device_index is not None
                else handle.get_default_wasapi_loopback()
            )
            self.device_index = info["index"]
            self.channels = int(info["maxInputChannels"]) or 2
            self.SAMPLE_RATE = int(info["defaultSampleRate"])
        finally:
            handle.terminate()

    def __enter__(self):
        self._audio = self._pyaudio.PyAudio()
        try:
            self._raw_stream = self._audio.open(
                format=self._pyaudio.paInt16,
                channels=self.channels,
                rate=self.SAMPLE_RATE,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.CHUNK,
            )
        except Exception:
            self._audio.terminate()
            self._audio = None
            raise
        self.stream = _MonoStream(self._raw_stream, self.channels)
        return self

    def __exit__(self, *_exc):
        try:
            if self._raw_stream is not None:
                self._raw_stream.stop_stream()
                self._raw_stream.close()
        finally:
            self._raw_stream = None
            self.stream = None
            if self._audio is not None:
                self._audio.terminate()
                self._audio = None


def list_loopback_devices():
    """Outputs we can eavesdrop on - for translating a podcast or a call."""
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return []

    handle = pyaudio.PyAudio()
    devices = []
    try:
        # Index None follows whatever Windows is currently playing through,
        # which is almost always what you want - picking the wrong endpoint
        # (speakers while sound goes to headphones) just captures silence.
        try:
            current = handle.get_default_wasapi_loopback()["name"]
            current = current.replace("[Loopback]", "").strip()
            current = current.split("(")[0].strip() or current
        except Exception:
            current = None

        if current:
            devices.append({
                "index": None,
                "kind": "loopback",
                "label": f"System audio ({current})",
                "loopback": True,
            })

        for info in handle.get_loopback_device_info_generator():
            name = info["name"].replace("[Loopback]", "").strip()
            short = name.split("(")[0].strip() or name
            devices.append({
                "index": info["index"],
                "kind": "loopback",
                "label": f"System audio - {short}",
                "loopback": True,
            })
    except Exception:
        return []
    finally:
        handle.terminate()
    return devices


def list_input_devices():
    """Return [{'index', 'label', 'loopback'}] of usable audio inputs."""
    import pyaudio

    pa = pyaudio.PyAudio()
    best = {}
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info["maxInputChannels"] < 1:
                continue

            name = info["name"].strip()
            low = name.lower()
            if "@system32" in low or low.startswith("input ()"):
                continue
            if any(skip in low for skip in _SKIP_DEVICES):
                continue

            api = pa.get_host_api_info_by_index(info["hostApi"])["name"]
            if api not in _API_RANK:
                continue
            base = name.split("(")[0].strip() or name
            rank = _API_RANK[api]
            # MME truncates names at 31 chars, so key on the part before "("
            if base.lower() not in best or rank < best[base.lower()][0]:
                best[base.lower()] = (rank, i, base, low)
    finally:
        pa.terminate()

    devices = []
    for _rank, index, base, low in sorted(best.values(), key=lambda t: t[1]):
        loopback = any(hint in low for hint in _LOOPBACK_HINTS)
        # "Microphone Array" is what Realtek calls the built-in laptop mic,
        # which is not obvious from the name
        friendly = base
        low_base = base.lower()
        if "microphone array" in low_base:
            friendly = "Internal microphone"
        elif low_base == "microphone":
            friendly = "Internal microphone (mono)"

        devices.append({
            "index": index,
            "kind": "mic",
            "label": f"System audio ({friendly})" if loopback else friendly,
            "loopback": loopback,
        })
    return devices
