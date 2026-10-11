import json
import os
import sys
from pathlib import Path


DEFAULT_SEARCH_ROOTS = [
    "~/Documents",
    "~/Downloads",
    "~/Pictures",
]


def _load_search_roots():
    root = Path(__file__).resolve().parent.parent
    config_path = root / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            roots = cfg.get("search_roots")
            if roots:
                return [Path(r).expanduser().resolve() for r in roots if r]
        except (json.JSONDecodeError, OSError):
            pass
    return [Path(r).expanduser().resolve() for r in DEFAULT_SEARCH_ROOTS]


ALLOWED_ROOTS = [r for r in _load_search_roots() if r.exists()]


def is_within_allowlist(path):
    resolved = Path(path).resolve()
    for root in ALLOWED_ROOTS:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def resolve_path(path):
    resolved = Path(path).expanduser().resolve()
    if not is_within_allowlist(resolved):
        raise ValueError(f"path outside allowed directories: {resolved}")
    return resolved


def safe_emit(ok, data=None, error=None, status=None, display_text=None):
    payload = {"ok": bool(ok), "data": data, "error": error}
    if status:
        payload["status"] = status
    if display_text:
        payload["display_text"] = display_text
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0 if ok else 1)
