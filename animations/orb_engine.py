"""Thinking-orbs geometry, ported from thinking-orbs engine.es.js (v0.3.1).

Pure math: no Qt, no canvas, no audio. Each mode function returns a finished
frame -- dots already z-sorted into draw order with radii clamped -- so the
renderer only has to paint the list and never re-derive anything.

The numbers are transcribed 1:1 from the shipped engine so the Python overlay
matches the React `<ThinkingOrb>` by construction rather than by eye. If you
change a constant here, change it in the engine and re-check the port.

Only the parts the voice overlay needs are kept. The microphone-driven volume
reactivity of the original demo overlay is deliberately absent: the voice loop
already owns the mic, and opening a second capture stream risks the wake word.
"""
import math


# ── Math utils (ported 1:1 from engine.es.js) ──────────────────────────

def lerp(a, b, f):
    return a + (b - a) * f


def frac(n):
    return n - math.floor(n)


def smoothstep(x):
    """Cubic smoothstep, clamped. Identical to the engine's easing primitive,
    so the crossfade feels native to the `morph` mode that already uses it."""
    p = 0.0 if x < 0 else (1.0 if x > 1 else x)
    return p * p * (3 - 2 * p)


def js_round(x):
    """Math.round(x): halves go away from zero.

    Not the same as Python's round(), which is banker's rounding -- round(2.5)
    is 2 in Python but 3 in JS. The engine counts with Math.round, so
    ribbon's `lanes = 5 * sqrt(0.25) = 2.5` lands on 3 bands, not 2. Getting
    this wrong silently drops ~180 dots and changes the animation.
    """
    return math.floor(x + 0.5)


def _hash(i, s):
    t = math.sin(i * 12.9898 + s * 78.233) * 43758.5453
    return t - math.floor(t)


def simplex_noise(x, y):
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    fx = fx * fx * (3 - 2 * fx)
    fy = fy * fy * (3 - 2 * fy)
    a = _hash(ix, iy)
    b = _hash(ix + 1, iy)
    c = _hash(ix, iy + 1)
    d = _hash(ix + 1, iy + 1)
    return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy


def angle_diff(a, b):
    return math.atan2(math.sin(a - b), math.cos(a - b))


def fib_sphere(i, n):
    golden = math.pi * (3 - math.sqrt(5))
    y = 1 - 2 * (i + 0.5) / n
    r = math.sqrt(max(0, 1 - y * y))
    a = i * golden
    return [r * math.cos(a), y, r * math.sin(a)]


def radius_scale(size, pow=0.6):
    """Dot radii were tuned for a 300pt frame; sub-linear scaling keeps small
    spinners legible. Lower pow = radii shrink less with size."""
    return (size / 300) ** pow


def make_proj(yaw, tilt, cx, cy, scale):
    """Shared spin + tilt + orthographic projection."""
    st, ct = math.sin(tilt), math.cos(tilt)
    sy, cy_ = math.sin(yaw), math.cos(yaw)

    def proj(x, y, z):
        e = x * cy_ + z * sy
        l = -x * sy + z * cy_
        R = y * ct - l * st
        w = y * st + l * ct
        return [cx + e * scale, cy - R * scale, w]

    return proj


# ── Frame finalization (depth sort + alpha filter) ──────────────────────

def finalize_frame(dots, lines, r_min=0.3):
    """Drop invisible marks, clamp radii to the mode's floor, and z-sort
    far->near into draw order.

    Re-running this on an already-finalized frame is idempotent apart from the
    radius clamp, which is what makes it safe to call again after the crossfade
    has scaled alphas down -- the `a < 0.02` cull is exactly what clears the
    fading tail instead of leaving ghost speckle.
    """
    fd = []
    for d in dots:
        a = d.get('a', 1)
        if a < 0.02:
            continue
        d['r'] = max(r_min, d['r'])
        fd.append(d)
    fd.sort(key=lambda d: d['z'])
    fl = [l for l in lines if l.get('a', 1) >= 0.02]
    return fd, fl


