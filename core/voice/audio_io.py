"""Microphone capture and playback.

The wake detector wants 16 kHz mono int16 (the native openWakeWord format), so
MicSource resamples to that regardless of what the hardware offers. Speaker
takes whatever rate TTS produces (Kokoro emits 24 kHz).

Which physical device that comes from is decided by devices.py, not by
PortAudio's default index -- see the note there about MME and about defaults
that move while you are not looking.

MuteGate exists so TTS playback can never re-trigger the wake word: while the
speaker is active the orchestrator drops mic frames instead of feeding them to
the detector.
"""
import threading
import time
import weakref

import numpy as np
import sounddevice as sd

from . import devices


class AudioError(RuntimeError):
    """Raised when a device cannot be opened."""


def _resample(audio, src_rate, dst_rate):
    """Linear resample to 1-D float32.

    sounddevice returns (n, 1) for a mono stream, so everything is flattened
    here -- callers (VAD, whisper) all expect plain 1-D mono.
    """
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if src_rate == dst_rate or audio.size == 0:
        return audio
    duration = audio.shape[0] / float(src_rate)
    dst_len = max(1, int(round(duration * dst_rate)))
    x_old = np.linspace(0.0, duration, num=audio.shape[0], endpoint=False)
    x_new = np.linspace(0.0, duration, num=dst_len, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


class MuteGate:
    """True while the speaker should be treated as 'noise'."""

    def __init__(self):
        self._until = 0.0

    def mute(self, seconds):
        self._until = max(self._until, time.monotonic() + seconds)

    @property
    def muted(self):
        return time.monotonic() < self._until

    def clear(self):
        self._until = 0.0


class MicSource:
    """Blocking 16 kHz mono int16 capture.

    Used two ways: stream() for the continuous idle loop (wake word), and
    record_until_speech() for the listen window (which owns its VAD).
    """

    def __init__(self, sample_rate=16000, device=None, block_ms=80):
        self.sample_rate = sample_rate
        self.device = device
        self.block_ms = block_ms
        self.block_frames = int(sample_rate * block_ms / 1000)
        self._gate = MuteGate()
        self._stream = None
        self._resolved = None
        self._resolved_name = None
        self._resolved_host = None
        # Set once the stream is open, so startup can log what it picked.
        self.resolved_label = None

    @property
    def gate(self):
        return self._gate

    def _open(self):
        if self._stream is not None:
            return self._stream

        options = devices.candidates("input", self.sample_rate, 1, "int16", self.device)
        # Try the cached device first. Devices come and go on this machine, so
        # a cache is a hint, not a guarantee -- one failed open is cheap, and
        # it costs far less than re-resolving from scratch every wake.
        if self._resolved is not None:
            options = ([(self._resolved, self._resolved_name, self._resolved_host)]
                       + [o for o in options if o[0] != self._resolved])

        if not options:
            raise AudioError(devices.no_device_error(
                "input", self.sample_rate, self.device))

        errors = []
        for index, name, host in options:
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    device=index,
                    blocksize=self.block_frames,
                )
                stream.start()
            except Exception as e:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                errors.append("%d %s: %s" % (index, name, e))
                continue
            # A stream can open cleanly and still be useless: the DirectSound
            # capture backend here reports 16 kHz as supported and then
            # free-runs at thousands of times real time, handing back silence.
            # Measure before committing to it.
            if not devices.validate_input_alive(stream, self.sample_rate, self.block_frames):
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                errors.append("%d %s: opened but does not deliver audio in real time"
                              % (index, name))
                continue
            self._stream = stream
            self._resolved = index
            self._resolved_name = name
            self._resolved_host = host
            self.resolved_label = "%d: %s [%s]" % (index, name, host)
            return stream

        # Everything on the list failed. Forget the cache so the next attempt
        # re-resolves, in case the device list itself changed.
        self._resolved = None
        self.resolved_label = None
        raise AudioError("cannot open any input device. tried:\n  " + "\n  ".join(errors))

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self._resolved = None

    def stream(self):
        """Yield 80 ms int16 frames forever. Frames arriving while muted are
        yielded as silence so the caller's timing stays uniform."""
        s = self._open()
        while True:
            frame, overflowed = s.read(self.block_frames)
            if overflowed:
                continue
            if self._gate.muted:
                yield np.zeros(self.block_frames, dtype=np.int16)
            else:
                yield frame

    def record_until_speech(self, vad, max_record_s, no_speech_timeout_s):
        """Record one utterance, endpointed by the VAD.

        Returns float32 samples at 16 kHz, or None if the user never spoke
        (no_speech_timeout_s) or nothing was captured.
        """
        s = self._open()
        window = int(self.sample_rate / 100)
        chunks = []
        got_speech = False
        started = None
        silence_since = None
        max_samples = int(max_record_s * self.sample_rate)
        deadline = no_speech_timeout_s

        while True:
            frame, _ = s.read(window)
            now = time.monotonic()
            samples = _resample(frame.astype(np.float32) / 32768.0, self.sample_rate, 16000)
            speech = vad.is_speech(samples)

            if speech:
                if not got_speech:
                    got_speech = True
                    started = now
                silence_since = None
            elif got_speech:
                if silence_since is None:
                    silence_since = now
                elif now - silence_since >= vad.silence_end_s:
                    return np.concatenate(chunks) if chunks else None
            chunks.append(samples)
            total = sum(c.size for c in chunks)
            if total >= max_samples:
                return np.concatenate(chunks)
            if not got_speech and now - (started or now) > deadline:
                return None
            if got_speech and now - started > max_record_s:
                return np.concatenate(chunks)


