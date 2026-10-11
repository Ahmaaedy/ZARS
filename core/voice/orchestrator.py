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


class VoiceLoop:
    def __init__(self, config, agent, console=None, providers=None):
        self.config = config
        self.agent = agent
        self.console = console
        self.state = IDLE
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

    def _to_idle(self):
        self.state = IDLE
        self.mic.gate.clear()

    def _listen(self):
        self.state = LISTENING
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
        self.state = TRANSCRIBING
        self.log(f"[dim]transcribing via {self.stt_name}...[/dim]")
        # Provider returns a plain string; tolerate the legacy (text, lang) tuple
        # so an older local engine still works.
        result = self.stt.transcribe(samples)
        if isinstance(result, tuple):
            result = result[0]
        return result or ""

    def _think(self, text):
        self.state = THINKING
        self.log(f"[dim]you: {text}[/dim]")
        return self.agent.handle_voice(text, llm=self.llm)

    def _speak(self, answer):
        self.state = SPEAKING
        cooldown = float(getattr(self.config, "voice_cooldown_s", 2.0))
        self.mic.gate.mute(cooldown)
        t0 = time.monotonic()
        try:
            spoke = self.tts.stream_to_speaker(answer, self.speaker)
        except (TtsError, AudioError) as e:
            self.log(f"[yellow]tts failed: {e}[/yellow]")
            return
        if spoke:
            self.log(f"[dim]spoke {time.monotonic() - t0:.1f}s "
                     f"({self.tts.voice}) via device {self.speaker.device}[/dim]")
        else:
            self.log("[dim]nothing to speak[/dim]")

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
            self.mic.close()
            Speaker.stop()

    def close(self):
        self.mic.close()
        Speaker.stop()