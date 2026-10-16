"""Standalone thinking-orbs overlay for `--voice` mode.

Runs as its own process. The parent (core/voice/orb_client.py) writes one state
name per line to stdin; a reader thread pushes them onto a queue and a 16 ms
timer drains it. When stdin reaches EOF the overlay exits, so closing the parent
never leaves an orphan orb on the desktop.

    echo working | python animations/orb_overlay.py     # drive it by hand
    python animations/orb_overlay.py --demo             # cycle all 3 states

Why a separate process rather than a thread: Qt needs the main thread for its
event loop, and VoiceLoop.run() blocks the main thread on the microphone. Giving
Qt the main thread and the voice loop a worker would work but couples the audio
pipeline's timing to a 60 fps render loop. A process boundary also means a crash
in here can never take the assistant down with it.

State -> animation:
    composing  idle
    listening  wake word fired, capturing a request
    working    transcribing, reasoning, or speaking
"""
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orb_engine import FRAME_FNS, finalize_frame, resolve, smoothstep  # noqa: E402

try:
    from PyQt5.QtCore import Qt, QTimer, QPoint
    from PyQt5.QtGui import QColor, QPen, QPainter, QPixmap
    from PyQt5.QtWidgets import QApplication, QWidget
except ImportError:
    sys.exit("PyQt5 is required for the orb overlay:  pip install PyQt5\n"
             "(or set voice_orb.enabled to false in config.json)")


# ── Config (overridable by CLI for the --demo harness) ──────────────────

SIZE = 120          # widget edge in px
MARGIN = 20         # px from the bottom-left corner
INTERVAL_MS = 16    # ~60 fps
DOT_THICKNESS = 2.0  # see note in _paint_leg
TRANSITION_MS = 400
SETTLE = 0.25

# Uniform multiplier on every dot radius. At the stock 64px preset tuning most
# radii land in the 0.5-1.1px sub-pixel range, so 2.0 is the smallest value
# that reads as visibly thicker. Applied to the finished frame rather than to
# the preset opts: finalize_frame clamps every dot up to rMin, so scaling the
# params would only reach the dots that clear that floor.
DOT_THICKNESS = 2.0

STATES = ('composing', 'listening', 'working')


def windows_prefers_dark():
    """True when Windows is in dark mode.

    The orb floats over an arbitrary desktop, so there is no ancestor
    data-theme to read like the React version does. The registry is the closest
    equivalent; on a missing key we assume dark, since the light-ink orb is the
    safer default against a wallpaper nobody chose.
    """
    if sys.platform != "win32":
        return True
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return not bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except OSError:
        return True


# ── Blend math (ported from the React crossfade module's blend.js) ───────

def clamp01(x):
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def blend_factor(elapsed_ms, duration_ms):
    """Blend progress: 0 = pure outgoing, 1 = pure incoming."""
    if not duration_ms > 0:
        return 1.0
    return smoothstep(clamp01(elapsed_ms / duration_ms))


def transform_frame(dots, lines, size, alpha, scale=1.0):
    """Fade and optionally scale a frame in place.

    In-place mutation is safe because every mode function allocates fresh dot
    and line lists on each call, so a frame is never shared between two legs of
    a blend. `scale` is about the centre.
    """
    if alpha < 1:
        for d in dots:
            d['a'] = d.get('a', 1) * alpha
        for l in lines:
            l['a'] = l.get('a', 1) * alpha
    if scale != 1.0:
        c = size / 2
        for d in dots:
            d['x'] = c + (d['x'] - c) * scale
            d['y'] = c + (d['y'] - c) * scale
            d['r'] *= scale
        for l in lines:
            l['x1'] = c + (l['x1'] - c) * scale
            l['y1'] = c + (l['y1'] - c) * scale
            l['x2'] = c + (l['x2'] - c) * scale
            l['y2'] = c + (l['y2'] - c) * scale
    return dots, lines


class Leg:
    """One animating mode: the state, its resolved preset, and its clock.

    Both legs in a blend read the same wall clock and multiply it by their own
    preset speed, so they stay phase-coherent while running at different rates.
    """

    __slots__ = ('state', 'mode', 'opts', 'speed')

    def __init__(self, state, size=64):
        self.state = state
        self.mode, self.speed, self.opts = resolve(state, size)

    def frame(self, size, t):
        return FRAME_FNS[self.mode](size, t, self.opts)


