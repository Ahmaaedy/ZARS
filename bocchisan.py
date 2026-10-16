#!/usr/bin/env python3
"""
record_key.py - capture the details of a special key press (media keys,
Fn-combos, vendor keys), save them, and replay them later.

Install:
    pip install pynput

Usage:
    python record_key.py                  # press the key you want to record
    python record_key.py --watch          # print every key event until Esc (diagnostics)
    python record_key.py --replay         # replay the saved key
    python record_key.py --replay --times 3 --delay 2

    --file PATH      where the key is saved/loaded (default: recorded_key.json)
    --timeout SECS   how long to wait for a key when recording (default: 15)

Only the one key you press is saved, and the listener stops immediately after.
Press Esc while recording to cancel.

Notes:
  * If NOTHING is detected when you press e.g. Fn+F9, the key is handled by
    the laptop's firmware/vendor driver and never reaches the OS as a
    keystroke, so it can't be recorded or replayed this way.
  * Windows: the raw scan code and flags are also captured.
  * macOS: grant your terminal "Input Monitoring" + "Accessibility" access.
  * Linux: needs X11 (Wayland blocks global key listeners).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

try:
    from pynput import keyboard
except ImportError:
    sys.exit("This tool requires pynput:  pip install pynput")

DEFAULT_FILE = Path("recorded_key.json")

# Latest raw low-level hook data (Windows only), filled by the event filter.
_raw: dict = {}


def _win32_filter(msg, data):
    _raw.update(raw_vk=data.vkCode, scan_code=data.scanCode, flags=data.flags, msg=msg)
    return True  # let the event through to the normal listener


def _listener_kwargs() -> dict:
    return {"win32_event_filter": _win32_filter} if sys.platform == "win32" else {}


# --------------------------------------------------------------------------- #
# Describing a key
# --------------------------------------------------------------------------- #
def describe(key) -> dict:
    """Return a JSON-serialisable description of a pynput key."""
    info: dict = {"repr": repr(key), "platform": sys.platform}

    if isinstance(key, keyboard.Key):  # named key, e.g. Key.media_volume_up
        info.update(type="named", name=key.name, vk=getattr(key.value, "vk", None))
    elif isinstance(key, keyboard.KeyCode):  # character or raw virtual-key code
        info.update(type="keycode", vk=key.vk, char=key.char)
    else:
        info.update(type="unknown")

    if sys.platform == "win32":
        info.update({k: v for k, v in _raw.items() if k != "msg"})
    return info


def _print_info(info: dict) -> None:
    print("\nCaptured key:")
    for k, v in info.items():
        print(f"  {k:<10} {v}")


# --------------------------------------------------------------------------- #
# Record a single key press
# --------------------------------------------------------------------------- #
def record_one(timeout: float = 15.0) -> dict | None:
    """Wait for one key press and return its description (None if cancelled/timeout)."""
    done = threading.Event()
    result: dict = {}

    def on_press(key):
        if key == keyboard.Key.esc:
            result["cancelled"] = True
        else:
            result["info"] = describe(key)
        done.set()
        return False  # stop the listener

    with keyboard.Listener(on_press=on_press, **_listener_kwargs()) as listener:
        print(f"Press the key you want to record (Esc to cancel, {timeout:g}s timeout)...")
        got_it = done.wait(timeout)
        listener.stop()

    if not got_it:
        print(
            "\nNo key event received.\n"
            "That usually means the key is handled by the laptop's firmware or\n"
            "vendor driver (ACPI/WMI) and never reaches the OS as a keystroke.\n"
            "Try the vendor's app/CLI, or set the mode directly (e.g. powercfg on\n"
            "Windows, powerprofilesctl on Linux)."
        )
        return None
    if result.get("cancelled"):
        print("Cancelled.")
        return None
    return result["info"]


def save(info: dict, path: Path) -> None:
    info = {**info, "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    path.write_text(json.dumps(info, indent=2))
    print(f"\nSaved to {path.resolve()}")


# --------------------------------------------------------------------------- #
# Watch mode (diagnostics)
# --------------------------------------------------------------------------- #
def watch() -> None:
    """Print every key press/release until Esc is pressed."""
    print("Watching key events. Press Esc to stop.\n")

    def on_press(key):
        if key == keyboard.Key.esc:
            return False
        info = describe(key)
        extra = {k: v for k, v in info.items() if k not in ("repr", "platform")}
        print(f"DOWN  {info['repr']:<28} {extra}")

    def on_release(key):
        if key != keyboard.Key.esc:
            print(f"UP    {key!r}")

    with keyboard.Listener(on_press=on_press, on_release=on_release,
                           **_listener_kwargs()) as listener:
        listener.join()


# --------------------------------------------------------------------------- #
# Replay a saved key
# --------------------------------------------------------------------------- #
def _key_from_info(info: dict):
    name = info.get("name")
    if name and hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    if info.get("vk") is not None:
        return keyboard.KeyCode.from_vk(info["vk"])
    if info.get("char"):
        return keyboard.KeyCode.from_char(info["char"])
    raise ValueError("Saved key has no name, vk or char to replay.")


def replay(path: Path = DEFAULT_FILE, times: int = 1, interval: float = 0.2,
           delay: float = 0.0) -> None:
    """Press the key stored in `path`."""
    if not path.exists():
        sys.exit(f"{path} not found - record a key first.")
    info = json.loads(path.read_text())
    key = _key_from_info(info)

    if delay:
        print(f"Replaying in {delay:g}s...")
        time.sleep(delay)

    ctrl = keyboard.Controller()
    for i in range(times):
        ctrl.press(key)
        ctrl.release(key)
        if i < times - 1:
            time.sleep(interval)
    print(f"Sent {info.get('repr', key)} x{times}")


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Record and replay a special key press.")
    p.add_argument("--watch", action="store_true", help="print all key events until Esc")
    p.add_argument("--replay", action="store_true", help="replay the saved key")
    p.add_argument("--file", type=Path, default=DEFAULT_FILE)
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--times", type=int, default=1, help="replay repeat count")
    p.add_argument("--interval", type=float, default=0.2, help="seconds between replays")
    p.add_argument("--delay", type=float, default=0.0, help="seconds before replaying")
    args = p.parse_args()

    if args.watch:
        watch()
    elif args.replay:
        replay(args.file, args.times, args.interval, args.delay)
    else:
        info = record_one(args.timeout)
        if info:
            _print_info(info)
            save(info, args.file)


if __name__ == "__main__":
    main()