"""Glyph atlas + vectorised layer composition.

Every picture in the film is built from monospace glyphs.  A GlyphAtlas pre-renders each glyph once for a
given cell size into a float32 alpha tile; composition is then pure numpy indexing (no per-glyph PIL work per
frame).  Block / box-drawing glyphs are drawn procedurally so they tile seamlessly.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = "/usr/share/fonts/truetype/jetbrains-mono/"
JB = {w: FONT_DIR + f"JetBrainsMono-{w}.ttf" for w in
      ("Thin", "ExtraLight", "Light", "Regular", "Medium", "SemiBold", "Bold", "ExtraBold", "Italic",
       "LightItalic")}
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

# glyphs drawn procedurally (seamless blocks and box lines)
PROC = set("█▀▄▌▐─│═║╔╗╚╝┌┐└┘├┤┬┴┼━┃╌")

_missing_cache: dict = {}


def _bitmap(font, ch, size=64):
    im = Image.new("L", (size * 2, size * 2))
    ImageDraw.Draw(im).text((size // 2, size // 2), ch, font=font, fill=255)
    return np.asarray(im)


def font_has(path: str, ch: str) -> bool:
    key = (path, ch)
    if key not in _missing_cache:
        f = ImageFont.truetype(path, 48)
        nd1 = _bitmap(f, "")
        nd2 = _bitmap(f, "\U0010fff0")
        b = _bitmap(f, ch)
        _missing_cache[key] = not ((b == nd1).all() or (b == nd2).all())
    return _missing_cache[key]


def _proc_glyph(c: str, cw: int, ch: int, ss: int, weight: float) -> np.ndarray:
    """Procedural block/box glyph at supersampled resolution (ss x)."""
    W, H = cw * ss, ch * ss
    a = np.zeros((H, W), np.float32)
    t = max(1.0, weight * ss)            # line thickness in ss pixels
    cx, cy = W / 2.0, H / 2.0

    def hl(x0, x1, yc, th=t):
        y0, y1 = int(round(yc - th / 2)), int(round(yc + th / 2))
        a[max(0, y0):min(H, y1), int(round(x0)):int(round(x1))] = 1

    def vl(y0, y1, xc, th=t):
        x0, x1 = int(round(xc - th / 2)), int(round(xc + th / 2))
        a[int(round(y0)):int(round(y1)), max(0, x0):min(W, x1)] = 1

    if c == "█":
        a[:] = 1
    elif c == "▀":
        a[: H // 2] = 1
    elif c == "▄":
        a[H // 2:] = 1
    elif c == "▌":
        a[:, : W // 2] = 1
    elif c == "▐":
        a[:, W // 2:] = 1
    elif c in "─━":
        hl(0, W, cy, t * (2 if c == "━" else 1))
    elif c == "╌":
        seg = W / 4
        hl(seg * 0.5, seg * 1.5, cy)
        hl(seg * 2.5, seg * 3.5, cy)
    elif c in "│┃":
        vl(0, H, cx, t * (2 if c == "┃" else 1))
    elif c in "┌┐└┘├┤┬┴┼":
        up = c in "└┘├┤┴┼"
        dn = c in "┌┐├┤┬┼"
        lf = c in "┐┘┤┬┴┼"
        rt = c in "┌└├┬┴┼"
        if up:
            vl(0, cy + t / 2, cx)
        if dn:
            vl(cy - t / 2, H, cx)
        if lf:
            hl(0, cx + t / 2, cy)
        if rt:
            hl(cx - t / 2, W, cy)
    elif c in "═║╔╗╚╝":
        g = max(t * 1.6, W * 0.22)       # gap between the double strokes
        o1, o2 = -g / 2, g / 2
        if c == "═":
            hl(0, W, cy + o1)
            hl(0, W, cy + o2)
        elif c == "║":
            vl(0, H, cx + o1)
            vl(0, H, cx + o2)
        else:
            # corners: outer and inner strokes
            right = c in "╔╚"           # arms go right
            down = c in "╔╗"            # arms go down
            sx = 1 if right else -1
            sy = 1 if down else -1
            for k, o in ((0, -g / 2), (1, g / 2)):
                # stroke k: outer (k=0) wraps around inner
                ox = cx - sx * g / 2 if k == 0 else cx + sx * g / 2
                oy = cy - sy * g / 2 if k == 0 else cy + sy * g / 2
                # horizontal part
                if right:
                    hl(ox - t / 2, W, oy)
                else:
                    hl(0, ox + t / 2, oy)
                if down:
                    vl(oy - t / 2, H, ox)
                else:
                    vl(0, oy + t / 2, ox)
    return a


class GlyphAtlas:
    """Pre-rendered alpha tiles for a set of glyphs at one cell size."""

    def __init__(self, chars: str, cw: int, ch: int, size: float, weight: str = "Regular",
                 ss: int = 4, proc_weight: float = 1.0, baseline: float | None = None,
                 x_shift: float = 0.0, proc: str | None = None, custom: dict | None = None):
        self.cw, self.ch = cw, ch
        chars = "".join(dict.fromkeys(" " + chars + "".join(custom or {})))   # unique, ' ' at index 0
        self.chars = chars
        self.index = {c: i for i, c in enumerate(chars)}
        tiles = np.zeros((len(chars), ch, cw), np.float32)
        jb_path = JB[weight]
        fj = ImageFont.truetype(jb_path, size * ss)
        fd = ImageFont.truetype(DEJAVU, size * ss)
        asc, desc = fj.getmetrics()
        adv = fj.getlength("M")
        W, H = cw * ss, ch * ss
        if baseline is None:
            base_y = (H - (asc + desc)) / 2 + asc
        else:
            base_y = baseline * H
        proc_set = PROC if proc is None else set(proc)
        custom = custom or {}
        for i, c in enumerate(chars):
            if c == " ":
                continue
            if c in custom:
                big = custom[c](cw, ch, ss).astype(np.float32)
            elif c in proc_set:
                big = _proc_glyph(c, cw, ch, ss, proc_weight * size / 10.0)
            else:
                f = fj if font_has(jb_path, c) else fd
                im = Image.new("L", (W, H))
                d = ImageDraw.Draw(im)
                gadv = f.getlength(c)
                x = (W - gadv) / 2 + x_shift * ss
                d.text((x, base_y), c, font=f, fill=255, anchor="ls")
                big = np.asarray(im, np.float32) / 255.0
            tiles[i] = big.reshape(ch, ss, cw, ss).mean(axis=(1, 3))
        self.tiles = tiles
        self.coverage = tiles.mean(axis=(1, 2))

    def idx(self, s: str) -> np.ndarray:
        return np.array([self.index.get(c, 0) for c in s], np.int32)


# --------------------------------------------------------------------------------------------- composition

def compose_grid(atlas: GlyphAtlas, idx: np.ndarray, col: np.ndarray) -> np.ndarray:
    """Dense grid -> image.  idx (R,C) int, col (R,C,3) float (already multiplied by alpha)."""
    R, C = idx.shape
    t = atlas.tiles[idx]                                # R,C,ch,cw
    img = t[..., None] * col[:, :, None, None, :]      # R,C,ch,cw,3
    return img.transpose(0, 2, 1, 3, 4).reshape(R * atlas.ch, C * atlas.cw, 3)


def compose_sparse(canvas: np.ndarray, atlas: GlyphAtlas, rows, cols, idx, col, ox: int = 0, oy: int = 0):
    """Add glyphs at grid cells (rows, cols) of a grid whose origin is (ox, oy) in canvas pixels.
    Cells falling outside the canvas are clipped (whole cell)."""
    if len(rows) == 0:
        return
    cw, ch = atlas.cw, atlas.ch
    H, W = canvas.shape[:2]
    rows = np.asarray(rows)
    cols = np.asarray(cols)
    x = ox + cols * cw
    y = oy + rows * ch
    ok = (x >= 0) & (y >= 0) & (x + cw <= W) & (y + ch <= H)
    if not ok.all():
        rows, cols, idx, col = rows[ok], cols[ok], np.asarray(idx)[ok], np.asarray(col)[ok]
        x, y = x[ok], y[ok]
    if len(rows) == 0:
        return
    tiles = atlas.tiles[idx][..., None] * np.asarray(col, np.float32)[:, None, None, :]   # K,ch,cw,3
    if ox % cw == 0 and oy % ch == 0 and H % ch == 0 and W % cw == 0:
        view = canvas.reshape(H // ch, ch, W // cw, cw, 3)
        r = (y // ch)
        c = (x // cw)
        view[r, :, c, :, :] += tiles
    else:
        yy = y[:, None, None] + np.arange(ch)[None, :, None]
        xx = x[:, None, None] + np.arange(cw)[None, None, :]
        yy = np.broadcast_to(yy, (len(y), ch, cw))
        xx = np.broadcast_to(xx, (len(x), ch, cw))
        np.add.at(canvas, (yy, xx), tiles)


def splat(canvas: np.ndarray, atlas: GlyphAtlas, xs, ys, idx, col, subpixel: bool = True):
    """Free placement of glyph sprites centred at float pixel positions (xs, ys).
    With subpixel=True each sprite is bilinearly distributed over 4 integer positions (smooth motion)."""
    xs = np.asarray(xs, np.float64)
    ys = np.asarray(ys, np.float64)
    if xs.size == 0:
        return
    idx = np.asarray(idx)
    col = np.asarray(col, np.float32)
    cw, ch = atlas.cw, atlas.ch
    H, W = canvas.shape[:2]
    x0 = xs - cw / 2.0
    y0 = ys - ch / 2.0
    tiles = atlas.tiles[idx][..., None] * col[:, None, None, :]
    if subpixel:
        ix = np.floor(x0).astype(np.int64)
        iy = np.floor(y0).astype(np.int64)
        fx = (x0 - ix).astype(np.float32)
        fy = (y0 - iy).astype(np.float32)
        # pad tile by one pixel right/bottom and shift
        K = len(xs)
        T = np.zeros((K, ch + 1, cw + 1, 3), np.float32)
        wx0, wx1 = (1 - fx)[:, None, None, None], fx[:, None, None, None]
        wy0, wy1 = (1 - fy)[:, None, None, None], fy[:, None, None, None]
        T[:, :ch, :cw] += tiles * wx0 * wy0
        T[:, :ch, 1:] += tiles * wx1 * wy0
        T[:, 1:, :cw] += tiles * wx0 * wy1
        T[:, 1:, 1:] += tiles * wx1 * wy1
        tiles = T
        th, tw = ch + 1, cw + 1
    else:
        ix = np.round(x0).astype(np.int64)
        iy = np.round(y0).astype(np.int64)
        th, tw = ch, cw
    ok = (ix > -tw) & (iy > -th) & (ix < W) & (iy < H)
    if not ok.all():
        ix, iy, tiles = ix[ok], iy[ok], tiles[ok]
    if len(ix) == 0:
        return
    yy = iy[:, None, None] + np.arange(th)[None, :, None]
    xx = ix[:, None, None] + np.arange(tw)[None, None, :]
    yy = np.broadcast_to(yy, (len(ix), th, tw))
    xx = np.broadcast_to(xx, (len(ix), th, tw))
    m = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
    np.add.at(canvas, (yy[m], xx[m]), tiles[m])


def add_layer(canvas: np.ndarray, layer: np.ndarray, x: float, y: float, gain: float = 1.0):
    """Additively blend a layer image at (possibly fractional) offset."""
    if gain <= 0:
        return
    ix, iy = int(np.floor(x)), int(np.floor(y))
    fx, fy = x - ix, y - iy
    if abs(fx) > 1e-3 or abs(fy) > 1e-3:
        h, w = layer.shape[:2]
        L = np.zeros((h + 1, w + 1, 3), np.float32)
        L[:h, :w] += layer * ((1 - fx) * (1 - fy))
        L[:h, 1:] += layer * (fx * (1 - fy))
        L[1:, :w] += layer * ((1 - fx) * fy)
        L[1:, 1:] += layer * (fx * fy)
        layer = L
    H, W = canvas.shape[:2]
    h, w = layer.shape[:2]
    x0, y0 = max(ix, 0), max(iy, 0)
    x1, y1 = min(ix + w, W), min(iy + h, H)
    if x1 <= x0 or y1 <= y0:
        return
    sub = layer[y0 - iy:y1 - iy, x0 - ix:x1 - ix]
    if gain != 1.0:
        canvas[y0:y1, x0:x1] += sub * gain
    else:
        canvas[y0:y1, x0:x1] += sub


def text_width(atlas: GlyphAtlas, s: str) -> int:
    return len(s) * atlas.cw


def draw_text(canvas, atlas: GlyphAtlas, s: str, x: float, y: float, colors, alpha=1.0, subpixel=False):
    """Draw a string with its top-left at (x, y).  colors: (3,) or (len(s),3).  alpha scalar or per-char."""
    n = len(s)
    if n == 0:
        return
    idx = atlas.idx(s)
    col = np.asarray(colors, np.float32)
    if col.ndim == 1:
        col = np.broadcast_to(col, (n, 3))
    a = np.asarray(alpha, np.float32)
    if a.ndim == 0:
        a = np.full(n, float(a), np.float32)
    keep = (idx != 0) & (a > 1e-4)
    if not keep.any():
        return
    xs = x + atlas.cw * (np.arange(n) + 0.5)
    ys = np.full(n, y + atlas.ch * 0.5)
    splat(canvas, atlas, xs[keep], ys[keep], idx[keep], col[keep] * a[keep, None], subpixel=subpixel)
