"""Microphone capture and playback.

The wake detector wants 16 kHz mono int16 (the native openWakeWord format), so
MicSource resamples to that regardless of what the hardware offers. Speaker
takes whatever rate TTS produces (Kokoro emits 24 kHz).

MuteGate exists so TTS playback can never re-trigger the wake word: while the
speaker is active the orchestrator drops mic frames instead of feeding them to
the detector.
"""
import time

import numpy as np
import sounddevice as sd


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

    @property
    def gate(self):
        return self._gate

    def _open(self):
        if self._stream is None:
            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    device=self.device,
                    blocksize=self.block_frames,
                )
                self._stream.start()
            except Exception as e:
                raise AudioError(f"cannot open input device {self.device!r}: {e}") from e
        return self._stream

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

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
    """Blocking playback of float32 audio."""

    def __init__(self, device=None):
        self.device = device

    def play(self, samples, sample_rate):
        if samples is None or getattr(samples, "size", 0) == 0:
            return
        try:
            sd.play(np.asarray(samples, dtype=np.float32), sample_rate)
            sd.wait()
        except Exception as e:
            raise AudioError(f"playback failed: {e}") from e

    @staticmethod
    def stop():
        try:
            sd.stop()
        except Exception:
            pass