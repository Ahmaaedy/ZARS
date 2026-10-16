import copy
import json
import os
from pathlib import Path

DEFAULTS = {
"provider": "ollama",
"model": "qwen2.5:3b-instruct",
"host": "http://localhost:11434",
# Start `ollama serve` ourselves when nothing is answering, so a cold machine
# does not fail every request. Only ever applies to a loopback host, and only
# ever stops a server it started.
"ollama_autostart": True,
"ollama_autostart_wait_s": 30.0,
    "api_key": "",
    "temperature": 0.1,
    "step_budget": 4,
    "output_head": 2000,
    "output_tail": 1200,
    "shell_timeout": 30,
    "skill_timeout": 60,
    "notes_dir": "notes",
    "num_ctx": 8192,
    "search_roots": ["~/Documents", "~/Downloads", "~/Pictures"],
    "telegram_config": "config/telegram.json",
    "memory_dir": "memory",
    "memory_recent": 20,
    # --- offline voice pipeline ---
    "voice_sample_rate": 16000,
    "voice_mic_device": None,
    "voice_speaker_device": None,
    # openWakeWord: the .onnx ships inside the openwakeword wheel
    "voice_wakeword_model": str(Path(__file__).resolve().parent.parent / "models" / "wakeword" / "hey_rhasspy_v0.1.onnx"),
    "voice_wakeword_threshold": 0.5,
    "voice_cooldown_s": 2.0,
    # She answers one request per wake word, then ignores everything (including
    # further wake words) until this window elapses. Set always_asleep to false
    # for the old always-hot behaviour.
    "voice_always_asleep": True,
    "voice_sleep_after_answer_s": 3.0,
    # Minimum gap between two wakes, and how far the score must fall back after
    # firing before another wake can arm. Distinct from voice_cooldown_s, which
    # only suppresses her own playback echoing into the mic.
    "voice_wake_cooldown_s": 2.0,
    "voice_wake_rearm_ratio": 0.6,
    # Hard cap on how long a single wake phrase can hold the detector. Without
    # it, a rearm floor that in-speech audio never dips below would leave her
    # unable to wake again for the rest of the session.
    "voice_wake_max_lock_s": 5.0,
    # Silero VAD: the .onnx ships inside the silero_vad wheel
    "voice_vad_model": "",
    "voice_vad_threshold": 0.5,
    "voice_silence_timeout_s": 0.8,
    "voice_max_record_s": 30,
    "voice_no_speech_timeout_s": 6.0,
    # faster-whisper: local directory only, HF_HUB_OFFLINE is forced at load
    "voice_whisper_model": "",
    "voice_whisper_device": "cpu",
    "voice_whisper_compute_type": "int8",
    # Kokoro ONNX
    "voice_kokoro_model": str(Path(__file__).resolve().parent.parent / "models" / "tts" / "kokoro-v1.0.onnx"),
    "voice_kokoro_voices": str(Path(__file__).resolve().parent.parent / "models" / "tts" / "voices-v1.0.bin"),
    "voice_kokoro_voice": "af_sarah",
    "voice_kokoro_speed": 1.0,
    # keep STT/TTS resident instead of lazy-loading on first use
    "voice_keep_warm": False,
    # seconds of silence before the voice session context resets (Option A:
    # in-memory only; memory/recent.json and the CLI are untouched)
    "voice_session_reset_s": 300,
    # --- mode / online provider (see .env.example) ---
    # offline = everything local; online = Groq for STT + reasoning only.
    # Wake word, VAD, and TTS stay local in every mode.
    "zars_mode": "offline",
    "groq_api_key": "",
    "groq_stt_model": "whisper-large-v3-turbo",
    "groq_llm_model": "openai/gpt-oss-20b",
    "groq_stt_language": "en",
    "groq_timeout_seconds": 15.0,
    # max seconds to honour Retry-After on a 429 before failing over to the
    # local stack; 0 disables waiting entirely
    "groq_retry_after_max_s": 2.0,
    # Gemini is the alternate reasoning provider (ZARS_MODE=gemini). STT still
    # goes to Groq whisper in that mode.
    "gemini_api_key": "",
    "gemini_llm_model": "gemini-flash-lite-latest",
    "gemini_timeout_seconds": 30.0,
    "gemini_retry_after_max_s": 2.0,
    "online_fallback_to_local": True,
    # --- orb overlay speech caption (voice mode only) ---
    # A translucent bubble above the orb that writes what is being spoken.
    # caption_mode: typewriter reveals word by word in time with the audio;
    # instant shows each whole sentence as it starts.
    "voice_orb_caption": True,
    "voice_orb_caption_mode": "typewriter",
    "voice_orb_caption_width": 360,
    "voice_orb_caption_max_lines": 4,
    "voice_orb_caption_fade_ms": 400,
}

