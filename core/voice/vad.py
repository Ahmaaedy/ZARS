"""Silero VAD used for end-of-speech endpointing.

The ONNX model ships inside the silero_vad wheel
(site-packages/silero_vad/silero_vad.onnx), so no download is required.

Two roles:
  is_speech()           cheap per-window vote, drives record_until_speech()
  finalize()            speech/silence segmentation over a whole buffer, used
                        by the standalone STT test path

silero_vad expects a torch tensor of float32 @ 16 kHz and operates on 512-sample
windows, so chunks are accumulated and only whole windows are submitted.
"""
import numpy as np
import torch

from .audio_io import _resample

SILERO_WINDOW = 512


class VadError(RuntimeError):
    pass


class SileroVad:
    def __init__(self, model_path=None, threshold=0.5, silence_end_s=0.8,
                 sample_rate=16000):
        from silero_vad import load_silero_vad

        self.threshold = float(threshold)
        self.silence_end_s = float(silence_end_s)
        self.sample_rate = sample_rate

        try:
            if model_path:
                # Local file only; silero_vad accepts a path via onnx=True + vad_model
                self._model = load_silero_vad(onnx=True)
            else:
                self._model = load_silero_vad(onnx=True)
        except Exception as e:
            raise VadError(f"cannot load Silero VAD: {e}") from e

        self._buf = np.empty(0, dtype=np.float32)

    def _flush(self):
        """Run every complete 512-sample window currently buffered."""
        n = (self._buf.size // SILERO_WINDOW) * SILERO_WINDOW
        if n == 0:
            return None
        block = self._buf[:n].copy()
        self._buf = self._buf[n:]
        chunk = torch.from_numpy(block)
        try:
            return float(self._model(chunk, 16000).item())
        except Exception:
            return None

    def is_speech(self, samples):
        """True if any full window in this chunk exceeds the threshold."""
        self._buf = np.concatenate([self._buf, np.asarray(samples, dtype=np.float32).reshape(-1)])
        prob = self._flush()
        return prob is not None and prob >= self.threshold

    def finalize(self, samples, min_speech_ms=250, min_silence_ms=200,
                 max_speech_s=60.0, return_seconds=False):
        """Segment a full utterance into speech spans via get_speech_timestamps."""
        from silero_vad import get_speech_timestamps

        self._buf = np.empty(0, dtype=np.float32)
        audio = _resample(np.asarray(samples, dtype=np.float32), self.sample_rate, 16000).copy()
        try:
            return get_speech_timestamps(
                torch.from_numpy(audio),
                self._model,
                threshold=self.threshold,
                sampling_rate=16000,
                min_speech_duration_ms=min_speech_ms,
                max_speech_duration_s=max_speech_s,
                min_silence_duration_ms=min_silence_ms,
                return_seconds=return_seconds,
            )
        except Exception as e:
            raise VadError(f"VAD segmentation failed: {e}") from e