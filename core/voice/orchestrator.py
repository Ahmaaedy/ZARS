"""Voice orchestrator: the IDLE/LISTENING/TRANSCRIBING/THINKING/SPEAKING machine.

    IDLE --wake--> LISTENING --silence--> TRANSCRIBING --text--> THINKING
      ^                                                        |
      +--------------- SPEAKING <--------------------------------+

Design notes:
  * While IDLE only the wake-word model is resident. STT and TTS load lazily
    on first use (or eagerly when voice.keep_warm is on).
  * During SPEAKING the mic is muted and a cooldown runs afterwards, so the
    assistant's own voice cannot re-trigger the wake word.
  * Conversation memory is shared with the CLI (Agent.memory / Agent.longterm).
    Voice adds only a *session* reset: after voice.session_reset_s of silence
    the next voice turn starts from a clean context. The persisted
    memory/recent.json is never wiped by this.
  * Every failure path logs and returns to IDLE; nothing here raises out.
"""
import re
import time
from pathlib import Path

import numpy as np

from ..providers.base import ProviderError
from . import devices
from .audio_io import AudioError, MicSource, Speaker
from .stt import SttEngine, SttError
from .tts import KokoroTts, TtsError
from .vad import SileroVad, VadError
from .wakeword import WakeWordDetector, WakeWordError

IDLE = "IDLE"
SLEEPING = "SLEEPING"
LISTENING = "LISTENING"
TRANSCRIBING = "TRANSCRIBING"
THINKING = "THINKING"
SPEAKING = "SPEAKING"

# Spoken "are you sure" templates. {command} is what would run; {effect} is
# the entry's description (what it would do). Kept literal per request so the
# user always hears both halves, even when {command} is an opaque script path.
CONFIRM_PROMPT = "Are you sure you want to run {command}? That will {effect}. Say yes or no."

# Whisper often returns these rather than a clean yes/no, and it can pick up
# filler ("yes please", "no wait"). Matched as whole words, most specific first.
YES_WORDS = {"yes", "yeah", "yep", "yup", "yepp", "yeahh", "ye", "ya", "yes please",
             "yes sure", "sure", "ok", "okay", "go ahead", "do it", "affirmative",
             "confirm", "confirmed", "absolutely", "definitely", "of course"}
NO_WORDS = {"no", "nope", "nah", "negative", "cancel", "don't", "dont", "do not",
            "stop", "wait", "no thanks", "no thank you", "abort", "deny", "denied"}


