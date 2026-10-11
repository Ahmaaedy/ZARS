import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, manifest_for
from core.skill_utils import safe_emit

VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
KEYEVENTF_KEYUP = 0x0002

VALID_ACTIONS = {"play", "pause", "toggle", "next", "previous"}

ACTION_MAP = {
    "play": VK_MEDIA_PLAY_PAUSE,
    "pause": VK_MEDIA_PLAY_PAUSE,
    "toggle": VK_MEDIA_PLAY_PAUSE,
    "next": VK_MEDIA_NEXT_TRACK,
    "previous": VK_MEDIA_PREV_TRACK,
}

ACTION_LABELS = {
    "play": "play/pause",
    "pause": "play/pause",
    "toggle": "play/pause",
    "next": "next track",
    "previous": "previous track",
}


def _send_media_key(vk_code):
    user32 = ctypes.windll.user32
    user32.keybd_event(vk_code, 0, 0, 0)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    if not ns.action:
        safe_emit(False, error="action is required")
    action = ns.action.lower().strip()
    if action not in VALID_ACTIONS:
        safe_emit(False, error=f"unknown action: {action}. valid: {', '.join(sorted(VALID_ACTIONS))}")

    vk_code = ACTION_MAP[action]
    try:
        _send_media_key(vk_code)
    except Exception as e:
        safe_emit(False, error=f"media key failed: {e}")

    label = ACTION_LABELS[action]
    safe_emit(
        True,
        {"action": action, "platform": "windows", "now_playing": "metadata unavailable on Windows"},
        display_text=f"{label} key sent",
    )


if __name__ == "__main__":
    main()