# keys sourced from .env; config.json can still override them
ENV_KEYS = {
    "ZARS_MODE": "zars_mode",
    "GROQ_API_KEY": "groq_api_key",
    "GROQ_STT_MODEL": "groq_stt_model",
    "GROQ_LLM_MODEL": "groq_llm_model",
    "GROQ_STT_LANGUAGE": "groq_stt_language",
    "GROQ_TIMEOUT_SECONDS": "groq_timeout_seconds",
    "GROQ_RETRY_AFTER_MAX_S": "groq_retry_after_max_s",
    "ONLINE_FALLBACK_TO_LOCAL": "online_fallback_to_local",
    "GEMINI_API_KEY": "gemini_api_key",
    "GEMINI_LLM_MODEL": "gemini_llm_model",
    "GEMINI_TIMEOUT_SECONDS": "gemini_timeout_seconds",
    "GEMINI_RETRY_AFTER_MAX_S": "gemini_retry_after_max_s",
    # Orb speech caption (voice mode). See DEFAULTS for what each one does.
    "ZARS_ORB_CAPTION": "voice_orb_caption",
    "ZARS_ORB_CAPTION_MODE": "voice_orb_caption_mode",
    "ZARS_ORB_CAPTION_WIDTH": "voice_orb_caption_width",
    "ZARS_ORB_CAPTION_MAX_LINES": "voice_orb_caption_max_lines",
    "ZARS_ORB_CAPTION_FADE_MS": "voice_orb_caption_fade_ms",
    # --- ollama server autostart ---
    "ZARS_OLLAMA_AUTOSTART": "ollama_autostart",
    "ZARS_OLLAMA_AUTOSTART_WAIT_S": "ollama_autostart_wait_s",
    # --- voice sleep/wake policy (see DEFAULTS) ---
    "ZARS_VOICE_ALWAYS_ASLEEP": "voice_always_asleep",
    "ZARS_VOICE_SLEEP_AFTER_ANSWER_S": "voice_sleep_after_answer_s",
    "ZARS_VOICE_WAKEWORD_THRESHOLD": "voice_wakeword_threshold",
    "ZARS_VOICE_WAKE_COOLDOWN_S": "voice_wake_cooldown_s",
    "ZARS_VOICE_WAKE_REARM_RATIO": "voice_wake_rearm_ratio",
    "ZARS_VOICE_WAKE_MAX_LOCK_S": "voice_wake_max_lock_s",
    "ZARS_VOICE_NO_SPEECH_TIMEOUT_S": "voice_no_speech_timeout_s",
    "ZARS_VOICE_MAX_RECORD_S": "voice_max_record_s",
}

# env values coerced to the types DEFAULTS expects
ENV_CASTS = {
    "groq_timeout_seconds": float,
    "groq_retry_after_max_s": float,
    "gemini_timeout_seconds": float,
    "gemini_retry_after_max_s": float,
    "online_fallback_to_local": lambda v: str(v).strip().lower() in ("1", "true", "yes", "on"),
    "voice_orb_caption": lambda v: str(v).strip().lower() in ("1", "true", "yes", "on"),
    "voice_orb_caption_width": int,
    "voice_orb_caption_max_lines": int,
    "voice_orb_caption_fade_ms": int,
    # --- ollama server autostart ---
    "ollama_autostart": lambda v: str(v).strip().lower() in ("1", "true", "yes", "on"),
    "ollama_autostart_wait_s": float,
    # --- voice sleep/wake policy ---
    "voice_always_asleep": lambda v: str(v).strip().lower() in ("1", "true", "yes", "on"),
    "voice_sleep_after_answer_s": float,
    "voice_wakeword_threshold": float,
    "voice_wake_cooldown_s": float,
    "voice_wake_rearm_ratio": float,
    "voice_wake_max_lock_s": float,
    "voice_no_speech_timeout_s": float,
    "voice_max_record_s": float,
}


class Config:
    # Precedence: DEFAULTS < config.json < .env file < process environment.
    # .env is authoritative for the mode/Groq keys (that is the point of
    # "controlled by .env"); a real environment variable beats the file, so
    # `ZARS_MODE=online python main.py --voice` works for a single run.
    def __init__(self, path="config.json", **overrides):
        data = copy.deepcopy(DEFAULTS)  # lists must not be shared between instances
        p = Path(path)
        if p.exists():
            data.update(json.loads(p.read_text(encoding="utf-8")))
        file_env = self._read_env()
        for env_key, cfg_key in ENV_KEYS.items():
            for source in (file_env, os.environ):
                if env_key in source:
                    data[cfg_key] = self._cast(ENV_CASTS.get(cfg_key), source[env_key])
        data.update({k: v for k, v in overrides.items() if v is not None})
        for k, v in data.items():
            setattr(self, k, v)
        self.path = str(p)
        self._resolve_paths()

    @staticmethod
    def _cast(cast, raw):
        if not cast:
            return raw
        try:
            return cast(raw)
        except (TypeError, ValueError):
            return DEFAULTS.get(raw)

    @staticmethod
    def _read_env():
        """Read .env without pulling in a dependency at import time."""
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if not env_path.exists():
            return {}
        try:
            from dotenv import dotenv_values
            return {k: v for k, v in dotenv_values(env_path).items() if v is not None}
        except Exception:
            # Fallback parser if python-dotenv is unavailable.
            out = {}
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
            except OSError:
                pass
            return out

    # config keys holding a filesystem path that may be written relative to
    # the project root in config.json
    PATH_KEYS = (
        "voice_wakeword_model",
        "voice_vad_model",
        "voice_whisper_model",
        "voice_kokoro_model",
        "voice_kokoro_voices",
    )

    def _resolve_paths(self):
        root = Path(__file__).resolve().parent.parent
        for key in self.PATH_KEYS:
            val = getattr(self, key, "")
            if not val:
                continue
            p = Path(val).expanduser()
            if not p.is_absolute():
                p = root / p
            setattr(self, key, str(p))

    def truncate_output(self, text):
        h, t = self.output_head, self.output_tail
        if len(text) <= h + t:
            return text
        return text[:h] + f"\n...[{len(text) - h - t} chars truncated]...\n" + text[-t:]