# ── Mode functions (ported 1:1 from engine.es.js) ──────────────────────

def _orbits(n, t, opts):
    cx = cy = n / 2
    rad = n / 2 * 0.82
    proj = make_proj(t * 0.12, 0.3, cx, cy, 1)
    rs = radius_scale(n, opts.get('rsPow', 0.6))
    orbit_n = opts.get('orbitN', 12)
    ghost_n = opts.get('ghostN', 40)
    particles = opts.get('particles', 3)
    dots = []

    for i in range(orbit_n):
        r1 = _hash(i, 1.7)
        r2 = _hash(i, 5.2)
        r3 = _hash(i, 8.9)
        rad_o = rad * (0.45 + 0.52 * r1)
        u = r1 * 2 * math.pi
        y_a = math.acos(2 * r2 - 1)
        bx = math.sin(y_a) * math.cos(u)
        by = math.cos(y_a)
        bz = math.sin(y_a) * math.sin(u)

        nx, nz = -by, bx
        d = 0
        v_len = max(1e-6, math.sqrt(nx * nx + nz * nz))
        nx /= v_len
        nz /= v_len

        kx = by * d - bz * nz
        ky = bz * nx - bx * d
        kz = bx * nz - by * nx
        O = (0.25 + 0.55 * r3) * (1 if r3 > 0.5 else -1)

        for j in range(ghost_n):
            ang = j / ghost_n * 2 * math.pi
            gx = nx * math.cos(ang) + kx * math.sin(ang)
            gy = nz * math.cos(ang) + ky * math.sin(ang)
            gz = d * math.cos(ang) + kz * math.sin(ang)
            px, py, pz = proj(gx * rad_o, gy * rad_o, gz * rad_o)
            depth = (pz / rad_o + 1) / 2
            dots.append({'x': px, 'y': py, 'z': pz,
                         'r': opts.get('ghostR', 0.9) * rs,
                         'white': 0.72,
                         'a': opts.get('ghostA', 0.5) * (0.4 + 0.6 * depth)})

        for j in range(particles):
            ang = t * O + j / particles * 2 * math.pi + r2 * 6
            gx = nx * math.cos(ang) + kx * math.sin(ang)
            gy = nz * math.cos(ang) + ky * math.sin(ang)
            gz = d * math.cos(ang) + kz * math.sin(ang)
            px, py, pz = proj(gx * rad_o, gy * rad_o, gz * rad_o)
            depth = (pz / rad_o + 1) / 2
            dots.append({'x': px, 'y': py, 'z': pz,
                         'r': (opts.get('partR', 1.2) + opts.get('partRDepth', 1.6) * depth) * rs,
                         'white': 0.3 - 0.22 * depth})

    return finalize_frame(dots, [], opts.get('rMin', 0.3))


def _wave(n, t, opts):
    """`listening` -- a waveform rolls through latitude rings."""
    cx = cy = n / 2
    rad = n / 2 * 0.874
    proj = make_proj(t * 0.18, 0.38, cx, cy, 1)
    rs = radius_scale(n, opts.get('rsPow', 0.6))
    dots = []
    rings = opts.get('rings', 15)
    lon_d = opts.get('lonDensity', 40)

    for p in range(rings + 1):
        lat = -math.pi / 2 + p / rings * math.pi
        cos_lat = math.cos(lat)
        sin_lat = math.sin(lat)
        wave_v = 0.62 * math.sin(t * 2.1 - p * 0.52) + 0.38 * math.sin(t * 1.27 + p * 0.83)
        ring_r = rad * (0.88 + 0.105 * wave_v)
        b = max(1, js_round(abs(cos_lat) * lon_d))
        for f in range(b):
            lon = f / b * 2 * math.pi
            px, py, pz = proj(cos_lat * math.cos(lon) * ring_r, sin_lat * ring_r,
                              cos_lat * math.sin(lon) * ring_r)
            depth = (pz / rad + 1) / 2
            dots.append({'x': px, 'y': py, 'z': pz,
                         'r': (opts.get('rBase', 0.6) + opts.get('rDepth', 1.7) * depth)
                         * (1 + 0.4 * max(0, wave_v)) * rs,
                         'white': 0.66 - 0.56 * depth - 0.1 * max(0, wave_v)})

    return finalize_frame(dots, [], opts.get('rMin', 0.3))


