import argparse
import json
import sys
from pathlib import Path

_TYPE_MAP = {"string": str, "number": float, "integer": int}


def manifest_for(script_file):
    p = Path(script_file).resolve().parent / "skill.json"
    return json.loads(p.read_text(encoding="utf-8"))


def build_parser(manifest):
    ap = argparse.ArgumentParser(description=manifest.get("desc", ""))
    for param in manifest.get("params", []):
        name = param["name"]
        ptype = param.get("type", "string")
        if ptype == "boolean":
            ap.add_argument(f"--{name}", action="store_true", help=param.get("desc", ""))
        else:
            ap.add_argument(
                f"--{name}",
                type=_TYPE_MAP.get(ptype, str),
                required=param.get("required", False),
                default=param.get("default"),
                help=param.get("desc", ""),
            )
    return ap


def emit(ok, data=None, error=None, status=None, display_text=None):
    payload = {"ok": bool(ok), "data": data, "error": error}
    if status:
        payload["status"] = status
    if display_text:
        payload["display_text"] = display_text
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0 if ok else 1)