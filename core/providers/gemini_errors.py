"""Google Gemini error translation.

Maps google.genai exceptions onto the shared provider error categories so the
factory can decide fallback behaviour without importing the SDK.
"""
from google.genai import errors as genai_errors

from .base import AuthError, OfflineError, ProviderError, RateLimitError, ServerError


def _status(err):
    """Best-effort HTTP status from a google.genai error."""
    for attr in ("code", "status_code"):
        val = getattr(err, attr, None)
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    resp = getattr(err, "response", None) or getattr(err, "_response", None)
    val = getattr(resp, "status_code", None)
    return val if isinstance(val, int) else None


def _retry_after_seconds(err):
    resp = getattr(err, "response", None)
    value = None
    if resp is not None:
        try:
            value = resp.headers.get("retry-after")
        except Exception:
            value = None
    if value is None:
        value = getattr(err, "retry_after", None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def translate(err, what="gemini"):
    """google.genai exception -> our provider exception. Never returns None.

    google.genai raises ClientError / ServerError / APIError rather than a
    dedicated connection error, so transport failures are detected from the
    message text.
    """
    status = _status(err)
    msg = str(err).lower()

    if "timeout" in msg or "deadline" in msg:
        return OfflineError(f"{what}: request timed out")
    if "connection" in msg or "dns" in msg or "network" in msg:
        return OfflineError(f"{what}: cannot reach the service (no network)")

    if status in (401, 403):
        return AuthError(f"{what}: authentication failed ({status}) -- check GEMINI_API_KEY")
    if status == 429:
        ra = _retry_after_seconds(err)
        hint = f"; Retry-After={ra:.0f}s" if ra else ""
        target = RateLimitError(f"{what}: rate limited (429){hint}")
        target.retry_after = ra
        return target
    if isinstance(err, genai_errors.ServerError) or (status and status >= 500):
        return ServerError(f"{what}: upstream server error ({status or '5xx'})")
    if isinstance(err, genai_errors.ClientError) or (status and 400 <= status < 500):
        return ProviderError(f"{what}: request rejected ({status or '4xx'})")
    return ProviderError(f"{what}: {type(err).__name__}: {err}")