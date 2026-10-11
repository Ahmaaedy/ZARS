"""Speech-to-text via faster-whisper.

Model is loaded from a local directory only. Set HF_HUB_OFFLINE so faster-whisper
cannot silently reach out if a path is wrong -- a missing model should raise,
not download.
"""
import os


class SttError(RuntimeError):
    pass


class SttEngine:
    """Lazy-loaded: constructing this object is cheap, the model loads on
    first transcribe() so idle memory stays low unless keep_warm is set."""

    def __init__(self, model_path, device="cpu", compute_type="int8", language=None,
                 beam_size=5):
        self.model_path = model_path
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self._model = None

    def load(self):
        if self._model is not None:
            return self._model
        if not self.model_path:
            raise SttError("voice.whisper_model is not configured")
        if not os.path.isdir(self.model_path):
            raise SttError(f"whisper model directory not found: {self.model_path}")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_path,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as e:
            raise SttError(f"cannot load whisper model {self.model_path}: {e}") from e
        return self._model

    def warm(self):
        """Eagerly load (used when voice.keep_warm is on)."""
        return self.load()

    def transcribe(self, samples, sample_rate=16000):
        """Returns (text, language). Empty string on silence."""
        import numpy as np

        if samples is None or np.asarray(samples).size == 0:
            return "", None
        model = self.load()
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        try:
            segments, info = model.transcribe(
                audio,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=False,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            return text, getattr(info, "language", None)
        except SttError:
            raise
        except Exception as e:
            raise SttError(f"transcription failed: {e}") from e