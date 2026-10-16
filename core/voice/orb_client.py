"""Parent-side handle on the orb overlay process.

Spawns `animations/orb_overlay.py` and feeds it one state name per line. Every
interaction with the child is best-effort: a dead, crashed, or never-started
overlay must never take down voice mode. The failure mode is "no orb on screen",
not "the assistant stopped listening".
"""
import os
import subprocess
import sys
from pathlib import Path

# Which animation each orchestrator state shows.
STATE_TO_ORB = {
    "IDLE": "composing",
    "LISTENING": "listening",
    # Everything between hearing a prompt and waiting for the next one.
    "TRANSCRIBING": "working",
    "THINKING": "working",
    "SPEAKING": "working",
}

ORB_SCRIPT = Path(__file__).resolve().parents[2] / "animations" / "orb_overlay.py"


class OrbClient:
    """Owns the overlay subprocess. Safe to call `state()` at any time."""

    def __init__(self, enabled=True, size=120, transition_ms=400, settle=0.25, log=None):
        self.proc = None
        self._log = log
        self._warned = False
        self.enabled = enabled
        self.size = size
        self.transition_ms = transition_ms
        self.settle = settle

    def start(self):
        if not self.enabled or self.proc is not None:
            return self
        if not ORB_SCRIPT.exists():
            self._warn_once(f"orb overlay missing at {ORB_SCRIPT}")
            return self
        try:
            self.proc = subprocess.Popen(
                [sys.executable, str(ORB_SCRIPT),
                 "--size", str(self.size),
                 "--transition", str(self.transition_ms),
                 "--settle", str(self.settle)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Own process group so Ctrl+C in the voice REPL does not race
                # the child's shutdown, and so we can signal it as a unit.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            self._warn_once(f"orb overlay failed to start: {e}")
            self.proc = None
        return self

    def state(self, name):
        """Show an orchestrator state. No-op if the orb is disabled or gone."""
        if not self.enabled or self.proc is None or self.proc.poll() is not None:
            return
        orb = STATE_TO_ORB.get(name)
        if orb is None:
            return
        try:
            self.proc.stdin.write(orb.encode("ascii") + b"\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            # The overlay died under us. Drop it quietly; voice carries on.
            self._warn_once("orb overlay exited unexpectedly -- continuing without it")
            self.proc = None

    def stop(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()   # EOF makes the child quit on its own
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _warn_once(self, msg):
        if self._warned:
            return
        self._warned = True
        if self._log:
            self._log(f"[dim]orb: {msg}[/dim]")

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


# A do-nothing stand-in so callers can hold one unconditionally.
NULL_ORB = OrbClient(enabled=False)