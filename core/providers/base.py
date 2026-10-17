"""Provider interfaces for the swappable STT and LLM stages.

The pipeline talks to these interfaces only and never inspects which mode is
active. A factory picks the implementations once at startup.

Design note on LLMProvider: the primary contract is

    pick(messages) -> dict        # {tool, args, command, response}

which is exactly what the existing core/llm.py LLM.pick() returns, so
core/agent.py and core/session.py need no changes. A prose chat() accessor is
also provided, but the orchestrator's reasoning stage goes through pick().
"""
import time


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


class MalformedOutputError(ProviderError):
    """The model emitted output the provider could not parse.

    Deliberately not a ServerError: the service is healthy and the request was
    well-formed, so the same prompt usually succeeds on a retry or through the
    provider's other decoding path. Callers should exhaust those cheap options
    before failing over to another provider.
    """


def call_with_retry(fn, max_wait, sleep=time.sleep):
    """Run fn(); on a 429 whose Retry-After fits the budget, wait once and
    retry exactly once.

    Lives here so every cloud provider shares one implementation. Deliberately
    NOT a loop: a voice assistant should stall briefly rather than stall
    indefinitely, and the caller still has a fallback available. A wait longer
    than max_wait (or no Retry-After at all) fails straight through so fallback
    happens immediately. max_wait <= 0 disables waiting.
    """
    try:
        return fn()
    except RateLimitError as e:
        ra = getattr(e, "retry_after", None)
        if max_wait <= 0 or ra is None or ra <= 0 or ra > max_wait:
            raise
        sleep(min(ra, max_wait))
        return fn()  # single retry; a second failure propagates


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