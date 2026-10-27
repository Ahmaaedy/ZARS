"""Plain-text mirror of everything the ZARS terminal prints.

The Rich console is handed a Tee instead of stdout, so the terminal keeps its
colours and the log keeps the same text with the ANSI escapes stripped. One
file per session, so it never grows without bound.

Nothing else in ZARS has to know this exists: every line the app shows goes
through the one Console object, and the Console writes to self.file.
"""
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

# CSI sequences -- colours, cursor moves, erase-screen. Rich emits them for the
# terminal copy; the log keeps only the visible characters.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

LOG_DIR = "logs"


class Tee:
    """File proxy: the terminal gets the bytes, the log gets the plain text.

    `isatty`, `fileno` and `encoding` proxy stdout, so Rich still sees a real
    terminal: colours, width detection and the modern-Windows write path are
    all unchanged. Only the text is duplicated, never the formatting.
    """

    def __init__(self, path=None):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = Path(path) if path else Path(LOG_DIR) / f"zars-{stamp}.txt"
        self._lock = threading.Lock()
        self._fh = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not self.path.exists() or self.path.stat().st_size == 0
            self._fh = open(self.path, "a", encoding="utf-8", errors="replace")
            if fresh:
                # A BOM, so Notepad and PowerShell read the file as UTF-8
                # instead of the system ANSI codepage.
                self._fh.write("\ufeff")
            self._fh.write(f"=== zars session {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
            self._fh.flush()
        except OSError:
            # A log is never worth a crash. Degrade to a plain stdout proxy.
            self._fh = None

    def write(self, text):
        """Rich writes here for every print."""
        sys.stdout.write(text)
        if self._fh is None:
            return len(text)
        body = _ANSI.sub("", text).rstrip("\n")
        if not body.strip():
            return len(text)
        stamp = f"[{datetime.now():%H:%M:%S}]"
        with self._lock:
            # A block that spans lines gets the stamp on its own line, so a
            # panel's border stays aligned.
            if "\n" in body:
                self._fh.write(f"{stamp}\n{body}\n")
            else:
                self._fh.write(f"{stamp} {body}\n")
            self._fh.flush()
        return len(text)

    def note(self, text):
        """One extra entry, for text ZARS never prints (the typed request)."""
        if self._fh is None:
            return
        with self._lock:
            self._fh.write(f"[{datetime.now():%H:%M:%S}] {text}\n")
            self._fh.flush()

    def flush(self):
        sys.stdout.flush()
        if self._fh is not None:
            self._fh.flush()

    def isatty(self):
        return sys.stdout.isatty()

    def fileno(self):
        return sys.stdout.fileno()

    @property
    def encoding(self):
        return getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
