import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, manifest_for
from core.skill_utils import safe_emit

VALID_ACTIONS = {"lock", "sleep", "hibernate", "restart", "shutdown", "cancel"}
MIN_DELAY = 10
MAX_DELAY = 3600
DEFAULT_DELAY = 30


def _run_ps(command):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True, text=True, timeout=15,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _is_power_pending():
    code, out, _ = _run_ps("shutdown /query")
    return code == 0 and "is scheduled" in out.lower()


def _lock():
    code, _, err = _run_ps("rundll32.exe user32.dll,LockWorkStation")
    if code != 0:
        return False, f"lock failed: {err}"
    return True, "screen locked"


def _sleep():
    ps = "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false)"
    code, _, err = _run_ps(ps)
    if code != 0:
        return False, f"sleep failed: {err}"
    return True, "entering sleep"


def _hibernate():
    ps = "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState('Hibernate',$false,$false)"
    code, _, err = _run_ps(ps)
    if code != 0:
        return False, f"hibernate failed: {err}"
    return True, "entering hibernate"


def _restart(delay):
    code, _, err = _run_ps(f"shutdown /r /t {delay}")
    if code != 0:
        return False, f"restart failed: {err}"
    scheduled = time.strftime("%H:%M:%S", time.localtime(time.time() + delay))
    return True, f"restart scheduled in {delay}s (at {scheduled})"


def _shutdown(delay):
    code, _, err = _run_ps(f"shutdown /s /t {delay}")
    if code != 0:
        return False, f"shutdown failed: {err}"
    scheduled = time.strftime("%H:%M:%S", time.localtime(time.time() + delay))
    return True, f"shutdown scheduled in {delay}s (at {scheduled})"


def _cancel():
    if not _is_power_pending():
        return True, "no shutdown/restart is currently pending"
    code, _, err = _run_ps("shutdown /a")
    if code != 0:
        return False, f"cancel failed: {err}"
    return True, "pending power action cancelled"


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    if not ns.action:
        safe_emit(False, error="action is required")
    action = ns.action.lower().strip()
    if action not in VALID_ACTIONS:
        safe_emit(False, error=f"unknown action: {action}. valid: {', '.join(sorted(VALID_ACTIONS))}")

    if action in ("restart", "shutdown"):
        delay = ns.delay if ns.delay is not None else DEFAULT_DELAY
        if delay < MIN_DELAY:
            safe_emit(False, error=f"delay must be at least {MIN_DELAY}s for {action}")
        if delay > MAX_DELAY:
            safe_emit(False, error=f"delay cannot exceed {MAX_DELAY}s (1 hour)")
        if _is_power_pending():
            safe_emit(False, error="a shutdown/restart is already pending. run 'cancel' first", status="already_pending")

    dispatch = {
        "lock": lambda: _lock(),
        "sleep": lambda: _sleep(),
        "hibernate": lambda: _hibernate(),
        "restart": lambda: _restart(delay),
        "shutdown": lambda: _shutdown(delay),
        "cancel": lambda: _cancel(),
    }

    ok, msg = dispatch[action]()
    data = {"action": action, "platform": "windows"}
    if action in ("restart", "shutdown") and ok:
        data["delay"] = delay
        data["scheduled_time"] = time.strftime("%H:%M:%S", time.localtime(time.time() + delay))
    safe_emit(ok, data, error=msg if not ok else None, display_text=msg)


if __name__ == "__main__":
    main()