def _ribbon(n, t, opts):
    """`composing` -- an undulating multi-band sash."""
    cx = cy = n / 2
    rad = n / 2 * 0.78
    spin = opts.get('spin', 1)
    tilt = 0.3
    proj = make_proj(t * 0.1 * spin, tilt, cx, cy, 1)
    rs = radius_scale(n, opts.get('rsPow', 0.6))
    face_on = opts.get('faceOn', 0)
    ghost_n = opts.get('ghostN', 150)
    dots = []

    for i in range(ghost_n):
        bp = fib_sphere(i, ghost_n)
        px, py, pz = proj(bp[0] * rad, bp[1] * rad, bp[2] * rad)
        depth = (pz / rad + 1) / 2
        dots.append({'x': px, 'y': py, 'z': pz, 'r': 0.8 * rs,
                     'white': 0.78, 'a': 0.1 + 0.22 * depth})

    # Camera basis (JS variables R, w, i, u, y, b, f, P, x)
    e = t * 0.1 * spin
    l_val = -tilt if face_on else 0.55 + 0.3 * math.sin(t * 0.18) * spin
    R = math.cos(e)
    w_v = 0
    i_v = math.sin(e)
    u_v = -i_v * math.sin(l_val)
    y_v = math.cos(l_val)
    b_v = R * math.sin(l_val)
    f_v = w_v * b_v - i_v * y_v
    P_v = i_v * u_v - R * b_v
    x_v = R * y_v - w_v * u_v

    wob = 0.23 * opts.get('wobMul', 1)
    d_val = rad / (1 + 0.85 * wob) if face_on else rad
    lanes = opts.get('lanes', 5)
    segs = opts.get('segs', 88)
    band_n = max(1, js_round(lanes * opts.get('bandMul', 1)))

    for z_idx in range(band_n):
        off = (z_idx - (band_n - 1) / 2) * 0.075
        edge = abs(z_idx - (band_n - 1) / 2) / max(1, (band_n - 1) / 2)
        for si in range(segs):
            ang = si / segs * 2 * math.pi
            wave = (0.16 * math.sin(ang * 3 - t * 1.7 + z_idx * 0.22)
                    + 0.07 * math.sin(ang * 5 + t * 1.1)) * opts.get('wobMul', 1)
            C = off if face_on else off + wave
            cos_a = math.cos(ang)
            sin_a = math.sin(ang)
            qx = R * cos_a + u_v * sin_a + f_v * C
            qy = w_v * cos_a + y_v * sin_a + P_v * C
            qz = i_v * cos_a + b_v * sin_a + x_v * C
            wl = math.sqrt(qx * qx + qy * qy + qz * qz)
            if wl > 0:
                qx, qy, qz = qx / wl * d_val, qy / wl * d_val, qz / wl * d_val
            px, py, pz = proj(qx, qy, qz)
            depth = (pz / rad + 1) / 2
            dots.append({'x': px, 'y': py, 'z': pz,
                         'r': (opts.get('rBase', 1.1) + opts.get('rDepth', 1.7) * depth)
                         * (1 - 0.25 * edge) * rs,
                         'white': 0.52 - 0.44 * depth + 0.18 * edge,
                         'a': 0.4 + 0.6 * depth})

    return finalize_frame(dots, [], opts.get('rMin', 0.3))


FRAME_FNS = {
    'orbits': _orbits,
    'wave': _wave,
    'ribbon': _ribbon,
}


# ── Presets (exact values from engine.es.js, 64px tier) ─────────────────

