"""Text-to-speech via Kokoro (kokoro-onnx).

Loading is lazy so the ~310 MB ONNX graph is not resident while idle.

Playback is streamed: the final answer is split into sentences, and
create_stream() is pumped in its own asyncio event loop on a worker thread while
the main thread plays each chunk as it arrives. Sentence one therefore starts
speaking while later sentences are still being synthesized.
"""
import asyncio
import queue
import re
import threading

import numpy as np

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text):
    """Split into speakable chunks. Falls back to clauses for very long or
    unpunctuated text so the first sentence isn't held up by the whole reply."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]
    out = []
    for p in parts:
        if len(p) > 300:
            out.extend(x.strip() for x in p.split(",") if x.strip())
        else:
            out.append(p)
    return out or [text]


class TtsError(RuntimeError):
    pass


class KokoroTts:
    def __init__(self, model_path, voices_path, voice="af_sarah", speed=1.0,
                 sample_rate=24000):
        self.model_path = model_path
        self.voices_path = voices_path
        self.voice = voice
        self.speed = float(speed)
        self.sample_rate = sample_rate
        self._kokoro = None
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            if self._kokoro is not None:
                return self._kokoro
            if not self.model_path or not self.voices_path:
                raise TtsError("Kokoro paths not configured")
            try:
                from kokoro_onnx import Kokoro
                self._kokoro = Kokoro(self.model_path, self.voices_path)
            except Exception as e:
                raise TtsError(f"cannot load Kokoro: {e}") from e
            available = self._kokoro.get_voices()
            if self.voice not in available:
                raise TtsError(
                    f"voice '{self.voice}' not in voices file. "
                    f"Available: {', '.join(available[:12])}..."
                )
            return self._kokoro

    def warm(self):
        return self.load()

    def synthesize(self, text):
        """Blocking single-shot synthesis. Returns (samples, sample_rate)."""
        k = self.load()
        try:
            samples, sr = k.create(text, voice=self.voice, speed=self.speed, lang="en-us")
            return np.asarray(samples, dtype=np.float32), sr
        except Exception as e:
            raise TtsError(f"synthesis failed: {e}") from e

    def stream_to_speaker(self, text, speaker):
        """Speak `text`, streaming sentence by sentence.

        Synthesis runs in a worker thread with its own event loop; chunks are
        handed to `speaker` as they are produced. Returns True if anything was
        spoken.
        """
        sentences = split_sentences(text)
        if not sentences:
            return False

        chunks = queue.Queue(maxsize=8)
        failure = {}
        DONE = object()

        def producer():
            async def run():
                loop = asyncio.get_running_loop()
                k = self.load()
                for sentence in sentences:
                    async for samples, sr in k.create_stream(
                        sentence, voice=self.voice, speed=self.speed, lang="en-us"
                    ):
                        await loop.run_in_executor(
                            None, chunks.put, (np.asarray(samples, dtype=np.float32), sr)
                        )

            try:
                asyncio.run(run())
            except Exception as e:
                failure["err"] = e
            finally:
                chunks.put(DONE)

        t = threading.Thread(target=producer, daemon=True)
        t.start()

        spoke = False
        try:
            while True:
                item = chunks.get()
                if item is DONE:
                    break
                samples, sr = item
                speaker.play(samples, sr)
                spoke = True
        finally:
            t.join(timeout=30)

        if "err" in failure and not spoke:
            raise TtsError(f"stream synthesis failed: {failure['err']}")
        return spoke