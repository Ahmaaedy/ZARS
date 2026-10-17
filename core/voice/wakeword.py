"""OpenWakeWord detector.

The ONNX models ship inside the openwakeword wheel
(site-packages/openwakeword/resources/models/), so this stage needs no download
and no network access.

Frames are 80 ms of 16 kHz mono int16 (1280 samples) -- openWakeWord's native
input format.

Three guards keep one spoken "hey zars" from becoming several wake events:

- Hysteresis. A bare `score >= threshold` test fires again on every 80 ms frame
  of a sustained phrase once the cooldown lapses. After firing, the detector
  stays locked until the score falls back under `threshold * rearm_ratio`, so a
  wake needs the word to end and a new one to begin.
- Suspension. The orchestrator sleeps the detector between requests so she only
  answers when called.
- A bounded lock. Hysteresis alone is not enough: a rearm floor below what
  ordinary in-speech audio scores would never be reached, and since the cooldown
  can swallow the entire pause after a wake word, the detector would stay locked
  for the rest of the session. The lock therefore expires after max_lock_s even
  if no quiet frame arrives.

No guard may move *which* frames reach the model. openWakeWord's Model is
stateful across predict() calls, so the frames it sees -- including the gaps --
are part of the result. All three are gates on the decision, applied after
scoring, or by skipping the score only in the window the old code already
skipped.
"""
import time

import numpy as np


class WakeWordError(RuntimeError):
    pass


class WakeWordDetector:
    """Wraps openWakeWord.Model for one or more wake words."""

    def __init__(self, model_path, threshold=0.5, cooldown_s=2.0,
                 rearm_ratio=0.6, max_lock_s=5.0, inference_framework="onnx"):
        from openwakeword.model import Model

        if not model_path:
            raise WakeWordError("wakeword model path not configured")
        self.model_path = model_path
        self.threshold = float(threshold)
        self.cooldown_s = float(cooldown_s)
        # Clamped: a ratio >= 1 would mean the detector never re-arms, and a
        # small one puts the floor below the scores ordinary in-speech audio
        # produces, so it would also never re-arm. The lock is additionally
        # time-bounded (max_lock_s), which is what actually guarantees she comes
        # back; the floor is the fast path, not the only way out.
        self.rearm_ratio = min(0.95, max(0.2, float(rearm_ratio)))
        self.rearm_floor = self.threshold * self.rearm_ratio
        self.max_lock_s = max(0.0, float(max_lock_s))
        self._last_fire = 0.0
        self._locked = False
        self._locked_at = 0.0
        self._suspended_until = 0.0

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

    @property
    def suspended(self):
        """True while the detector is asleep between requests."""
        return time.monotonic() < self._suspended_until

    def allow_external_wake(self, now):
        """Gate for a wake that did not come from audio, i.e. the hotkey.

        Same rules as a spoken wake word: nothing starts a request while the
        detector is suspended or inside the cooldown. Recording the fire means a
        wake word spoken immediately afterwards cannot open a second listen
        window.
        """
        if now < self._suspended_until:
            return False
        if now - self._last_fire < self.cooldown_s:
            return False
        self._last_fire = now
        self._locked = True
        self._locked_at = now
        return True

    def suspend(self, seconds):
        """Sleep the detector for a window, ignoring everything until it ends.

        The lock is aged out past max_lock_s as part of this, so the first scored
        frame after the window can wake her on its own. That is deliberate: by
        then she has just finished an answer, so the room is quiet, and requiring
        a dip that may never arrive would be the one way to leave her stuck.
        """
        if seconds and seconds > 0:
            self._suspended_until = max(self._suspended_until,
                                        time.monotonic() + float(seconds))
            self._locked = True
            # Age the lock out past the timeout: after a sleep longer than
            # max_lock_s the first scored frame must be able to re-arm on its
            # own, rather than needing a dip that may never come.
            self._locked_at = time.monotonic() - self.max_lock_s

    def resume(self):
        """Wake the detector immediately. Does not clear the lock."""
        self._suspended_until = 0.0

    def fired(self, frame_int16, now):
        """True once per utterance when the score crosses the threshold.

        The cooldown check comes FIRST and returns without scoring, exactly as
        it always has. That ordering is load-bearing: openWakeWord's Model is
        stateful across predict() calls, and skipping frames leaves a hole in
        the audio it has seen. Measured on hey_rhasspy, dropping 24 consecutive
        frames zeroed the next 4 scores entirely. So the set of frames handed to
        the model must not change -- only the decision about them. Suspension and
        the hysteresis lock are gates on the decision, never on the scoring.
        """
        if now - self._last_fire < self.cooldown_s:
            return False

        score = self.predict(frame_int16)

        if self._locked:
            # The utterance ended (score fell back), or the lock has simply been
            # held too long. The timeout is the safety valve: without it a
            # rearm floor below what in-speech audio scores would leave her
            # permanently unable to wake, since the cooldown can swallow the
            # whole pause after a wake word.
            if score < self.rearm_floor or now - self._locked_at >= self.max_lock_s:
                self._locked = False

        if self._locked or now < self._suspended_until:
            return False
        if score >= self.threshold:
            self._last_fire = now
            self._locked = True
            self._locked_at = now
            return True
        return False