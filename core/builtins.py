import re
import threading
import time
from pathlib import Path


def write_note(config, content):
    d = Path(config.notes_dir)
    d.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", str(content).lower()).strip("-")[:40] or "note"
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.txt"
    path = d / name
    path.write_text(str(content), encoding="utf-8")
    return True, f"saved note: {path}"


def search_notes(config, query):
    d = Path(config.notes_dir)
    if not d.exists():
        return True, "no notes saved yet"
    q = str(query).lower()
    hits = []
    for f in sorted(d.glob("*.txt"), reverse=True):
        text = f.read_text(encoding="utf-8", errors="replace")
        low = text.lower()
        idx = low.find(q.lower())
        if idx >= 0 or q in f.name.lower():
            snippet = text[max(0, idx - 30): idx + 70].replace("\n", " ") if idx >= 0 else text[:80]
            hits.append(f"- {f.name}: ...{snippet}...")
        if len(hits) >= 10:
            break
    if not hits:
        return True, f"no notes matching '{query}'"
    return True, f"{len(hits)} match(es):\n" + "\n".join(hits)


def _parse_duration(s):
    """Parse duration like 30s, 5m, 1h30m into seconds."""
    s = str(s).strip().lower()
    total = 0
    num = ""
    for ch in s:
        if ch.isdigit() or ch == ".":
            num += ch
        elif ch == "h":
            total += int(float(num) * 3600) if num else 0
            num = ""
        elif ch == "m":
            total += int(float(num) * 60) if num else 0
            num = ""
        elif ch == "s":
            total += int(float(num)) if num else 0
            num = ""
    if num:
        total += int(float(num))
    return total


def _timer_beep(seconds):
    """Sleep then beep using winsound."""
    time.sleep(seconds)
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1000, 300)
            time.sleep(0.15)
    except (ImportError, RuntimeError):
        pass


def set_timer(config, duration):
    seconds = _parse_duration(duration)
    if seconds <= 0:
        return False, f"invalid duration: {duration!r}"
    t = threading.Thread(target=_timer_beep, args=(seconds,), daemon=True)
    t.start()
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    label = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s"
    return True, f"timer started: {label} (beep when done)"


BUILTINS = {
    "write_note": write_note,
    "search_notes": search_notes,
    "set_timer": set_timer,
}