class OrbOverlay(QWidget):
    def __init__(self, states_queue, size=SIZE, margin=MARGIN,
                 transition_ms=TRANSITION_MS, settle=SETTLE, dark=None):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool          # keep it off the taskbar / alt-tab
            | Qt.WindowTransparentForInput  # clicks pass through to the desktop
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(size, size)

        self.size_px = size
        self.transition_ms = transition_ms
        self.settle = settle
        self.dark = windows_prefers_dark() if dark is None else dark

        self._q = states_queue
        self._pending = STATES[0]
        self._leg = Leg(self._pending)
        self._prev_leg = None
        self._blend_start = 0.0
        self._t_base = 0.0
        self._last = time.monotonic()

        self._buffer = QPixmap(size, size)
        self._buffer.fill(QColor(0, 0, 0, 0))

        self._place(margin)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(INTERVAL_MS)

    def _place(self, margin):
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        # Use geo.x()/geo.y() so this still lands bottom-left when the primary
        # screen's origin isn't (0, 0) (some multi-monitor setups).
        self.move(geo.x() + margin,
                  geo.y() + geo.height() - self.size_px - margin)

    def _drain(self):
        """Apply every state the parent has sent since the last tick."""
        changed = False
        while True:
            try:
                state = self._q.get_nowait()
            except queue.Empty:
                break
            if state not in STATES or state == self._leg.state:
                continue
            self._pending = state
            changed = True
        if not changed:
            return
        # A re-switch mid-blend promotes the incoming leg to outgoing, so rapid
        # state changes chain instead of snapping back.
        self._prev_leg = self._leg
        self._blend_start = time.monotonic()
        self._leg = Leg(self._pending)

    def _tick(self):
        now = time.monotonic()
        # Clamp dt so a debugger pause or a stalled timer doesn't teleport the
        # animation forward on the next frame.
        dt = min(now - self._last, 0.1)
        self._last = now
        self._t_base += dt

        self._drain()

        blending = self._prev_leg is not None and self.transition_ms > 0
        f = blend_factor((now - self._blend_start) * 1000.0, self.transition_ms) if blending else 1.0

        self._buffer.fill(QColor(0, 0, 0, 0))
        painter = QPainter(self._buffer)
        painter.setRenderHint(QPainter.Antialiasing, True)

        if blending and f < 1.0:
            # Contract the outgoing leg while expanding the incoming one, so
            # the switch reads as a transition rather than a flat dissolve.
            self._paint_leg(painter, self._prev_leg, 1.0 - f, 1.0 - self.settle * (1.0 - f))
            self._paint_leg(painter, self._leg, f, 1.0 - self.settle * f)
        else:
            self._prev_leg = None
            self._paint_leg(painter, self._leg, 1.0, 1.0)

        painter.end()
        self.update()

    def _paint_leg(self, painter, leg, alpha, scale):
        dots, lines = leg.frame(self.size_px, self._t_base * leg.speed)
        if DOT_THICKNESS != 1.0:
            for d in dots:
                d['r'] *= DOT_THICKNESS
        transform_frame(dots, lines, self.size_px, alpha, scale)
        # finalize_frame is always re-run, even though the mode function already
        # produced a finalized frame. It is idempotent apart from the radius
        # clamp, and its a < 0.02 cull is what clears the fading tail -- without
        # it a blend leaves ghost speckle behind.
        dots, lines = finalize_frame(dots, lines, leg.opts['rMin'])
        self._paint_lines(painter, lines)
        self._paint_dots(painter, dots)

    def _paint_dots(self, painter, dots):
        for d in dots:
            a = d.get('a', 1)
            w = min(1.0, max(0.0, d['white']))
            v = round((1 - w) * 255 if self.dark else w * 255)
            painter.setBrush(QColor(v, v, v, int(clamp01(a) * 255)))
            painter.setPen(Qt.NoPen)
            # Round rather than truncate, and keep a 1px floor: several modes
            # produce sub-pixel radii after the preset scaling, and int()
            # truncation would round those down to 0, which draws nothing.
            r = max(1, round(d['r']))
            painter.drawEllipse(QPoint(round(d['x']), round(d['y'])), r, r)

    def _paint_lines(self, painter, lines):
        for l in lines:
            a = l.get('a', 1)
            w = min(1.0, max(0.0, l['white']))
            v = round((1 - w) * 255 if self.dark else w * 255)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(v, v, v, int(clamp01(a) * 255)),
                                max(1, round(l.get('w', 1)))))
            painter.drawLine(int(l['x1']), int(l['y1']), int(l['x2']), int(l['y2']))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._buffer)
        painter.end()


def _stdin_reader(q, done):
    """Block on stdin, pushing state names. EOF marks `done`."""
    try:
        for line in sys.stdin:
            state = line.strip()
            if state:
                q.put(state)
    except (ValueError, OSError):
        pass
    done.set()


def main():
    args = sys.argv[1:]
    demo = '--demo' in args

    def arg(name, default):
        if name in args:
            return args[args.index(name) + 1]
        return default

    size = int(arg('--size', SIZE))
    transition = int(arg('--transition', TRANSITION_MS))
    settle = float(arg('--settle', SETTLE))

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)

    states_queue = queue.Queue()
    done = threading.Event()

    overlay = OrbOverlay(states_queue, size=size, margin=MARGIN,
                         transition_ms=transition, settle=settle)

    if demo:
        # Cycle every state so the blend can be eyeballed without a mic.
        idx = 0

        def cycle():
            nonlocal idx
            idx = (idx + 1) % len(STATES)
            states_queue.put(STATES[idx])

        demo_timer = QTimer(overlay)
        demo_timer.timeout.connect(cycle)
        demo_timer.start(2500)
    else:
        threading.Thread(target=_stdin_reader, args=(states_queue, done),
                         daemon=True).start()
        # QApplication.quit() is not thread-safe, so poll the EOF flag from a
        # timer on the Qt thread. Without this the overlay outlives its parent
        # and sits on the desktop with nothing driving it.
        watchdog = QTimer(overlay)
        watchdog.timeout.connect(lambda: app.quit() if done.is_set() else None)
        watchdog.start(250)

    overlay.show()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())