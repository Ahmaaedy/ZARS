"""Groq speech-to-text (cloud).

Sends the recorded utterance as an in-memory WAV -- no temp files on disk.

Audio only ever leaves the machine after the wake word fires and the VAD has
confirmed speech; the idle loop never calls this.
"""
import io
import wave

import numpy as np

from .base import ProviderError, STTProvider
from .groq_errors import call_with_retry, translate


def _to_wav_bytes(samples, sample_rate=16000):
    """float32 mono -> 16-bit PCM WAV in memory."""
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return None
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


class GroqSTT(STTProvider):
    name = "groq whisper (online)"

    def __init__(self, api_key, model, timeout=15.0, language="en",
                 prompt="Zars, an offline voice assistant for Windows.", client=None,
                 retry_after_max_s=2.0):
        if not model:
            raise ProviderError("GROQ_STT_MODEL is not set")
        self.model = model
        self.timeout = float(timeout)
        self.retry_after_max_s = float(retry_after_max_s)
        # Explicit language cuts latency and avoids mis-detecting short utterances.
        self.language = (language or "").lower() or None
        self.prompt = prompt
        self._client = client
        self._api_key = api_key

    def _get_client(self):
        if self._client is None:
            import groq
            self._client = groq.Groq(
                api_key=self._api_key,
                timeout=self.timeout,
                max_retries=0,  # no retry loops
            )
        return self._client

    def _create(self, kwargs):
        try:
            return self._get_client().audio.transcriptions.create(**kwargs)
        except Exception as e:
            raise translate(e, "groq stt") from e

    def transcribe(self, samples, sample_rate=16000):
        wav = _to_wav_bytes(samples, sample_rate)
        if not wav:
            return ""
        kwargs = {
            "file": ("audio.wav", wav, "audio/wav"),
            "model": self.model,
            "temperature": 0.0,
        }
        if self.language:
            kwargs["language"] = self.language
        if self.prompt:
            kwargs["prompt"] = self.prompt
        # One bounded Retry-After wait, then fall back to local if still limited.
        result = call_with_retry(lambda: self._create(kwargs), self.retry_after_max_s)
        text = (getattr(result, "text", "") or "").strip()
        return text