class Speaker:
    """Blocking playback of float32 audio.

    Holds one OutputStream open and reuses it. sd.play() would build a fresh
    stream per call and never close it, so every wake chime and every spoken
    reply leaked one -- and it ignored the configured device entirely.
    """

    _live = weakref.WeakSet()

    def __init__(self, device=None):
        self.device = device
        self._stream = None
        self._rate = None
        self._resolved = None
        self._resolved_name = None
        self._resolved_host = None
        self.resolved_label = None
        Speaker._live.add(self)
        self._buffer = None
        self._offset = 0
        self._finished = threading.Event()

    def _ensure_stream(self, sample_rate):
        if self._stream is not None and self._rate == sample_rate:
            return self._stream
        # Different rate (Kokoro at 24 kHz, a chime file at its own rate), so
        # the open stream is no longer usable.
        self.close()

        options = devices.candidates("output", sample_rate, 1, "float32", self.device)
        if self._resolved is not None:
            cached = self._resolved
            if not options or options[0][0] != cached:
                options.insert(0, (cached, self._resolved_name, self._resolved_host))

        if not options:
            raise AudioError(devices.no_device_error(
                "output", sample_rate, self.device))

        errors = []
        for index, name, host in options:
            stream = None
            try:
                stream = sd.OutputStream(
                    device=index,
                    channels=1,
                    dtype="float32",
                    samplerate=sample_rate,
                    callback=self._callback,
                )
                stream.start()
            except Exception as e:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                errors.append("%d %s: %s" % (index, name, e))
                continue
            # Opening is not the same as working. Push a short silent buffer
            # through and confirm the callback sees it finished; a device that
            # accepts audio and never plays it looks identical otherwise.
            if not self._drain(np.zeros(int(sample_rate * 0.02), dtype=np.float32),
                               sample_rate, budget_s=1.0):
                try:
                    stream.abort()
                    stream.close()
                except Exception:
                    pass
                errors.append("%d %s: opened but never played" % (index, name))
                continue
            self._stream = stream
            self._rate = sample_rate
            self._resolved = index
            self._resolved_name = name
            self._resolved_host = host
            self.resolved_label = "%d: %s [%s]" % (index, name, host)
            return stream

        self._resolved = None
        self.resolved_label = None
        raise AudioError("cannot open any output device. tried:\n  " + "\n  ".join(errors))

    # Playback goes through the stream callback rather than write(), because
    # write() is asynchronous and `stream.active` only means "started" -- both
    # were measured here and neither can tell you when the audio finished.
    # This is the same mechanism sounddevice.play()/wait() uses internally.

    def _callback(self, outdata, frames, time_info, status):
        data = self._buffer
        if data is None:
            outdata[:] = 0
            return
        start = self._offset
        end = min(start + frames, data.size)
        if end > start:
            outdata[: end - start] = data[start:end].reshape(-1, outdata.shape[1])
        self._offset = end
        if end >= data.size:
            outdata[end - start:] = 0
            self._buffer = None
            self._offset = 0
            self._finished.set()

    def _drain(self, data, sample_rate, budget_s=None):
        """Hand `data` to the callback and block until it has been played out."""
        expected = data.size / float(sample_rate)
        limit = budget_s if budget_s is not None else expected + 2.0
        self._buffer = np.asarray(data, dtype=np.float32)
        self._offset = 0
        self._finished.clear()
        return self._finished.wait(limit)

    def play(self, samples, sample_rate):
        if samples is None or getattr(samples, "size", 0) == 0:
            return
        try:
            stream = self._ensure_stream(int(sample_rate))
        except AudioError:
            raise
        except Exception as e:
            raise AudioError(f"playback failed: {e}") from e
        if not self._drain(np.asarray(samples, dtype=np.float32), int(sample_rate)):
            raise AudioError("playback timed out; the device is not draining")

    def close(self):
        self._buffer = None
        self._offset = 0
        if self._stream is not None:
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self._rate = None
            self._resolved = None

    @staticmethod
    def stop():
        """Stop and release every open speaker. Static so the orchestrator's
        shutdown path can call it without holding a reference."""
        try:
            sd.stop()
        except Exception:
            pass
        for speaker in list(Speaker._live):
            speaker.close()