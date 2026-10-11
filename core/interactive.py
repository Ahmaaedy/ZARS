"""Single-queue stdin reader so all input shares one REPL loop.

The reader thread is the only code that touches stdin. Everything else
(main loop prompts, agent confirm, macro wizard) goes through ensure_prompt/
read_line, which hand the prompt to the reader and collect the result from
the shared event queue. External events land on the same queue via put_event.
"""
import queue
import threading
from collections import deque

_console = None
_reader = None
_events = queue.Queue()
_pending = deque()
_prompt_q = queue.Queue()
_busy = False  # a prompt is queued or the reader is inside input()
_input_fn = None  # test hook: replaces the reader for read_line


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
