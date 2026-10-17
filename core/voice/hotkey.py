"""Global hotkey that wakes ZARS without saying the wake word.

RegisterHotKey rather than a low-level keyboard hook: it needs no admin rights,
sees only the one combination instead of every keystroke, and Windows gives it
back on process exit rather than leaving the key dead. It also has to be
released explicitly in stop() -- a hotkey the process forgot stays bound
system-wide, and the next run would fail to register it.

Registration failing is never fatal. Another app may already hold the key, or
the window station may not allow it; in that case voice mode runs exactly as
before and just keeps using the wake word.

This pywin32 build ships no VK_OEM_* constants, so the table is spelled out
rather than read from win32con. Virtual-key codes are not ASCII for the
punctuation row -- ` is 0xC0, not 96 -- which is why single characters cannot
simply go through ord().
"""
import threading

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

HOTKEY_ID = 0xB0A7  # arbitrary but fixed; one hotkey per process

_MODIFIERS = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
    "super": MOD_WIN,
}

_VK_BY_NAME = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "spacebar": 0x20,
    "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "`": 0xC0, "backquote": 0xC0, "grave": 0xC0, "tilde": 0xC0,
    "-": 0xBD, "minus": 0xBD, "=": 0xBB, "plus": 0xBB,
    "[": 0xDB, "{": 0xDB, "lbracket": 0xDB,
    "]": 0xDD, "}": 0xDD, "rbracket": 0xDD,
    "\\": 0xDC, "|": 0xDC, "backslash": 0xDC,
    ";": 0xBA, ":": 0xBA, "semicolon": 0xBA,
    "'": 0xDE, '"': 0xDE, "quote": 0xDE,
    ",": 0xBC, "<": 0xBC, "comma": 0xBC,
    ".": 0xBE, ">": 0xBE, "period": 0xBE,
    "/": 0xBF, "?": 0xBF, "slash": 0xBF,
}
for _n in range(1, 13):
    _VK_BY_NAME["f%d" % _n] = 0x6F + _n


class HotkeyError(RuntimeError):
    pass


def parse(spec):
    """'alt+`' -> (MOD_ALT | MOD_NOREPEAT, 0xC0, 'Alt+`')."""
    if spec is None:
        raise HotkeyError("no hotkey configured")
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        raise HotkeyError("no hotkey configured")

    mods = 0
    keys = []
    for part in parts:
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
        else:
            keys.append(part)
    if not keys:
        raise HotkeyError("hotkey %r has modifiers but no key" % spec)
    if len(keys) > 1:
        raise HotkeyError("hotkey %r names more than one key" % spec)

    name = keys[0]
    vk = _VK_BY_NAME.get(name)
    if vk is None:
        # Letters and digits share their virtual-key code with ASCII.
        if len(name) == 1 and ("a" <= name <= "z" or "0" <= name <= "9"):
            vk = ord(name)
        else:
            raise HotkeyError("unknown key %r in hotkey %r" % (name, spec))
    if not mods:
        raise HotkeyError(
            "hotkey %r has no modifier; a bare key would swallow normal typing" % spec)
    return mods | MOD_NOREPEAT, vk, _label(mods, name)


def _label(mods, key_name):
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    pretty = {"backquote": "`", "space": "Space", "spacebar": "Space",
              "enter": "Enter", "return": "Enter",
              "esc": "Esc", "escape": "Esc", "tab": "Tab", "backspace": "Backspace",
              "delete": "Del", "del": "Del", "insert": "Ins", "ins": "Ins",
              "pageup": "PgUp", "pagedown": "PgDn",
              "up": "Up", "down": "Down", "left": "Left", "right": "Right"}
    parts.append(pretty.get(key_name, key_name.upper() if len(key_name) == 1 else key_name))
    return "+".join(parts)


class GlobalHotkey:
    """Fires `fired` on a background thread while the hotkey is pressed."""

    def __init__(self, spec):
        self.spec = spec
        self.label = None
        self.fired = threading.Event()
        self._hwnd = None
        self._thread = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error = None
        self._wndproc = None
        self._registered = False
        try:
            self._mods, self._vk, self.label = parse(spec)
        except HotkeyError as e:
            raise HotkeyError(str(e)) from e

    @property
    def active(self):
        return self._registered

    def start(self):
        """Register and begin pumping. Returns True on success.

        Everything -- window, registration, message pump -- happens on the
        background thread. RegisterHotKey posts WM_HOTKEY to the queue of the
        thread that created the window, so creating it here and pumping it
        elsewhere means the hotkey silently never arrives.
        """
        if self._registered:
            return True
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="zars-hotkey")
        self._thread.start()
        if not self._ready.wait(5.0):
            raise HotkeyError("timed out registering %s" % self.label)
        if self._error:
            raise HotkeyError(self._error)
        return self._registered

    def _run(self):
        import win32api
        import win32con
        import win32gui

        fired = self.fired

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_HOTKEY and wparam == HOTKEY_ID:
                fired.set()
            return 0

        try:
            # Held on the instance: pywin32 has to be able to call this later.
            self._wndproc = wndproc
            wc = win32gui.WNDCLASS()
            wc.style = 0
            wc.lpfnWndProc = wndproc
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = "ZarsWakeHotkey"
            try:
                win32gui.RegisterClass(wc)
            except Exception:
                # Already registered by an earlier start() in this process,
                # which is fine: the class outlives its window.
                pass
            # HWND_MESSAGE: message-only window, so nothing is ever drawn.
            self._hwnd = win32gui.CreateWindow(
                wc.lpszClassName, "ZarsWakeHotkey", 0, 0, 0, 0, 0,
                win32con.HWND_MESSAGE, 0, wc.hInstance, None,
            )
            win32gui.RegisterHotKey(self._hwnd, HOTKEY_ID, self._mods, self._vk)
            self._registered = True
        except Exception as e:
            self._error = "Windows refused %s: %s" % (self.label, e)
            self._ready.set()
            self._teardown()
            return

        self._ready.set()
        while not self._stop.is_set():
            try:
                win32gui.PumpWaitingMessages()
            except Exception:
                pass
            self._stop.wait(0.02)
        self._teardown()

    def _teardown(self):
        if self._hwnd is not None:
            try:
                import win32con
                import win32gui
                if self._registered:
                    win32gui.UnregisterHotKey(self._hwnd, HOTKEY_ID)
                win32gui.DestroyWindow(self._hwnd)
            except Exception:
                pass
            self._hwnd = None
        self._registered = False
        self.fired.clear()

    def take(self):
        """True once if the hotkey was pressed since the last call."""
        if self.fired.is_set():
            self.fired.clear()
            return True
        return False

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._hwnd is not None:
            # The pump thread never came up; clean up on this one.
            self._teardown()