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

from ..providers.base import ProviderError
from .audio_io import AudioError, MicSource, Speaker
from .stt import SttEngine, SttError
from .tts import KokoroTts, TtsError
from .vad import SileroVad, VadError
from .wakeword import WakeWordDetector, WakeWordError

IDLE = "IDLE"
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

        self.mic = MicSource(
            sample_rate=getattr(config, "voice_sample_rate", 16000),
            device=getattr(config, "voice_mic_device", None),
        )
        self.speaker = Speaker(device=getattr(config, "voice_speaker_device", None))
        self.wake = WakeWordDetector(
            model_path=getattr(config, "voice_wakeword_model", ""),
            threshold=getattr(config, "voice_wakeword_threshold", 0.5),
            cooldown_s=getattr(config, "voice_cooldown_s", 2.0),
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

    def _voice_confirm(self, plan, entry=None):
        """Ask for confirmation by voice. Returns True to run, False to abort.

        Fails closed: any TTS/STT/timeout problem denies the action, so a broken
        audio path can never escalate to "run it without asking".
        """
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

    def _listen(self):
        self._set_state(LISTENING)
        # The previous answer's caption is still readable at this point, but it
        # would sit over the screen while the user talks. Drop it on the way in
        # rather than on the way out, so it stays up for as long as possible.
        self._cap_clear()
        self.log("[bold cyan]listening...[/bold cyan]")
        try:
            return self.mic.record_until_speech(
                self.vad,
                max_record_s=float(getattr(self.config, "voice_max_record_s", 30)),
                no_speech_timeout_s=float(getattr(self.config, "voice_no_speech_timeout_s", 6)),
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
        """Idle loop: score frames for the wake word until a cycle completes."""
        self.log(
            f"[bold cyan]voice mode[/bold cyan] -- say [bold]{self.wake.name}[/bold] "
            f"(threshold {self.wake.threshold:.2f}). Ctrl+C to quit."
        )
        cycles = 0
        try:
            for frame in self.mic.stream():
                if self.wake.fired(frame, time.monotonic()):
                    self.handle_once()
                    cycles += 1
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