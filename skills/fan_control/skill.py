import ctypes
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, manifest_for
from core.skill_utils import safe_emit

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEY_FILE = ROOT / "recorded_key.json"

VALID_ACTIONS = {"max", "quiet", "toggle"}

ACTION_HINTS = {
    "max": "fans boosted to max",
    "quiet": "fans dropped to quiet",
    "toggle": "fan mode toggled",
}


def _load_key(path):
    if not path.exists():
        safe_emit(False, error=f"recorded key file not found: {path} - record one with: python bocchisan.py")
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        safe_emit(False, error=f"cannot read {path.name}: {e}")
    if not isinstance(info, dict):
        safe_emit(False, error=f"{path.name} does not contain a key object")

    scan = info.get("scan_code")
    vk = info.get("vk")
    if not scan and not vk:
        safe_emit(False, error=f"{path.name} has no scan_code or vk - re-record it with: python bocchisan.py")
    if info.get("name"):
        label = info["name"]
    elif vk:
        label = f"vk {vk}"
    else:
        label = f"scan {scan}"
    return scan, vk, label


def _tap(scan, vk):
    keybd_event = ctypes.windll.user32.keybd_event
    if scan:
        keybd_event(0, scan, KEYEVENTF_SCANCODE, 0)
        keybd_event(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)
    else:
        keybd_event(vk, 0, 0, 0)
        keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def main():
    if sys.platform != "win32":
        safe_emit(False, error="fan_control only works on Windows")

    ns = build_parser(manifest_for(__file__)).parse_args()
    action = (ns.action or "").lower().strip()
    if action not in VALID_ACTIONS:
        safe_emit(False, error=f"unknown action: {action or '(none)'}. valid: {', '.join(sorted(VALID_ACTIONS))}")

    path = Path(ns.file).expanduser() if ns.file else DEFAULT_KEY_FILE
    scan, vk, label = _load_key(path)

    try:
        _tap(scan, vk)
    except Exception as e:
        safe_emit(False, error=f"fan key failed: {e}")

    safe_emit(
        True,
        {"action": action, "key": label, "vk": vk, "scan_code": scan, "file": str(path), "platform": "windows"},
        display_text=f"{ACTION_HINTS[action]} ({label})",
    )


if __name__ == "__main__":
    main()