STATE_TO_MODE = {
    'working': 'orbits',
    'listening': 'wave',
    'composing': 'ribbon',
}

MODE_DEFAULTS = {
    'orbits': {'orbitN': 12, 'ghostN': 40, 'ghostR': 0.9, 'ghostA': 0.5,
               'particles': 3, 'partR': 1.2, 'partRDepth': 1.6,
               'rsPow': 0.6, 'rMin': 0.3},
    'wave': {'rings': 15, 'lonDensity': 40, 'rBase': 0.6, 'rDepth': 1.7,
             'rsPow': 0.6, 'rMin': 0.3},
    'ribbon': {'lanes': 5, 'segs': 88, 'ghostN': 150, 'rBase': 1.1,
               'rDepth': 1.7, 'rsPow': 0.6, 'rMin': 0.3},
}

PRESETS_64 = {
    'orbits': {'speed': 1.885, 'count': 1, 'size': 1},
    'wave': {'speed': 4.388, 'count': 0.341, 'size': 1},
    'ribbon': {'speed': 2.34, 'count': 0.25, 'size': 0.85,
               'extra': {'spin': 0, 'bandMul': 3.9, 'wobMul': 1}},
}

_COUNT_PARAMS = {'rings', 'lonDensity', 'lanes', 'segs'}
_SIZE_PARAMS = {'rBase', 'rDepth', 'ghostR', 'partR', 'partRDepth'}
_COUNT_SCALE = {'ghostN'}

# The engine scales count params in *pairs*, atomically, because a pair like
# (rings, lonDensity) is one design decision: halving one without the other
# changes the shape of the lattice. A `done` set stops a shared member from
# being scaled twice. Mirrors the `[[k, k], ...]` table in engine.es.js.
_COUNT_PAIRS = [('rings', 'lonDensity'), ('lanes', 'segs')]


def _scale_by_count(opts, count):
    """Scale dot *counts* by `count`.

    Deliberately does NOT touch the size params (rBase, rDepth, ghostR, ...).
    In the engine those are scaled only by the size multiplier -- St() leaves
    them alone. Scaling them by count too silently thickens the dots on every
    mode whose count is not 1.
    """
    out = dict(opts)
    done = set()
    s = math.sqrt(count)
    for a, b in _COUNT_PAIRS:
        if a in out and b in out and a not in done and b not in done:
            out[a] = max(2, js_round(out[a] * s))
            out[b] = max(2, js_round(out[b] * s))
            done.add(a)
            done.add(b)
    for p in _COUNT_SCALE:
        if p in out and p not in done:
            out[p] = max(1, js_round(out[p] * count))
            done.add(p)
    return out


def _scale_by_size(opts, sz):
    out = dict(opts)
    for p in _SIZE_PARAMS:
        if p in out:
            out[p] = out[p] * sz
    return out


def resolve(state, size=64):
    """Resolve a (state, size) pair to (mode, preset_speed, fully-scaled opts).

    Only the 64px preset tier is ported. The upstream engine ships exactly two
    tiers (64 and 20); anything else has no entry and would throw. We render at
    an arbitrary widget size but keep the 64px dot counts, which is what the
    original overlay did -- the dot radius scales sub-linearly via
    radius_scale(), so a larger window reads as a larger orb rather than a
    sparse one.
    """
    if state not in STATE_TO_MODE:
        raise KeyError(f"unknown orb state {state!r}; expected one of {sorted(STATE_TO_MODE)}")
    mode = STATE_TO_MODE[state]
    defaults = dict(MODE_DEFAULTS[mode])
    preset = PRESETS_64.get(mode, {})
    if preset.get('count', 1) != 1:
        defaults = _scale_by_count(defaults, preset['count'])
    if preset.get('size', 1) != 1:
        defaults = _scale_by_size(defaults, preset['size'])
    if 'extra' in preset:
        defaults.update(preset['extra'])
    return mode, preset.get('speed', 1), defaults