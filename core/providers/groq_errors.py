"""Groq error translation.

Maps groq SDK exceptions onto the provider error categories so the factory can
decide fallback behaviour without importing the SDK.

Also enforces the "do not retry in a loop" rule: callers get a Retry-After hint
and are expected to try at most once.
"""
import time

import groq

from .base import AuthError, OfflineError, ProviderError, RateLimitError, ServerError


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
    if isinstance(err, groq.APIStatusError):
        status = getattr(err, "status_code", "?")
        if isinstance(status, int) and status >= 500:
            return ServerError(f"{what}: upstream server error ({status})")
        return ProviderError(f"{what}: request failed ({status})")
    return ProviderError(f"{what}: {type(err).__name__}: {err}")


def call_with_retry(fn, max_wait, sleep=time.sleep):
    """Run fn(); on a 429 whose Retry-After fits the budget, wait once and
    retry exactly once.

    Deliberately NOT a loop: a voice assistant should stall briefly rather than
    stall indefinitely, and the caller still has local fallback available. A
    wait longer than max_wait (or no Retry-After at all) fails straight through
    so fallback happens immediately. max_wait <= 0 disables waiting.
    """
    try:
        return fn()
    except RateLimitError as e:
        ra = getattr(e, "retry_after", None)
        if max_wait <= 0 or ra is None or ra <= 0 or ra > max_wait:
            raise
        sleep(min(ra, max_wait))
        return fn()  # single retry; a second failure propagates