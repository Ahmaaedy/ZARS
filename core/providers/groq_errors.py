"""Groq error translation.

Maps groq SDK exceptions onto the provider error categories so the factory can
decide fallback behaviour without importing the SDK.

Also enforces the "do not retry in a loop" rule: callers get a Retry-After hint
and are expected to try at most once.
"""
import groq

from .base import (
    AuthError,
    MalformedOutputError,
    OfflineError,
    ProviderError,
    RateLimitError,
    ServerError,
    call_with_retry,
)

# Re-exported so existing `from .groq_errors import call_with_retry` callers
# keep working now that it lives in base.py (provider-agnostic).
__all__ = ["translate", "call_with_retry", "MalformedOutputError"]

# Groq words the same underlying problem several different ways depending on
# which layer rejected the generation, so match on the shapes rather than on one
# exact string. gpt-oss in particular hits "Parsing failed..." intermittently
# and without any mention of a tool call, which is why callers must key off the
# error class rather than off the message.
_MALFORMED_MARKERS = (
    "parsing failed",
    "could not be parsed",
    "failed_generation",
    "tool call validation failed",
    "tool choice is none",
    "model called a tool",
    "invalid json",
)


def _looks_malformed(err):
    text = str(err).lower()
    return any(marker in text for marker in _MALFORMED_MARKERS)


def _retry_after_seconds(err):
    """Pull Retry-After off the response headers if present."""
    resp = getattr(err, "response", None)
    if resp is None:
        return None
    value = None
    try:
        value = resp.headers.get("retry-after")
    except Exception:
        value = None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def translate(err, what="groq"):
    """groq exception -> our provider exception. Never returns None."""
    if isinstance(err, groq.AuthenticationError):
        return AuthError(f"{what}: authentication failed (401) -- check GROQ_API_KEY")
    if isinstance(err, groq.RateLimitError):
        ra = _retry_after_seconds(err)
        hint = f"; Retry-After={ra:.0f}s" if ra else ""
        target = RateLimitError(f"{what}: rate limited (429){hint}")
        target.retry_after = ra  # read by call_with_retry
        return target
    if isinstance(err, groq.PermissionDeniedError):
        return AuthError(f"{what}: permission denied (403) -- key lacks access")
    if isinstance(err, (groq.InternalServerError,)):
        return ServerError(f"{what}: upstream server error (5xx)")
    if isinstance(err, groq.APITimeoutError):
        return OfflineError(f"{what}: request timed out")
    if isinstance(err, groq.APIConnectionError):
        return OfflineError(f"{what}: cannot reach the service (no network)")
    # Checked before the status branches below because Groq raises this both as
    # an APIStatusError (400) and as a plain APIError with no status at all, and
    # only the former would be caught by a status-code test.
    if _looks_malformed(err):
        return MalformedOutputError(f"{what}: model output could not be parsed -- {err}")
    if isinstance(err, groq.APIStatusError):
        status = getattr(err, "status_code", "?")
        if isinstance(status, int) and status >= 500:
            return ServerError(f"{what}: upstream server error ({status})")
        return ProviderError(f"{what}: request failed ({status})")
    return ProviderError(f"{what}: {type(err).__name__}: {err}")
