"""OpenWakeWord detector.

The ONNX models ship inside the openwakeword wheel
(site-packages/openwakeword/resources/models/), so this stage needs no download
and no network access.

Frames are 80 ms of 16 kHz mono int16 (1280 samples) -- openWakeWord's native
input format.
"""
import numpy as np


class WakeWordError(RuntimeError):
    pass


class WakeWordDetector:
    """Wraps openWakeWord.Model for one or more wake words."""

    def __init__(self, model_path, threshold=0.5, cooldown_s=2.0, inference_framework="onnx"):
        from openwakeword.model import Model

        if not model_path:
            raise WakeWordError("wakeword model path not configured")
        self.model_path = model_path
        self.threshold = float(threshold)
        self.cooldown_s = float(cooldown_s)
        self._last_fire = 0.0

        try:
            self._model = Model(
                wakeword_models=[model_path],
                inference_framework=inference_framework,
            )
        except Exception as e:
            raise WakeWordError(f"cannot load wake word {model_path}: {e}") from e

        # openWakeWord keys predictions by model stem; expose a stable name.
        self.prediction_keys = list(self._model.models.keys())
        if not self.prediction_keys:
            raise WakeWordError(f"wake word model exposed no classes: {model_path}")
        self.name = self.prediction_keys[0]

    def predict(self, frame_int16):
        """Score one 80 ms frame. Returns the max score across models."""
        arr = np.asarray(frame_int16, dtype=np.int16).reshape(-1)
        try:
            probs = self._model.predict(arr)
        except Exception:
            return 0.0
        if not probs:
            return 0.0
        return float(max(probs.values()))

    def fired(self, frame_int16, now):
        """True when this frame crosses the threshold and the cooldown is over."""
        import time as _time

        if now - self._last_fire < self.cooldown_s:
            return False
        if self.predict(frame_int16) >= self.threshold:
            self._last_fire = now
            return True
        return False