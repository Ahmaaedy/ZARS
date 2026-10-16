"""Standalone thinking-orbs overlay for `--voice` mode.

Runs as its own process. The parent (core/voice/orb_client.py) writes one
command per line to stdin; a reader thread pushes them onto a queue and a 16 ms
timer drains it. When stdin reaches EOF the overlay exits, so closing the parent
never leaves an orphan orb on the desktop.

    echo working | python animations/orb_overlay.py     # drive it by hand
    python animations/orb_overlay.py --demo             # cycle all 3 states

A bare word switches the animation state. Three prefixed verbs drive the speech
caption bubble drawn above the orb: `say {json}` begins a sentence, `pace {json}`
corrects its duration once the real audio length is known, and `clear` drops it.

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
import json
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orb_engine import FRAME_FNS, finalize_frame, resolve, smoothstep  # noqa: E402

try:
    from PyQt5.QtCore import Qt, QTimer, QPoint, QRectF
    from PyQt5.QtGui import QColor, QPen, QPainter, QPixmap, QPainterPath, QFont, QFontMetrics
    from PyQt5.QtWidgets import QApplication, QWidget
except ImportError:
    sys.exit("PyQt5 is required for the orb overlay:  pip install PyQt5\n"
             "(or set voice_orb.enabled to false in config.json)")


# ── Config (overridable by CLI for the --demo harness) ──────────────────

SIZE = 120          # orb edge in px
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

# ── Speech caption bubble ────────────────────────────────────────────────

CAPTION_MODE = 'typewriter'   # or 'instant'
CAPTION_WIDTH = 360
CAPTION_MAX_LINES = 4
CAPTION_FADE_MS = 400

BUBBLE_PAD = 12        # inner padding, px
BUBBLE_RADIUS = 12     # corner radius, px
BUBBLE_GAP = 10        # vertical space between bubble and orb, px
TAIL_W = 14            # tail base width, px
TAIL_H = 10            # tail drop below the bubble, px
SUFFIX_RESERVE = 22    # right-hand room reserved for the caret / ellipsis, px
FAINT_ALPHA = 0.35     # resting opacity once a sentence has finished speaking
FALLBACK_WORDS_PER_S = 2.6  # seeds the reveal before real timing arrives
PACE_TAU_S = 0.35      # time constant for easing toward the real duration

VERBS = ('say', 'pace', 'clear')

# Canned caption for --demo, so the bubble can be checked without a mic or a
# TTS load. Kept long enough to exercise wrapping and the caret.
DEMO_SENTENCE = ("There are three applications holding 4.2 gigabytes of your "
                 "memory right now, and Brave is the worst offender.")
DEMO_MS = 4200


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
                 transition_ms=TRANSITION_MS, settle=SETTLE, dark=None,
                 caption_mode=CAPTION_MODE, caption_width=CAPTION_WIDTH,
                 caption_max_lines=CAPTION_MAX_LINES,
                 caption_fade_ms=CAPTION_FADE_MS):
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

        self.size_px = size
        self.transition_ms = transition_ms
        self.settle = settle
        self.dark = windows_prefers_dark() if dark is None else dark

        # ── caption bubble ──
        # Fixed height on purpose. The bubble replaces itself per sentence, so a
        # height that tracked the text would resize the window mid-answer and
        # visibly shift the orb. Long sentences are elided instead.
        self.caption_mode = caption_mode if caption_mode in ('typewriter', 'instant') else 'typewriter'
        # Enabled is decided from the raw width, before it is clamped up to the
        # orb's width -- otherwise a width of 0 (--no-caption) would be clamped
        # to `size` and quietly turn the bubble back on.
        self.caption_on = int(caption_width) > 0
        self.caption_width = max(int(caption_width), size)
        self.caption_max_lines = max(1, int(caption_max_lines))
        self.caption_fade_ms = max(0, int(caption_fade_ms))

        self.font = QFont("Segoe UI", 9)
        self.fm = QFontMetrics(self.font)
        self.line_h = self.fm.lineSpacing()
        self.bubble_h = (self.caption_max_lines * self.line_h
                         + 2 * BUBBLE_PAD) if self.caption_on else 0
        # The orb is painted at the bottom-left of the widget, so widening the
        # widget for the bubble leaves the orb's screen position untouched.
        self.widget_w = max(size, self.caption_width)
        self.widget_h = self.bubble_h + (BUBBLE_GAP if self.caption_on else 0) + size
        self.setFixedSize(self.widget_w, self.widget_h)

        self._q = states_queue
        self._pending = STATES[0]
        self._leg = Leg(self._pending)
        self._prev_leg = None
        self._blend_start = 0.0
        self._t_base = 0.0
        self._last = time.monotonic()

        # caption state, advanced once per tick in _advance_caption
        self._cap_id = None
        self._cap_text = ''
        self._cap_words = []
        self._cap_truncated = False
        self._cap_at = 0.0        # monotonic time the current sentence started
        self._cap_goal_ms = 0.0   # duration we are easing the reveal toward
        self._cap_total_ms = 0.0  # duration the reveal is actually using
        self._cap_cursor = 0.0    # how many words are revealed
        self._cap_revealing = False
        self._cap_alpha = 0.0     # drawn opacity, ramps toward _cap_goal

        self._buffer = QPixmap(self.widget_w, self.widget_h)
        self._buffer.fill(QColor(0, 0, 0, 0))

        self._place(margin)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(INTERVAL_MS)

    def _place(self, margin):
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        # Use geo.x()/geo.y() so this still lands bottom-left when the primary
        # screen's origin isn't (0, 0) (some multi-monitor setups). The bottom
        # edge is pinned to size + margin, NOT widget_h + margin, so the orb
        # itself sits where it always did and only the bubble grows upward.
        self.move(geo.x() + margin,
                  geo.y() + geo.height() - self.size_px - margin
                  - (self.widget_h - self.size_px))

    # ---------- caption commands ----------

    def _inner_w(self):
        """Text width available inside the bubble's padding."""
        return self.caption_width - 2 * BUBBLE_PAD

    def _wrap(self, words, width):
        """Greedy word wrap into a list of lines.

        Done here rather than with QPainter.drawText(QRect, flags, ...) because
        the rect+flags overload of drawText silently paints nothing in PyQt5
        5.15, and only the plain (x, y, text) form works. Wrapping by hand also
        lets the fit and the draw agree exactly, so the reveal cannot reflow.
        """
        lines, cur = [], ""
        for w in words:
            trial = w if not cur else cur + " " + w
            if not cur or self.fm.horizontalAdvance(trial) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def _fit_words(self, text):
        """Longest prefix of `text` that wraps into `caption_max_lines`.

        Resolved once per sentence rather than once per frame, so the 60 fps
        reveal is a slice of a precomputed list. Returns (words, truncated).
        A right-hand reserve keeps room for the ellipsis or the caret, which are
        appended after fitting.
        """
        words = text.split()
        if not words:
            return [], False
        width = self._inner_w() - SUFFIX_RESERVE
        if len(self._wrap(words, width)) <= self.caption_max_lines:
            return words, False
        for n in range(len(words) - 1, 0, -1):
            if len(self._wrap(words[:n], width)) <= self.caption_max_lines:
                return words[:n], True
        return [], False

    def _cmd_say(self, msg):
        text = (msg.get("t") or "").strip()
        if not text:
            return
        self._cap_id = msg.get("i")
        self._cap_text = text
        self._cap_words, self._cap_truncated = self._fit_words(text)
        if not self._cap_words:
            self._cap_text = ''
            return
        self._cap_at = time.monotonic()
        est = msg.get("e")
        self._cap_goal_ms = (float(est) if est else
                             len(self._cap_words) / FALLBACK_WORDS_PER_S * 1000.0)
        self._cap_total_ms = self._cap_goal_ms
        self._cap_cursor = 0.0
        self._cap_revealing = True
        # _cap_alpha is deliberately left alone: a new sentence swaps the text
        # in place. Fading out and back in between sentences would flicker.

    def _cmd_pace(self, msg):
        ms = msg.get("ms")
        if ms and float(ms) > 0 and msg.get("i") == self._cap_id:
            self._cap_goal_ms = float(ms)

    def _cmd_clear(self, msg):
        self._cap_id = None
        self._cap_text = ''
        self._cap_words = []
        self._cap_truncated = False
        self._cap_at = 0.0
        self._cap_cursor = 0.0
        self._cap_revealing = False

    def _ramp(self, cur, goal, dt):
        """Move `cur` toward `goal`, traversing the gap in caption_fade_ms.

        A fixed-rate ramp rather than a per-frame fraction, so the fade takes the
        same wall-clock time at 144 Hz as at 60.
        """
        if self.caption_fade_ms <= 0:
            return goal
        step = dt * 1000.0 / self.caption_fade_ms
        return min(goal, cur + step) if cur < goal else max(goal, cur - step)

    def _advance_caption(self, now, dt):
        """Update reveal position and opacity. Runs before painting."""
        if not self._cap_text:
            self._cap_alpha = self._ramp(self._cap_alpha, 0.0, dt)
            return

        # Ease the duration instead of assigning it. The parent only learns a
        # sentence's true length after its audio has finished playing, so the
        # correction arrives mid-sentence; easing spreads it over ~PACE_TAU_S and
        # the reveal never jumps.
        self._cap_total_ms += (self._cap_goal_ms - self._cap_total_ms) * min(
            1.0, dt / PACE_TAU_S)
        elapsed_ms = (now - self._cap_at) * 1000.0
        progress = clamp01(elapsed_ms / max(self._cap_total_ms, 1.0))

        n = len(self._cap_words)
        if self.caption_mode == 'instant':
            self._cap_cursor = float(n)
        else:
            # Start at one word so the first word is on screen immediately rather
            # than after 1/(n-1) of the sentence.
            self._cap_cursor = 1.0 + progress * max(0, n - 1)
        self._cap_revealing = progress < 1.0
        self._cap_alpha = self._ramp(self._cap_alpha,
                                     1.0 if self._cap_revealing else FAINT_ALPHA,
                                     dt)

    def _drain(self):
        """Apply every command the parent has sent since the last tick."""
        changed = False
        while True:
            try:
                line = self._q.get_nowait()
            except queue.Empty:
                break
            verb, _, payload = line.partition(" ")
            if verb in VERBS:
                try:
                    msg = json.loads(payload) if payload.strip() else {}
                except ValueError:
                    continue
                getattr(self, f"_cmd_{verb}")(msg)
                continue
            if verb not in STATES or verb == self._leg.state:
                continue
            self._pending = verb
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
        self._advance_caption(now, dt)

        blending = self._prev_leg is not None and self.transition_ms > 0
        f = blend_factor((now - self._blend_start) * 1000.0, self.transition_ms) if blending else 1.0

        self._buffer.fill(QColor(0, 0, 0, 0))
        painter = QPainter(self._buffer)
        painter.setRenderHint(QPainter.Antialiasing, True)

        self._paint_caption(painter)

        # Everything below works in an size x size box centred on size/2, so the
        # orb is simply translated down past the bubble instead of every dot
        # coordinate being offset.
        painter.save()
        painter.translate(0, self.widget_h - self.size_px)
        if blending and f < 1.0:
            # Contract the outgoing leg while expanding the incoming one, so
            # the switch reads as a transition rather than a flat dissolve.
            self._paint_leg(painter, self._prev_leg, 1.0 - f, 1.0 - self.settle * (1.0 - f))
            self._paint_leg(painter, self._leg, f, 1.0 - self.settle * f)
        else:
            self._prev_leg = None
            self._paint_leg(painter, self._leg, 1.0, 1.0)
        painter.restore()

        painter.end()
        self.update()

    # ---------- caption painting ----------

    def _caption_visible_text(self):
        """The words revealed so far, plus a caret while still typing."""
        if not self._cap_words:
            return ''
        shown_n = max(1, min(len(self._cap_words), int(round(self._cap_cursor))))
        shown = " ".join(self._cap_words[:shown_n])
        if shown_n >= len(self._cap_words):
            if self._cap_truncated:
                shown += "…"
        elif self.caption_mode == 'typewriter':
            shown += " ▌"   # LEFT HALF BLOCK
        return shown

    def _caption_shape(self):
        """Rounded body plus a tail pointing at the orb's centre, as one path.

        united() rather than two subpaths: QPainterPath fills OddEven by
        default, so overlapping subpaths would punch a hole at the seam where the
        tail meets the body.
        """
        body = QPainterPath()
        body.addRoundedRect(QRectF(0.0, 0.0, float(self.caption_width),
                                   float(self.bubble_h)),
                            BUBBLE_RADIUS, BUBBLE_RADIUS)
        cx = self.size_px / 2.0
        tail = QPainterPath()
        tail.moveTo(cx - TAIL_W / 2.0, self.bubble_h - 1.0)
        tail.lineTo(cx + TAIL_W / 2.0, self.bubble_h - 1.0)
        tail.lineTo(cx, self.bubble_h + TAIL_H)
        tail.closeSubpath()
        return body.united(tail)

    def _paint_caption(self, painter):
        if not self.caption_on or self._cap_alpha <= 0.01 or not self._cap_text:
            return

        alpha = clamp01(self._cap_alpha)
        # Ink direction follows the orb: light ink on a dark desktop, dark ink on
        # a light one, so the bubble reads as part of the same object.
        bg_rgb, ink_rgb, bg_a = ((18, 18, 18), (240, 240, 240), 200) if self.dark \
            else ((252, 252, 252), (24, 24, 24), 215)

        shape = self._caption_shape()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(*bg_rgb, int(bg_a * alpha)))
        painter.drawPath(shape)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(*ink_rgb, int(90 * alpha)), 1))
        painter.drawPath(shape)

        # _fit_words already trimmed the sentence to what wraps here, so drawing it
        # line by line cannot overflow. drawText(x, y, ...) takes a baseline, so
        # each line is placed with the font ascent rather than its top.
        lines = self._wrap(self._caption_visible_text().split(), self._inner_w())
        painter.setFont(self.font)
        painter.setPen(QColor(*ink_rgb, int(235 * alpha)))
        block_h = len(lines) * self.line_h
        # Centre the block in the bubble, so one- and two-line sentences sit
        # visually centred rather than clinging to the top padding.
        y = (self.bubble_h - block_h) / 2.0 + self.fm.ascent()
        for line in lines[:self.caption_max_lines]:
            painter.drawText(BUBBLE_PAD, int(round(y)), line)
            y += self.line_h

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
    caption_mode = arg('--caption-mode', CAPTION_MODE)
    # --no-caption collapses the widget back to a bare size x size orb.
    caption_width = 0 if '--no-caption' in args else int(arg('--caption-width', CAPTION_WIDTH))
    caption_max_lines = int(arg('--caption-max-lines', CAPTION_MAX_LINES))
    caption_fade_ms = int(arg('--caption-fade-ms', CAPTION_FADE_MS))

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)

    states_queue = queue.Queue()
    done = threading.Event()

    overlay = OrbOverlay(states_queue, size=size, margin=MARGIN,
                         transition_ms=transition, settle=settle,
                         caption_mode=caption_mode,
                         caption_width=caption_width,
                         caption_max_lines=caption_max_lines,
                         caption_fade_ms=caption_fade_ms)

    if demo:
        # Cycle every state so the blend can be eyeballed without a mic, and
        # drive a canned sentence so the caption can be checked the same way.
        idx = 0
        sent = -1

        def cycle():
            nonlocal idx, sent
            idx = (idx + 1) % len(STATES)
            states_queue.put(STATES[idx])
            if overlay.caption_on:
                states_queue.put("say " + json.dumps(
                    {"i": sent, "t": DEMO_SENTENCE, "e": DEMO_MS}))
                states_queue.put(f"pace {json.dumps({'i': sent, 'ms': DEMO_MS})}")
                sent += 1

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