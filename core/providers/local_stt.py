"""Local STT: wraps the existing faster-whisper SttEngine.

The heavy model load stays lazy inside SttEngine, so in online mode this
provider costs nothing until a fallback actually happens.
"""
from .base import ProviderError, STTProvider


class LocalWhisperSTT(STTProvider):
    name = "faster-whisper (local)"

    def __init__(self, engine):
        self._engine = engine

    @property
    def engine(self):
        return self._engine

    def warm(self):
        self._engine.warm()

    def transcribe(self, samples, sample_rate=16000):
        try:
            text, _lang = self._engine.transcribe(samples, sample_rate)
        except Exception as e:
            raise ProviderError(f"local whisper failed: {e}") from e
        return text or ""


def build_local_stt(config):
    """Construct the local STT from config. Imported lazily by the factory."""
    from ..voice.stt import SttEngine

    engine = SttEngine(
        model_path=getattr(config, "voice_whisper_model", ""),
        device=getattr(config, "voice_whisper_device", "cpu"),
        compute_type=getattr(config, "voice_whisper_compute_type", "int8"),
    )
    return LocalWhisperSTT(engine)