import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, manifest_for
from core.skill_utils import safe_emit

VALID_TARGETS = {"volume", "brightness"}
VALID_OPS = {"set", "get", "mute", "unmute"}

NAMED_LEVELS = {
    "max": 100, "full": 100, "loud": 100, "high": 90,
    "half": 50, "mid": 50, "medium": 50,
    "min": 0, "off": 0, "mute": 0, "silent": 0, "quiet": 0,
}

WMI_NAMESPACE = "root/WMI"


def _parse_level(raw):
    text = str(raw).strip().lower().rstrip("%").strip()
    if text in NAMED_LEVELS:
        return NAMED_LEVELS[text]
    try:
        value = float(text)
    except ValueError:
        return None
    return max(0, min(100, int(round(value))))


def _run_ps(script):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# --------------------------------------------------------------------------- #
# Volume (Core Audio via pycaw)
# --------------------------------------------------------------------------- #
def _speaker():
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities, ERole, EDataFlow, IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator
    except ImportError:
        safe_emit(False, error="pycaw is not installed - run: pip install pycaw comtypes")
    try:
        enumerator = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
        endpoint = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eConsole.value)
        return AudioUtilities.CreateDevice(endpoint)
    except Exception:
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetSpeakers()


def _volume():
    try:
        device = _speaker()
    except Exception as e:
        safe_emit(False, error=f"no audio output device: {e}")
    if device is None:
        safe_emit(False, error="no audio output device found")
    return device


def _run_volume(op, level_raw):
    device = _volume()
    try:
        if op == "get":
            current = round(device.volume_percent, 1)
            muted = bool(device.EndpointVolume.GetMute())
            return {
                "target": "volume", "op": op, "level": current, "muted": muted,
                "device": device.FriendlyName, "platform": "windows",
            }, f"volume is at {current:.0f}%" + (" (muted)" if muted else "")

        if op in ("mute", "unmute"):
            device.EndpointVolume.SetMute(1 if op == "mute" else 0, None)
            return {
                "target": "volume", "op": op, "muted": op == "mute",
                "level": round(device.volume_percent, 1),
                "device": device.FriendlyName, "platform": "windows",
            }, "audio muted" if op == "mute" else "audio unmuted"

        if level_raw is None:
            safe_emit(False, error="level is required for op=set, e.g. --level 50")
        level = _parse_level(level_raw)
        if level is None:
            safe_emit(False, error=f"invalid level: {level_raw!r}. use 0-100, or max/min/half")
        device.volume_percent = level
        return {
            "target": "volume", "op": op, "level": level,
            "muted": bool(device.EndpointVolume.GetMute()),
            "device": device.FriendlyName, "platform": "windows",
        }, f"volume set to {level}%"
    except Exception as e:
        safe_emit(False, error=f"volume control failed: {e}")


# --------------------------------------------------------------------------- #
# Brightness (WMI internal panel + Dxva2 external monitors)
# --------------------------------------------------------------------------- #
def _monitor_handles():
    handles = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )

    def collect(handle, hdc, rect, data):
        handles.append(handle)
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(collect), 0)
    return handles


def _wmi_read():
    script = (
        f"$m = Get-CimInstance -Namespace {WMI_NAMESPACE} -ClassName WmiMonitorBrightness "
        "-ErrorAction SilentlyContinue; if ($m) { $m | ForEach-Object { $_.CurrentBrightness } }"
    )
    code, out, _ = _run_ps(script)
    values = [int(line) for line in out.split() if line.strip().isdigit()]
    if code != 0 or not values:
        return []
    return values


def _wmi_set(level):
    script = (
        f"$m = Get-CimInstance -Namespace {WMI_NAMESPACE} -ClassName WmiMonitorBrightnessMethods "
        f"-ErrorAction SilentlyContinue; if ($m) {{ "
        f"Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness "
        f"-Arguments @{{ Timeout = 1; Brightness = {level} }} -ErrorAction SilentlyContinue | Out-Null }} "
        f"if (-not $m) {{ Write-Output 'UNSUPPORTED' }}"
    )
    code, out, err = _run_ps(script)
    if code != 0:
        return False, err or f"powershell exited with {code}"
    if "UNSUPPORTED" in out:
        return False, "no WMI brightness panel"
    return True, None


def _dxva_read():
    results = []
    dxva = ctypes.windll.Dxva2
    for handle in _monitor_handles():
        current, minimum = ctypes.c_long(0), ctypes.c_long(0)
        if dxva.GetMonitorBrightness(handle, ctypes.byref(current), ctypes.byref(minimum)):
            results.append((handle, current.value, minimum.value))
    return results


def _dxva_set(entries, level):
    dxva = ctypes.windll.Dxva2
    changed = []
    for handle, _current, minimum in entries:
        target = max(level, minimum if minimum > 0 else 0)
        if dxva.SetMonitorBrightness(handle, ctypes.c_long(target), ctypes.c_long(minimum)):
            changed.append((handle, target))
    return changed


def _run_brightness(op, level_raw):
    if op in ("mute", "unmute"):
        safe_emit(False, error=f"op={op} only applies to volume, not brightness")

    displays = []
    wmi_ok, wmi_err = True, None

    if op == "set":
        if level_raw is None:
            safe_emit(False, error="level is required for op=set, e.g. --level 60")
        level = _parse_level(level_raw)
        if level is None:
            safe_emit(False, error=f"invalid level: {level_raw!r}. use 0-100, or max/min/half")
        wmi_ok, wmi_err = _wmi_set(level)
        if wmi_ok:
            displays.extend({"display": "internal panel (WMI)", "level": level} for _ in range(len(_wmi_read()) or 1))
        for handle, target in _dxva_set(_dxva_read(), level):
            displays.append({"display": f"monitor {handle}", "level": target})
        if not displays:
            detail = wmi_err if wmi_ok is False and wmi_err else "no DDC/CI-capable monitor found"
            safe_emit(False, error=f"brightness could not be set: {detail}")
        return {
            "target": "brightness", "op": op, "level": level,
            "displays": displays, "platform": "windows",
        }, f"brightness set to {level}% on {len(displays)} display(s)"

    for value in _wmi_read():
        displays.append({"display": "internal panel (WMI)", "level": value})
    for handle, current, _minimum in _dxva_read():
        displays.append({"display": f"monitor {handle}", "level": current})
    if not displays:
        safe_emit(False, error="brightness unavailable: no WMI panel and no DDC/CI-capable monitor")
    current = displays[0]["level"]
    return {
        "target": "brightness", "op": "get", "level": current,
        "displays": displays, "platform": "windows",
    }, f"brightness is at {current}%"


def main():
    if sys.platform != "win32":
        safe_emit(False, error="display_audio only works on Windows")

    ns = build_parser(manifest_for(__file__)).parse_args()
    target = (ns.target or "").strip().lower()
    op = (ns.op or "set").strip().lower()

    if target not in VALID_TARGETS:
        safe_emit(False, error=f"unknown target: {target or '(none)'}. valid: {', '.join(sorted(VALID_TARGETS))}")
    if op not in VALID_OPS:
        safe_emit(False, error=f"unknown op: {op}. valid: {', '.join(sorted(VALID_OPS))}")

    data, display = _run_volume(op, ns.level) if target == "volume" else _run_brightness(op, ns.level)
    safe_emit(True, data, display_text=display)


if __name__ == "__main__":
    main()