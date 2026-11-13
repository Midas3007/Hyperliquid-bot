"""Shared constants: format, palette (from STORYBOARD.md), easing and small numeric helpers."""
from __future__ import annotations

import os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC_IMAGE = os.path.join(ROOT, "assets", "hevi.webp")
OUT_DIR = os.path.join(HERE, "out")
CACHE_DIR = os.path.join(OUT_DIR, "cache")

W, H = 1920, 1080
FPS = 60
NFRAMES = 3600
BEAT = 0.5


def hexc(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255.0


# palette (storyboard)
BG = hexc("04080A")
TEAL = hexc("2FD3B0")
DEEP = hexc("0E5E55")
MINT = hexc("7DFFD9")
CREAM = hexc("FFF6E0")
GREEN = hexc("3DFFA8")
RED = hexc("FF4F6D")
BLUE = hexc("6EA8FF")
DIM = hexc("3E6E68")
WHITE = np.array([1.0, 1.0, 1.0], np.float32)

LUMW = np.array([0.2126, 0.7152, 0.0722], np.float32)


# ------------------------------------------------------------------------------------------------- easing

def clamp01(x):
    return np.clip(x, 0.0, 1.0)


def prog(t, t0, t1):
    """Normalised progress of t through [t0, t1], clamped."""
    return clamp01((t - t0) / (t1 - t0))


def smoothstep(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_in_out_cubic(x):
    x = clamp01(x)
    return np.where(x < 0.5, 4 * x ** 3, 1 - (-2 * x + 2) ** 3 / 2)


def ease_out_cubic(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_in_cubic(x):
    x = clamp01(x)
    return x ** 3


def ease_out_expo(x):
    x = clamp01(x)
    return np.where(x >= 1, 1.0, 1 - 2.0 ** (-10 * x))


def ease_in_out_expo(x):
    x = clamp01(x)
    return np.where(x <= 0, 0.0, np.where(x >= 1, 1.0,
                    np.where(x < 0.5, 2.0 ** (20 * x - 10) / 2, (2 - 2.0 ** (-20 * x + 10)) / 2)))


def ease_out_quint(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 5


def ease_in_out_sine(x):
    x = clamp01(x)
    return -(np.cos(np.pi * x) - 1) / 2


def fade(t, t_in0, t_in1, t_out0=None, t_out1=None, ease=smoothstep):
    """Envelope: eases in over [t_in0, t_in1] and (optionally) out over [t_out0, t_out1]."""
    a = ease(prog(t, t_in0, t_in1)) if t_in1 > t_in0 else float(t >= t_in0)
    if t_out0 is not None:
        a = a * (1 - ease(prog(t, t_out0, t_out1)))
    return float(a)


def beat_pulse(t, decay=0.16, period=BEAT, offset=0.0):
    """Exponential pulse that peaks on every beat."""
    ph = (t - offset) % period
    return float(np.exp(-ph / decay))


def damped_overshoot(u, amp=0.3, freq=9.0, damp=10.0):
    """1 + damped cosine: starts at 1+amp, settles to 1 with one gentle undershoot."""
    u = max(0.0, float(u))
    return 1.0 + amp * np.exp(-damp * u) * np.cos(freq * u)


def hash01(*args):
    """Deterministic pseudo-random in [0,1) from integer arrays (vectorised)."""
    shape = np.broadcast(*[np.asarray(a) for a in args]).shape
    out = np.full(shape if shape else (1,), 1469598103934665603, np.uint64)
    with np.errstate(over="ignore"):
        for a in args:
            a = np.atleast_1d(np.asarray(a)).astype(np.int64).astype(np.uint64)
            out = (out ^ (a + np.uint64(0x9E3779B97F4A7C15))) * np.uint64(1099511628211)
            out ^= out >> np.uint64(29)
            out *= np.uint64(0xBF58476D1CE4E5B9)
            out ^= out >> np.uint64(32)
    r = (out >> np.uint64(11)).astype(np.float64) / float(1 << 53)
    return r.reshape(shape) if shape else float(r[0])


def lerp(a, b, t):
    return a + (b - a) * t
