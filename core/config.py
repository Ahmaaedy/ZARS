import copy
import json
import os
from pathlib import Path

DEFAULTS = {
    "provider": "ollama",
    "model": "qwen2.5:3b-instruct",
    "host": "http://localhost:11434",
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
    "online_fallback_to_local": True,
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
}

# env values coerced to the types DEFAULTS expects
ENV_CASTS = {
    "groq_timeout_seconds": float,
    "groq_retry_after_max_s": float,
    "online_fallback_to_local": lambda v: str(v).strip().lower() in ("1", "true", "yes", "on"),
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
