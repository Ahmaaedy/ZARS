"""Swappable STT / LLM providers (local + Groq)."""
from .base import (
    AuthError,
    LLMProvider,
    OfflineError,
    ProviderError,
    RateLimitError,
    ServerError,
    STTProvider,
)
from .factory import build_providers, resolve_mode
from .groq_stt import GroqSTT
from .llm_providers import GroqLLM, OllamaLLM
from .local_stt import LocalWhisperSTT, build_local_stt

__all__ = [
    "STTProvider", "LLMProvider", "ProviderError", "AuthError",
    "RateLimitError", "ServerError", "OfflineError",
    "LocalWhisperSTT", "build_local_stt", "GroqSTT",
    "OllamaLLM", "GroqLLM", "build_providers", "resolve_mode",
]