class VoiceLoop:
    def __init__(self, config, agent, console=None, providers=None, orb=None):
        self.config = config
        self.agent = agent
        self.console = console
        self.state = IDLE
        self._orb = orb
        self._cap_idx = None
        self._last_activity = time.monotonic()

        # Apply any host-API override before anything resolves a device.
        devices.set_host_preference(getattr(config, "voice_audio_host_api", ""))

        self.mic = MicSource(
            sample_rate=getattr(config, "voice_sample_rate", 16000),
            device=getattr(config, "voice_mic_device", None),
        )
        self.speaker = Speaker(device=getattr(config, "voice_speaker_device", None))
        # Sleep policy. The wake word is the only way in; `always_asleep` just
        # decides whether she drops back into SLEEPING after each request or
        # stays hot, which is the old behaviour.
        self.always_asleep = bool(getattr(config, "voice_always_asleep", True))
        self.sleep_after_answer_s = float(
            getattr(config, "voice_sleep_after_answer_s", 3.0))
        self._wake_at = 0.0
        self.wake = WakeWordDetector(
            model_path=getattr(config, "voice_wakeword_model", ""),
            threshold=getattr(config, "voice_wakeword_threshold", 0.5),
            # Deliberately not voice_cooldown_s: that is how long her own
            # playback is muted to avoid self-triggering, and tuning it should
            # not move the wake re-arm delay.
            cooldown_s=getattr(config, "voice_wake_cooldown_s", 2.0),
            rearm_ratio=getattr(config, "voice_wake_rearm_ratio", 0.6),
            max_lock_s=getattr(config, "voice_wake_max_lock_s", 5.0),
        )
        self.vad = SileroVad(
            model_path=getattr(config, "voice_vad_model", ""),
            threshold=getattr(config, "voice_vad_threshold", 0.5),
            silence_end_s=getattr(config, "voice_silence_timeout_s", 0.8),
        )
        # STT/LLM come from the provider factory (local vs Groq). They are
        # injected so the state machine never checks which mode is active.
        if providers is not None:
            self.stt, self.llm = providers
        else:
            from ..providers.factory import build_providers
            stt, llm, note = build_providers(config, None, agent,
                                             log=lambda m: self.log(m))
            self.stt, self.llm = stt, llm
            self.log(f"[dim]{note}[/dim]")
        self.stt_name = getattr(self.stt, "name", "stt")
        self.llm_name = getattr(self.llm, "name", "llm")
        self.tts = KokoroTts(
            model_path=getattr(config, "voice_kokoro_model", ""),
            voices_path=getattr(config, "voice_kokoro_voices", ""),
            voice=getattr(config, "voice_kokoro_voice", "af_sarah"),
            speed=getattr(config, "voice_kokoro_speed", 1.0),
        )
        if getattr(config, "voice_keep_warm", False):
            self._warm()
        self._prev_confirm_hook = None
        self._install_confirm_hook()
        # Wake chime is decoded on the first wake, not at startup, so a missing
        # file costs nothing until it would actually have been used.
        self._chime = None
        self._chime_tried = False

    def _warm(self):
        warm = getattr(self.stt, "warm", None)
        if callable(warm):
            try:
                warm()
                self.log("[dim]warmed stt[/dim]")
            except Exception as e:
                self.log(f"[yellow]stt warm-up failed: {e}[/yellow]")
        try:
            self.tts.warm()
            self.log("[dim]warmed tts[/dim]")
        except Exception as e:
            self.log(f"[yellow]tts warm-up failed: {e}[/yellow]")

    def log(self, msg):
        if self.console:
            self.console.print(msg)

    # ---------- spoken confirmation ----------

    @staticmethod
    def _speakable_plan(plan, entry):
        """Turn an internal plan string into something worth saying out loud.

        Skill plans embed an absolute script path, which is noise when spoken,
        so the skill id stands in for the path.
        """
        text = (plan or "").strip()
        if entry is not None and entry.handler.get("type") == "skill":
            return f"the {entry.id.replace('_', ' ')} action"
        if text.startswith("powershell:"):
            text = text[len("powershell:"):].strip()
        elif text.startswith("python "):
            text = "that command"
        return text.rstrip(".?") or "that command"

    def _effect_text(self, plan, entry):
        """Plain-language description of what the action would do."""
        if entry is not None and entry.desc:
            return entry.desc.rstrip(".?")
        return "run this command"

    @staticmethod
    def _parse_yes_no(text):
        """True/False from a spoken answer, None when it is not clearly either."""
        if not text:
            return None
        cleaned = re.sub(r"[^a-z'\s]", " ", text.lower())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return None
        # Longest phrases first so "yes please" beats a bare "yes" check.
        tokens = cleaned.split()
        joined = " ".join(tokens)
        for word in sorted(YES_WORDS, key=len, reverse=True):
            if word == joined or f" {word} " in f" {joined} ":
                return True
        for word in sorted(NO_WORDS, key=len, reverse=True):
            if word == joined or f" {word} " in f" {joined} ":
                return False
        # A bare leading token is a common STT shape ("yes go ahead").
        if tokens[0] in YES_WORDS:
            return True
        if tokens[0] in NO_WORDS:
            return False
        return None

    def _countdown_seconds(self, reason):
        """Grace period for `reason`, or 0 when it should use the y/N prompt."""
        from ..safety import COUNTDOWN_REASONS

        if reason not in COUNTDOWN_REASONS:
            return 0.0
        try:
            return max(0.0, float(getattr(self.config, "confirm_countdown_seconds", 0) or 0))
        except (TypeError, ValueError):
            return 0.0

    def _voice_countdown(self, plan, entry, reason, seconds):
        """Announce the countdown, listen for an objection, then act.

        Silence means go, matching the console path. The one thing that still
        denies is an unspeakable warning: if the user was never told the machine
        is about to reboot, they cannot meaningfully object to it.
        """
        warning = (f"{self._effect_text(plan, entry)} in {seconds:.0f} seconds. "
                   f"Say stop to cancel.")
        self.log(f"[dim]countdown (voice): {warning}[/dim]")
        try:
            self._speak(warning)
        except (TtsError, AudioError, RuntimeError) as e:
            self.log(f"[yellow]could not speak the countdown warning: {e} -- aborting[/yellow]")
            return False

        # A little headroom over the window so a "stop" started near the end is
        # still heard; silence is the go signal, not an error.
        samples = self._listen(max_record_s=seconds + 2, no_speech_timeout_s=seconds + 1)
        if samples is None:
            self.log("[dim]nothing said during the countdown -- running it[/dim]")
            self._speak_quietly("Running it now.")
            return True

        try:
            answer = self._transcribe(samples)
        except (SttError, ProviderError) as e:
            self.log(f"[yellow]stt failed during the countdown ({e}) -- running it[/yellow]")
            self._speak_quietly("Running it now.")
            return True

        verdict = self._parse_yes_no(answer)
        if verdict is False:
            self.log(f"[dim]countdown heard {answer!r} -- cancelled[/dim]")
            self._speak_quietly("Okay, cancelled.")
            return False

        # An unrecognised phrase is not an objection, so it proceeds. Only a
        # clear refusal cancels; noise must never veto a power action silently.
        self.log(f"[dim]countdown heard {answer!r} -- running it[/dim]")
        self._speak_quietly("Running it now.")
        return True

    def _speak_quietly(self, line):
        """Best-effort acknowledgement. The verdict is already decided."""
        try:
            self._speak(line)
        except (TtsError, AudioError, RuntimeError):
            pass

    def _voice_confirm(self, plan, entry=None, reason=None):
        """Ask for confirmation by voice. Returns True to run, False to abort.

        Fails closed: any TTS/STT/timeout problem denies the action, so a broken
        audio path can never escalate to "run it without asking". The countdown
        is the one deliberate exception, and only for actions that are
        irreversible anyway -- see _voice_countdown.
        """
        seconds = self._countdown_seconds(reason)
        if seconds > 0:
            return self._voice_countdown(plan, entry, reason, seconds)

        prompt = CONFIRM_PROMPT.format(
            command=self._speakable_plan(plan, entry),
            effect=self._effect_text(plan, entry),
        )
        self.log(f"[dim]confirm (voice): {prompt}[/dim]")
        # Speak first, then listen -- the mute gate keeps the prompt itself from
        # being captured as the answer. If the prompt cannot be spoken the user
        # was never actually asked, so deny rather than guess.
        try:
            self._speak(prompt)
        except (TtsError, AudioError, RuntimeError) as e:
            self.log(f"[yellow]could not speak confirmation prompt: {e} -- aborting[/yellow]")
            return False
        samples = self._listen()
        if samples is None:
            self.log("[yellow]no answer heard -- aborting[/yellow]")
            return False
        try:
            answer = self._transcribe(samples)
        except (SttError, ProviderError) as e:
            self.log(f"[yellow]stt failed during confirm: {e}[/yellow]")
            return False
        verdict = self._parse_yes_no(answer)
        if verdict is None:
            self.log(f"[yellow]could not read that as yes or no ({answer!r}) -- aborting[/yellow]")
            return False
        try:
            self._speak("Okay, running it." if verdict else "Okay, cancelled.")
        except (TtsError, AudioError, RuntimeError):
            pass  # verdict already decided; a failed ack is cosmetic
        return verdict

    def _install_confirm_hook(self):
        """Route Agent confirmations through the voice loop for this run."""
        if self.agent is None:
            return
        self._prev_confirm_hook = getattr(self.agent, "confirm_hook", None)
        self.agent.confirm_hook = self._voice_confirm

    def _restore_confirm_hook(self):
        if self.agent is None:
            return
        if hasattr(self, "_prev_confirm_hook"):
            self.agent.confirm_hook = self._prev_confirm_hook

    # ---------- memory session reset ----------

    def _reset_session_if_idle(self):
        """Clear the in-memory voice session context after a silence period.

        Touches only agent.memory's in-process buffer. memory/recent.json is
        left alone so the CLI keeps its history, and long-term conversation
        notes are still written.
        """
        timeout = float(getattr(self.config, "voice_session_reset_s", 300))
        if timeout <= 0:
            return
        if time.monotonic() - self._last_activity < timeout:
            return
        mem = getattr(self.agent, "memory", None)
        if mem and mem.count():
            self.log("[dim]voice session idle -- resetting conversation context[/dim]")
            mem._messages = []

    def _touch(self):
        self._last_activity = time.monotonic()

    # ---------- state machine ----------

    def _set_state(self, name):
        """Single point where the state changes.

        Everything else -- including the spoken-confirmation sub-flow, which
        re-enters _listen/_speak -- goes through here, so the orb overlay
        tracks the machine without any call site being able to forget.
        """
        self.state = name
        if self._orb is not None:
            try:
                self._orb.state(name)
            except Exception:
                # The overlay is decoration. Never let it break voice mode.
                self._orb = None

    def _to_idle(self):
        self._set_state(IDLE)
        self.mic.gate.clear()

    # ---------- sleep / wake policy ----------

    def _enter_sleep(self):
        """Dormant between requests; only a wake word brings her back.

        Called after a completed cycle. The gate lives here and in run()'s wake
        check, never in _listen(): the spoken-confirmation sub-flow re-enters
        _listen() with no wake word because she asked the question, and gating
        there would deadlock every confirmation.
        """
        self._cap_clear()
        if not self.always_asleep or self.sleep_after_answer_s <= 0:
            return
        self._wake_at = time.monotonic() + self.sleep_after_answer_s
        self.wake.suspend(self.sleep_after_answer_s)
        self._set_state(SLEEPING)
        self.log(f"[dim]asleep {self.sleep_after_answer_s:g}s"
                 f" -- say the wake word when you're ready[/dim]")

    def _wake_from_sleep(self, now):
        """Return to IDLE once the refractory window has elapsed."""
        if self.state == SLEEPING and now >= self._wake_at:
            self._set_state(IDLE)
            self.log("[dim]awake[/dim]")

    def _sleep_policy(self):
        return ("one request per wake" if self.always_asleep
                else "stays awake between requests")

    # ---------- wake chime ----------

    def _load_wake_chime(self):
        """Decode voice_wake_chime to (mono float32 samples, rate), or None.

        Any problem here is reported once and then treated as "no chime",
        because the chime is acknowledgement, not a dependency.
        """
        raw = getattr(self.config, "voice_wake_chime", "") or ""
        if not str(raw).strip():
            return None
        path = Path(str(raw).strip())
        if not path.exists():
            self.log(f"[dim]no wake chime at {path.name} -- continuing without one[/dim]")
            return None

        try:
            import soundfile as sf
            samples, rate = sf.read(str(path), dtype="float32", always_2d=False)
        except Exception as e:
            self.log(f"[yellow]wake chime unreadable ({path.name}: {e}) "
                     f"-- continuing without it[/yellow]")
            return None

        data = np.asarray(samples, dtype=np.float32)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if data.size == 0:
            self.log(f"[yellow]wake chime is empty ({path.name})[/yellow]")
            return None

        try:
            gain = float(getattr(self.config, "voice_wake_chime_gain", 0.6))
        except (TypeError, ValueError):
            gain = 0.6
        # Normalise to the configured peak so any drop-in file lands at the same
        # level rather than at whatever its master happened to be. A silent file
        # is treated as absent: playing it would only stall the listen window.
        peak = float(np.max(np.abs(data))) if data.size else 0.0
        if peak < 1e-4:
            self.log(f"[yellow]wake chime is silent ({path.name}) -- skipping it[/yellow]")
            return None
        data = np.clip(data / peak * min(1.0, max(0.0, gain)), -1.0, 1.0)
        return data, int(rate)

    def _play_wake_chime(self):
        """Acknowledge the wake word before the listen window opens.

        Speaker.play blocks until the sound is finished, so the chime is over
        before record_until_speech() opens -- the trailing sleep is only room
        decay, so the VAD cannot mistake the tail for the user's request.
        """
        if not self._chime_tried:
            self._chime_tried = True
            self._chime = self._load_wake_chime()
        if not self._chime:
            return
        samples, rate = self._chime
        # Same reasoning as _speak: keep the wake loop from re-triggering on it.
        self.mic.gate.mute(samples.size / float(rate) + 0.3)
        try:
            self.speaker.play(samples, rate)
        except AudioError as e:
            self.log(f"[yellow]wake chime playback failed: {e}[/yellow]")
            return
        time.sleep(0.15)

    def _listen(self, max_record_s=None, no_speech_timeout_s=None):
        """Record until speech, then until the utterance ends.

        The two bounds default to config but can be overridden per call so a
        countdown can borrow exactly as much microphone time as it has left.
        """
        self._set_state(LISTENING)
        # The previous answer's caption is still readable at this point, but it
        # would sit over the screen while the user talks. Drop it on the way in
        # rather than on the way out, so it stays up for as long as possible.
        self._cap_clear()
        self.log("[bold cyan]listening...[/bold cyan]")
        if max_record_s is None:
            max_record_s = float(getattr(self.config, "voice_max_record_s", 30))
        if no_speech_timeout_s is None:
            no_speech_timeout_s = float(getattr(self.config, "voice_no_speech_timeout_s", 6))
        try:
            return self.mic.record_until_speech(
                self.vad,
                max_record_s=max_record_s,
                no_speech_timeout_s=no_speech_timeout_s,
            )
        except AudioError as e:
            self.log(f"[red]mic error: {e}[/red]")
            return None

    def _transcribe(self, samples):
        self._set_state(TRANSCRIBING)
        self.log(f"[dim]transcribing via {self.stt_name}...[/dim]")
        # Provider returns a plain string; tolerate the legacy (text, lang) tuple
        # so an older local engine still works.
        result = self.stt.transcribe(samples)
        if isinstance(result, tuple):
            result = result[0]
        return result or ""

    def _think(self, text):
        self._set_state(THINKING)
        self.log(f"[dim]you: {text}[/dim]")
        return self.agent.handle_voice(text, llm=self.llm)

    def _speak(self, answer):
        self._set_state(SPEAKING)
        cooldown = float(getattr(self.config, "voice_cooldown_s", 2.0))
        self.mic.gate.mute(cooldown)
        t0 = time.monotonic()
        try:
            spoke = self.tts.stream_to_speaker(
                answer, self.speaker,
                on_sentence_start=self._cap_say,
                on_sentence_end=self._cap_pace,
            )
        except (TtsError, AudioError) as e:
            self.log(f"[yellow]tts failed: {e}[/yellow]")
            self._cap_clear()
            return
        self._cap_clear()
        if spoke:
            self.log(f"[dim]spoke {time.monotonic() - t0:.1f}s "
                     f"({self.tts.voice}) via device {self.speaker.device}[/dim]")
        else:
            self.log("[dim]nothing to speak[/dim]")

    # ---------- orb speech caption ----------
    #
    # These run on the thread blocked in speaker.play(), inside
    # stream_to_speaker, so they see the audio boundaries directly. They are
    # deliberately not routed through _set_state: that tracks the state machine,
    # this tracks the words.

    def _cap_say(self, text):
        if self._orb is None:
            return
        try:
            self._cap_idx = self._orb.say(text)
        except Exception:
            self._orb = None

    def _cap_pace(self, ms):
        if self._orb is None:
            return
        try:
            self._orb.pace(self._cap_idx, ms)
        except Exception:
            self._orb = None

    def _cap_clear(self):
        if self._orb is None:
            return
        try:
            self._orb.clear()
        except Exception:
            self._orb = None

    def handle_once(self):
        """Advance the machine one full cycle: listen -> transcribe -> think
        -> speak -> idle. Returns the transcript (or '' if nothing happened)."""
        self._reset_session_if_idle()
        samples = self._listen()
        if samples is None:
            self._to_idle()
            return ""
        try:
            text = self._transcribe(samples)
        except (SttError, ProviderError) as e:
            self.log(f"[yellow]stt failed: {e}[/yellow]")
            self._to_idle()
            return ""
        if not text:
            self.log("[dim]empty transcript[/dim]")
            self._to_idle()
            return ""
        self._touch()
        answer = self._think(text)
        if answer:
            # Agent already printed the answer; don't repeat it.
            self._speak(answer)
        self._touch()
        self._to_idle()
        return text

    def run(self, max_cycles=None):
        """Idle loop: score frames for the wake word until a cycle completes.

        While asleep the frames are still read -- closing the mic would mean
        hearing no wake word at all -- but they are never turned into a request.
        Nothing buffers, records, or runs VAD/STT/LLM in that state.
        """
        self.log(
            f"[bold cyan]voice mode[/bold cyan] -- say [bold]{self.wake.name}[/bold] "
            f"(threshold {self.wake.threshold:.2f}, {self._sleep_policy()}). "
            f"Ctrl+C to quit."
        )
        cycles = 0
        try:
            for frame in self.mic.stream():
                now = time.monotonic()
                self._wake_from_sleep(now)
                if self.wake.fired(frame, now):
                    self._play_wake_chime()
                    self.handle_once()
                    cycles += 1
                    self._enter_sleep()
                    if max_cycles and cycles >= max_cycles:
                        return
        except KeyboardInterrupt:
            self.log("\n[dim]voice mode stopped[/dim]")
        finally:
            self._restore_confirm_hook()
            self.mic.close()
            Speaker.stop()

    def close(self):
        self._restore_confirm_hook()
        self.mic.close()
        Speaker.stop()