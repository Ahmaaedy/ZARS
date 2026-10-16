"""Parent-side handle on the orb overlay process.

Spawns `animations/orb_overlay.py` and feeds it one command per line. Every
interaction with the child is best-effort: a dead, crashed, or never-started
overlay must never take down voice mode. The failure mode is "no orb on screen",
not "the assistant stopped listening".

Line protocol (one command per line, newline-terminated):

    working                     bare word  -> switch animation state
    say {"i":0,"t":"Hi."}       begin sentence i; "t" is its text
    pace {"i":0,"ms":1234}      sentence i really took ms of audio
    clear                      drop the caption

Payloads are JSON so a sentence can contain anything, newlines included --
json.dumps escapes them, which keeps each command on exactly one line. The
overlay splits on the first space, so bare state names keep working unchanged.
"""
import json
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

# Fallback speaking rate, used to seed the caption's reveal before the real
# sentence duration is known. Kokoro lands near this for normal prose.
FALLBACK_WORDS_PER_S = 2.6

ORB_SCRIPT = Path(__file__).resolve().parents[2] / "animations" / "orb_overlay.py"


class OrbClient:
    """Owns the overlay subprocess. Safe to call `state()` at any time."""

    def __init__(self, enabled=True, size=120, transition_ms=400, settle=0.25, log=None,
                 caption=True, caption_mode="typewriter", caption_width=360,
                 caption_max_lines=4, caption_fade_ms=400):
        self.proc = None
        self._log = log
        self._warned = False
        self.enabled = enabled
        self.size = size
        self.transition_ms = transition_ms
        self.settle = settle
        self.caption = caption
        self.caption_mode = caption_mode
        self.caption_width = caption_width
        self.caption_max_lines = caption_max_lines
        self.caption_fade_ms = caption_fade_ms
        # Sentence index currently being spoken. The parent speaks one thing at
        # a time, so a plain counter is enough to pair say with pace.
        self._sentence = 0

    def start(self):
        if not self.enabled or self.proc is not None:
            return self
        if not ORB_SCRIPT.exists():
            self._warn_once(f"orb overlay missing at {ORB_SCRIPT}")
            return self
        argv = [sys.executable, str(ORB_SCRIPT),
                "--size", str(self.size),
                "--transition", str(self.transition_ms),
                "--settle", str(self.settle)]
        if self.caption:
            argv += ["--caption-width", str(self.caption_width),
                     "--caption-max-lines", str(self.caption_max_lines),
                     "--caption-fade-ms", str(self.caption_fade_ms),
                     "--caption-mode", self.caption_mode]
        try:
            self.proc = subprocess.Popen(
                argv,
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
        orb = STATE_TO_ORB.get(name)
        if orb is not None:
            self._write(orb)

    def say(self, text):
        """Start a new caption sentence. Returns the index used, or None."""
        text = (text or "").strip()
        if not text or not self._live():
            return None
        est_ms = int(len(text.split()) / FALLBACK_WORDS_PER_S * 1000)
        idx = self._sentence
        self._sentence += 1
        self._write("say " + json.dumps({"i": idx, "t": text, "e": est_ms}))
        return idx

    def pace(self, idx, ms):
        """Correct a sentence's pacing once its real audio duration is known."""
        if idx is None or not self._live():
            return
        self._write("pace " + json.dumps({"i": idx, "ms": int(round(ms))}))

    def clear(self):
        """Drop the caption."""
        if self._live():
            self._write("clear")

    def _live(self):
        """True when a caption sent right now would actually reach the child."""
        return (self.enabled and self.caption
                and self.proc is not None and self.proc.poll() is None)

    def _write(self, line):
        """One command to the child. Never raises.

        Everything funnels through here so a dead overlay is handled in exactly
        one place, no matter which command hit the broken pipe.
        """
        if not self.enabled or self.proc is None or self.proc.poll() is not None:
            return
        try:
            self.proc.stdin.write(line.encode("utf-8") + b"\n")
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