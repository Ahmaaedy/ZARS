"""Single-queue stdin reader so all input shares one REPL loop.

The reader thread is the only code that touches stdin. Everything else
(main loop prompts, agent confirm, macro wizard) goes through ensure_prompt/
read_line, which hand the prompt to the reader and collect the result from
the shared event queue. External events land on the same queue via put_event.
"""
import queue
import threading
import time
from collections import deque

_console = None
_reader = None
_events = queue.Queue()
_pending = deque()
_prompt_q = queue.Queue()
_busy = False  # a prompt is queued or the reader is inside input()
_input_fn = None  # test hook: replaces the reader for read_line
_orphans = 0  # prompts abandoned by read_line_until; their late line is not an answer


def init(console):
    global _console
    _console = console


def start_reader():
    global _reader
    if _reader is not None:
        return
    _reader = threading.Thread(target=_reader_loop, daemon=True, name="zars-stdin")
    _reader.start()


def reader_active():
    return _reader is not None


def put_event(kind, payload=None):
    _events.put((kind, payload))


def ensure_prompt(prompt=""):
    """Ask the reader to start an input() with this prompt (no-op if busy)."""
    global _busy
    if not _busy:
        _busy = True
        _prompt_q.put(prompt)


def get_event():
    """Next event for the main loop. Stashed events (from read_line) come first."""
    if _pending:
        return _pending.popleft()
    return _events.get()


def read_line(prompt=""):
    """Return the next line the user typed, or None on Ctrl+C/EOF."""
    global _orphans
    if _input_fn is not None:
        try:
            return _input_fn(prompt)
        except (KeyboardInterrupt, EOFError):
            return None
    if reader_active():
        ensure_prompt(prompt)
        while True:
            kind, payload = _events.get()
            if kind == "line":
                if _orphans:
                    # Late answer to a prompt read_line_until already gave up
                    # on. It belongs to the abandoned countdown, not to us.
                    _orphans -= 1
                    continue
                return payload
            if kind == "interrupt":
                return None
            _pending.append((kind, payload))
    try:
        if _console is not None:
            return _console.input(prompt)
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        return None


def read_line_until(prompt, timeout):
    """Wait up to `timeout` seconds for one line of input.

    Returns (got_line, text). got_line is False when the deadline passed with
    nothing typed -- the normal outcome of a countdown, not an error.

    A prompt abandoned here stays orphaned: the reader thread cannot be
    interrupted out of input(), so it remains blocked and will deliver a line to
    _events whenever the user next types. That line answers nothing, so it is
    counted here and discarded by the next read_line.
    """
    global _busy, _orphans

    try:
        deadline_s = max(0.0, float(timeout))
    except (TypeError, ValueError):
        deadline_s = 0.0

    if _input_fn is not None:
        # Test hook is synchronous, so run it off-thread to keep the deadline.
        box = {}

        def _call():
            try:
                box["line"] = _input_fn(prompt)
            except (KeyboardInterrupt, EOFError):
                box["line"] = None

        worker = threading.Thread(target=_call, daemon=True)
        worker.start()
        worker.join(deadline_s)
        if worker.is_alive():
            return False, None
        return True, box.get("line")

    if not reader_active():
        # Scripts and tests: nothing can arrive asynchronously, so never block.
        return False, None

    ensure_prompt(prompt)
    deadline = time.monotonic() + deadline_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _busy = False    # let the next ensure_prompt through
            _orphans += 1    # and swallow this prompt's eventual late line
            return False, None
        try:
            kind, payload = _events.get(timeout=remaining)
        except queue.Empty:
            _busy = False
            _orphans += 1
            return False, None
        if kind == "line":
            return True, payload
        if kind == "interrupt":
            return False, None
        _pending.append((kind, payload))


def ask(prompt, default=None, choices=None):
    """Prompt.ask-style helper backed by read_line.

    Empty input falls back to `default`; interrupt (Ctrl+C/EOF) returns None.
    """
    while True:
        line = read_line(prompt)
        if line is None:
            return None
        line = line.strip()
        if not line:
            return default if default is not None else ""
        if choices:
            if line.lower() in choices:
                return line.lower()
            print(f"please choose one of: {', '.join(choices)}")
            continue
        return line


def _reader_loop():
    global _busy
    while True:
        prompt = _prompt_q.get()
        try:
            if _console is not None:
                line = _console.input(prompt)
            else:
                line = input(prompt)
        except (KeyboardInterrupt, EOFError):
            _busy = False
            _events.put(("interrupt", None))
            continue
        _busy = False
        _events.put(("line", line))
