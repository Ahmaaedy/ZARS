"""Which audio device to actually open, and how to tell a working one from a
plausible-looking dud.

Two lessons shaped this module, both measured on the author's machine rather
than taken from documentation:

  * `check_input_settings` passing does NOT mean a capture stream works. Every
    DirectSound input device here reports 16 kHz as supported and then delivers
    silence at ~6000x realtime, because the backend cannot resample 44.1 kHz and
    free-runs instead. MME captures correctly at 1.0x realtime. So input prefers
    MME -- the opposite of the usual advice -- and validate_input_alive is what
    actually enforces it.
  * `OutputStream.write()` is asynchronous and returns immediately on a healthy
    device, so a fast write proves nothing. Playback therefore runs through the
    stream callback in audio_io.Speaker, which can actually tell when the
    buffer finished.

Windows also exposes one physical device under several PortAudio host APIs with
different names and channel counts, and PortAudio's default index moves while
you are not looking (the default input went 1 -> 3 -> 4 across probes as a USB
mic came and went). So nothing here trusts position: candidates are filtered by
capability, ordered by a measured preference, then validated at open time.
"""
import time

import sounddevice as sd

# Measured preferences. These are hints, not correctness: validate_output_alive /
# validate_input_alive are what actually pick a working device, so a machine
# where DirectSound capture behaves will still be served correctly.
#
# Input prefers MME because the DirectSound capture backend free-runs here.
# Output prefers DirectSound because every WDM-KS endpoint on this machine fails
# to open at all ("Blocking API not available", PaErrorCode -9999).
DEFAULT_INPUT_HOSTS = ("MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS")
DEFAULT_OUTPUT_HOSTS = ("Windows DirectSound", "MME", "Windows WASAPI", "Windows WDM-KS")

# A capture stream outside this band is degenerate (free-running or stalled).
INPUT_RATE_BAND = (0.25, 4.0)

# Endpoints that are mappers rather than hardware. "Primary Sound Capture
# Driver" is DirectSound's default-capture mapper: it opens happily, reports
# 16 kHz, and delivers silence because there is no real source behind it. A
# last resort for an automatic choice, never a first one, and never when the
# config names a device explicitly.
_GENERIC_DEVICE_MARKS = ("primary sound", "sound mapper")
_GENERIC_DEVICE_NAMES = {"input", "output", "input ()", "output ()"}

_input_hosts = list(DEFAULT_INPUT_HOSTS)
_output_hosts = list(DEFAULT_OUTPUT_HOSTS)


def set_host_preference(value, kind=None):
    """Override the host ranking. Accepts a list or a comma-separated string.

    With kind=None both directions are set; kind="input"/"output" sets one.
    Empty/None restores the measured default for the affected direction.
    Unrecognised names rank last rather than being dropped, so a typo degrades
    to "try everything" instead of quietly matching nothing.
    """
    global _input_hosts, _output_hosts
    parsed = None
    if value:
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",")]
        else:
            parts = [str(p) for p in value]
        parsed = [p for p in parts if p] or None
    if kind == "input":
        _input_hosts = parsed or list(DEFAULT_INPUT_HOSTS)
    elif kind == "output":
        _output_hosts = parsed or list(DEFAULT_OUTPUT_HOSTS)
    else:
        _input_hosts = parsed or list(DEFAULT_INPUT_HOSTS)
        _output_hosts = list(parsed) if parsed else list(DEFAULT_OUTPUT_HOSTS)


def current_preference():
    return tuple(_input_hosts), tuple(_output_hosts)


def _rank(name, kind):
    order = _input_hosts if kind == "input" else _output_hosts
    try:
        return order.index(name)
    except ValueError:
        return len(order)


def host_name(hostapi_index):
    try:
        return sd.query_hostapis()[hostapi_index]["name"]
    except Exception:
        return ""


def _is_generic(name):
    n = (name or "").strip().lower()
    return n in _GENERIC_DEVICE_NAMES or any(m in n for m in _GENERIC_DEVICE_MARKS)


def _name_match(device_name, wanted):
    """0 for an exact match, 1 for a substring match, None for no match.

    Host-API variants of one device are frequently named differently
    ("...Enhance" vs "...Enhancer"), so exact-first with a substring fallback
    keeps a configured name working across them.
    """
    w = wanted.strip().lower()
    n = (device_name or "").strip().lower()
    if not w:
        return 0
    if w == n:
        return 0
    return 1 if w in n else None


