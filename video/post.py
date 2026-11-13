"""Film finish: bloom pyramid, chromatic aberration, flash, vignette, scanlines, grain, soft tone shoulder."""
from __future__ import annotations

import numpy as np
from PIL import Image

from common import W, H, BG

_K5 = np.array([1, 4, 6, 4, 1], np.float32) / 16.0


def _blur5(a: np.ndarray, passes: int = 1) -> np.ndarray:
    """Binomial 5-tap separable blur (edge clamped) on an (h, w, 3) array."""
    for _ in range(passes):
        p = np.pad(a, ((2, 2), (0, 0), (0, 0)), mode="edge")
        a = p[:-4] * _K5[0] + p[1:-3] * _K5[1] + p[2:-2] * _K5[2] + p[3:-1] * _K5[3] + p[4:] * _K5[4]
        p = np.pad(a, ((0, 0), (2, 2), (0, 0)), mode="edge")
        a = p[:, :-4] * _K5[0] + p[:, 1:-3] * _K5[1] + p[:, 2:-2] * _K5[2] + p[:, 3:-1] * _K5[3] + p[:, 4:] * _K5[4]
    return a


def _resize(a: np.ndarray, w: int, h: int, method) -> np.ndarray:
    out = np.empty((h, w, 3), np.float32)
    for c in range(3):
        out[..., c] = np.asarray(Image.fromarray(np.ascontiguousarray(a[..., c]), "F").resize((w, h), method))
    return out


def _down2(a: np.ndarray) -> np.ndarray:
    h, w = a.shape[:2]
    h2, w2 = h // 2, w // 2
    a = a[:h2 * 2, :w2 * 2]
    return a.reshape(h2, 2, w2, 2, 3).mean(axis=(1, 3))


def bloom(img: np.ndarray, gain: float = 1.0, threshold: float = 0.04,
          weights=(0.55, 0.65, 0.65, 0.55, 0.45)) -> np.ndarray:
    """Multi-scale glow.  Returns the glow image (to be added)."""
    src = np.maximum(img - threshold, 0.0)
    # level 1 = 1/2 res ... level 5 = 1/32 res
    lv = []
    a = _down2(src)
    for i in range(5):
        a = _down2(a) if i > 0 else a
        lv.append(_blur5(a, 1 if i < 2 else 2))
    acc = lv[-1] * weights[-1]
    for i in range(len(lv) - 2, -1, -1):
        h, w = lv[i].shape[:2]
        acc = _resize(acc, w, h, Image.BILINEAR) + lv[i] * weights[i]
    return _resize(acc, W, H, Image.BILINEAR) * gain


class Finisher:
    def __init__(self, seed: int = 7):
        rng = np.random.default_rng(seed)
        # grain: a few textures at half resolution, upsampled (2 px grain)
        self.grain = []
        for _ in range(6):
            g = rng.standard_normal((H // 2 + 8, W // 2 + 8)).astype(np.float32)
            g = (g + 0.35 * np.roll(g, 1, 0) + 0.35 * np.roll(g, 1, 1)) / 1.25
            self.grain.append(g)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        nx = (xx - W / 2) / (W / 2)
        ny = (yy - H / 2) / (H / 2)
        r2 = (nx * nx * 0.85 + ny * ny * 1.0)
        self.vignette = (1.0 - 0.30 * np.clip(r2, 0, 2) ** 1.25)[..., None].astype(np.float32)
        scan = 1.0 - 0.055 * (0.5 + 0.5 * np.cos(2 * np.pi * yy[:, :1] / 3.0))
        self.scan = scan[..., None].astype(np.float32)        # (H,1,1)
        # background: near-black with the faintest teal lift in the middle (never pure black)
        lift = np.exp(-(nx * nx * 0.6 + ny * ny * 1.1) * 1.4)[..., None]
        self.background = (BG[None, None, :] * (0.92 + 0.22 * lift)).astype(np.float32)

    def finish(self, content: np.ndarray, frame: int, bloom_gain: float = 1.0, flash: float = 0.0,
               ca: float = 0.0, exposure: float = 1.0, grain_amt: float = 0.016) -> np.ndarray:
        """content: emitted light on black (H,W,3) float.  Returns uint8 RGB frame."""
        img = content * exposure
        if bloom_gain > 0:
            img = img + bloom(img, gain=bloom_gain)
        if ca > 0.05:
            img = _chromatic(img, ca)
        img = img + self.background
        if flash > 0:
            img = img + flash
        img = img * self.scan
        img = img * self.vignette
        # soft shoulder above 0.8 (keeps colours true below, rolls bright glow towards white gracefully)
        over = np.maximum(img - 0.8, 0.0)
        img = np.minimum(img, 0.8) + 0.2 * (1.0 - np.exp(-over / 0.2))
        # luminance-shaped grain (more in the darks/mids, none on top of blown highlights)
        g = self.grain[frame % len(self.grain)]
        rng = np.random.default_rng(frame * 7919 + 13)
        oy, ox = rng.integers(0, 8, size=2)
        g = g[oy:oy + H // 2, ox:ox + W // 2]
        g = np.repeat(np.repeat(g, 2, 0), 2, 1)[..., None]
        lum = img.mean(axis=2, keepdims=True)
        img = img + g * grain_amt * (0.55 + 0.9 * np.clip(lum * 3, 0, 1) * (1 - np.clip(lum - 0.6, 0, 1)))
        return np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _chromatic(img: np.ndarray, amt: float) -> np.ndarray:
    """Radial-ish chromatic aberration: red pushed outwards, blue inwards (horizontal + vertical scale)."""
    out = img.copy()
    s = amt / (W / 2)
    for c, sign in ((0, 1.0), (2, -1.0)):
        k = 1.0 + sign * s
        ch = Image.fromarray(np.ascontiguousarray(img[..., c]), "F")
        # scale about centre
        a = 1.0 / k
        ch = ch.transform((W, H), Image.AFFINE, (a, 0, W / 2 * (1 - a), 0, a, H / 2 * (1 - a)), Image.BILINEAR)
        out[..., c] = np.asarray(ch)
    return out
