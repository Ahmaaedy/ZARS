"""Provider interfaces for the swappable STT and LLM stages.

The pipeline talks to these interfaces only and never inspects which mode is
active. A factory picks the implementations once at startup.

Design note on LLMProvider: the primary contract is

    pick(messages) -> dict        # {tool, args, command, response}

which is exactly what the existing core/llm.py LLM.pick() returns, so
core/agent.py and core/session.py need no changes. A prose chat() accessor is
also provided, but the orchestrator's reasoning stage goes through pick().
"""


class ProviderError(RuntimeError):
    """Any provider failure. Carries a category so the factory can decide
    whether to fall back to the local stack."""


class AuthError(ProviderError):
    """401/403 -- bad or missing credentials."""


class RateLimitError(ProviderError):
    """429 -- rate limited. `retry_after` holds the seconds if the server sent
    a Retry-After header."""


class ServerError(ProviderError):
    """5xx -- upstream problem."""


class OfflineError(ProviderError):
    """No network / DNS / connection refused."""


class STTProvider:
    """Speech-to-text."""

    name = "stt"

    def transcribe(self, samples, sample_rate=16000):
        """float32 mono @ sample_rate -> transcript string ('' if silence)."""
        raise NotImplementedError


class LLMProvider:
    """Reasoning / tool-picking."""

    name = "llm"

    def system_prompt(self):
        raise NotImplementedError

    def pick(self, messages):
        """List of {role, content} -> tool-pick dict."""
        raise NotImplementedError

    def chat(self, messages, stream=True):
        """Yield raw text chunks. Convenience over pick(); unused by the
        orchestrator, provided so callers can stream prose."""
        raise NotImplementedError