def _system_default(kind):
    try:
        pair = sd.default.device
        return pair[0] if kind == "input" else pair[1]
    except Exception:
        return None


def candidates(kind, rate, channels=1, dtype="float32", wanted=None, limit=None):
    """Device indices usable for `kind` at `rate`, best first.

    `kind` is "input" or "output". `wanted` is a device name (or an index as a
    string) from config, or None/empty to choose automatically. Returns
    (index, name, host_api_name) tuples.
    """
    probe = sd.check_input_settings if kind == "input" else sd.check_output_settings
    found = []
    try:
        inventory = sd.query_devices()
    except Exception:
        return found

    pinned = wanted is not None and str(wanted).strip()
    default_index = _system_default(kind)

    for index, dev in enumerate(inventory):
        if kind == "input":
            if not dev["max_input_channels"]:
                continue
        elif not dev["max_output_channels"]:
            continue

        try:
            probe(device=index, channels=channels, dtype=dtype, samplerate=rate)
        except Exception:
            continue  # cannot do this rate on this device; not a candidate

        name = dev["name"]
        host = host_name(dev["hostapi"])

        if pinned:
            text = str(wanted).strip()
            if text.lstrip("-").isdigit():
                if index != int(text):
                    continue
                score = 0
            else:
                score = _name_match(name, text)
                if score is None:
                    continue
            # An explicit choice is taken at face value, even a mapper.
            found.append((score, 0, 0, index, name, host))
            continue

        # Automatic: keep the system default when it is real hardware on a
        # better-than-last host API, because that is what Windows itself thinks
        # the user wants. Mappers are demoted hard.
        demote = (0 if index == default_index else 1) + _rank(host, kind)
        if _is_generic(name):
            demote += 100
        found.append((0, demote, _rank(host, kind), index, name, host))

    found.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    out = [(index, name, host) for _, _, _, index, name, host in found]
    return out[:limit] if limit else out


def validate_input_alive(stream, sample_rate, block_frames, budget_s=0.3):
    """True when the capture stream actually delivers audio at wall-clock rate.

    A free-running backend returns frames far faster than real time and hands
    back silence; feeding that to the wake detector would look like a mic that
    is always silent. Costs up to budget_s once per candidate.
    """
    frames = 0
    start = time.monotonic()
    while time.monotonic() - start < budget_s:
        try:
            frame, overflowed = stream.read(block_frames)
        except Exception:
            return False
        if not overflowed:
            frames += 1
    elapsed = max(1e-6, time.monotonic() - start)
    ratio = (frames * block_frames / float(sample_rate)) / elapsed
    low, high = INPUT_RATE_BAND
    return low <= ratio <= high


def describe(index):
    try:
        dev = sd.query_devices()[index]
        return "%d: %s [%s]" % (index, dev["name"], host_name(dev["hostapi"]))
    except Exception:
        return str(index)


def listing(kind, rate, channels=1, dtype="float32"):
    """Inventory for error messages, so a failed open tells the user what they
    can choose from rather than only that it failed."""
    probe = sd.check_input_settings if kind == "input" else sd.check_output_settings
    lines = []
    try:
        inventory = sd.query_devices()
    except Exception as e:
        return ["  could not enumerate audio devices: %s" % e]
    for index, dev in enumerate(inventory):
        if kind == "input" and not dev["max_input_channels"]:
            continue
        if kind == "output" and not dev["max_output_channels"]:
            continue
        try:
            probe(device=index, channels=channels, dtype=dtype, samplerate=rate)
            ok = "usable"
        except Exception:
            ok = "no %d Hz" % rate
        lines.append("  %2d  %-44s %-18s %s"
                     % (index, dev["name"][:44], host_name(dev["hostapi"])[:18], ok))
    return lines or ["  (no devices of this kind)"]


def no_device_error(kind, rate, wanted, reason=None):
    label = "input" if kind == "input" else "output"
    head = "no %s device can run at %d Hz" % (label, rate)
    if wanted is not None and str(wanted).strip():
        head += " matching %r" % str(wanted).strip()
    if reason:
        head += " (%s)" % reason
    return "%s.\n%s devices at %d Hz:\n%s" % (
        head, label.capitalize(), rate, "\n".join(listing(kind, rate))
    )