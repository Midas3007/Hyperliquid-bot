"""HEVI ASCII portrait: source illustration -> glyph grid (index, colour, masks) with blink variants.

Method (chosen after several visual iterations):
  * cream background is removed by flood-filling from the image border (enclosed cream = face/apron stays);
  * every source pixel is put in a tonal class (ink, dark teal, mid teal, light mint, cream, iris, iris highlight);
  * each cell takes the texture glyph + palette colour of its dominant class (dense '%' for dark fills, '=' '*'
    for mid tones, '+' for cream skin/apron, glowing '@'/'o' for the irises);
  * line art: cells crossed by the source's dark outline get a directional glyph (/ \\ | _ - ( ) ^ ~ ...) chosen by
    correlating the cell's ink pattern with the glyph bitmaps, drawn in a dim teal so the drawing reads;
  * blink variants are produced by repainting the source eyes (lash line moved down) and re-running the mapping.
"""
from __future__ import annotations

import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from common import SRC_IMAGE, CACHE_DIR, BG, TEAL, DEEP, MINT, CREAM, LUMW
from glyphs import GlyphAtlas, compose_grid

# --- source geometry (pixels in assets/hevi.webp, measured by eye on zoomed crops) -------------------------
IRIS = [(312, 290, 17, 24), (421, 288, 19, 24)]          # cx, cy, rx, ry of each iris
EYE_BOX = [(270, 244, 338, 318), (378, 242, 466, 316)]   # region that changes during a blink
LASH = [(270, 244, 338, 274), (378, 242, 466, 274)]      # upper lash band
HEADPHONE = (531, 287, 34, 49)                            # ear-cup ellipse
SKIN = (254, 249, 234)

PORTRAIT_GLYPHS = " %=*+@o" + "/\\|_-()'`,.^~#"
LINE_SET = "/\\|_-()'`,.^~="
CLASS_GLYPH = (" ", "%", "=", "*", "+", "@", "o")
CLASS_COLOR = None   # set in _class_colors


def _class_colors():
    return np.array([BG, DEEP * 1.6, TEAL * 0.80, MINT * 0.75, CREAM * 0.80, MINT * 1.0, CREAM], np.float32)


LINE_COLOR = TEAL * 0.55


def load_source():
    im = Image.open(SRC_IMAGE).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    near = (np.abs(a - np.array([255, 253, 243])).max(-1) < 14)
    m = Image.fromarray((near * 255).astype(np.uint8)).copy()
    W, H = m.size
    for x in range(0, W, 8):
        for y in (0, H - 1):
            if m.getpixel((x, y)) == 255:
                ImageDraw.floodfill(m, (x, y), 128, thresh=0)
    for y in range(0, H, 8):
        for x in (0, W - 1):
            if m.getpixel((x, y)) == 255:
                ImageDraw.floodfill(m, (x, y), 128, thresh=0)
    fg = (np.asarray(m) != 128)
    fg[:, 720:] = False                                     # drop the "HEVI" lettering on the right
    return im, fg


# lid curves (quadratic Bezier: start, control, end, outer-corner flick) for each eye, source px
LIDS = [((272, 262), (300, 238), (332, 270), ((272, 262), (264, 255))),
        ((384, 272), (414, 236), (458, 268), ((458, 268), (467, 262)))]


def _bezier(p0, p1, p2, n=24):
    t = np.linspace(0, 1, n)[:, None]
    p0, p1, p2 = (np.array(p, float) for p in (p0, p1, p2))
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2


def paint_blink(im: Image.Image, amount: float) -> Image.Image:
    """Return a copy of the source with the eyes closed by `amount` (0 open, 0.5 half, 1 closed).
    The eye opening is painted skin above the lid and a thick dark lid line (the lash) is drawn at the
    lowered position; for the half blink the lower half of the iris stays visible below the lid."""
    ss = 4
    out = im.copy()
    for (bx0, by0, bx1, by1), (p0, p1, p2, flick), (cx, cy, rx, ry) in zip(EYE_BOX, LIDS, IRIS):
        pad = 4
        box = (bx0 - pad, by0 - pad, bx1 + pad, by1 + pad)
        patch = out.crop(box).resize(((box[2] - box[0]) * ss, (box[3] - box[1]) * ss), Image.LANCZOS)
        d = ImageDraw.Draw(patch)

        def P(pt, dy=0.0):
            return ((pt[0] - box[0]) * ss, (pt[1] + dy - box[1]) * ss)

        if amount >= 0.99:
            # closed: the whole opening becomes skin, the lid is a gentle concave-up arc through the eye centre
            ex0, ex1 = p0[0], p2[0]
            d.ellipse([P((bx0 + 2, by0 + 10)), P((bx1 - 2, by1 + 2))], fill=SKIN)
            d.line([P(tuple(q)) for q in _bezier(p0, p1, p2)], fill=SKIN, width=int(15 * ss), joint="curve")
            ymid = cy + 4
            a, c = (ex0, (p0[1] + ymid) / 2 + 6), (ex1, (p2[1] + ymid) / 2 + 6)
            b = ((ex0 + ex1) / 2, ymid + 12)
            curve = _bezier(a, b, c)
            width = 6
        else:
            dy = amount * 2 * 15
            # skin above the lowered lid
            orig = _bezier(p0, p1, p2)
            curve = orig + np.array([0, dy])
            d.line([P(tuple(q)) for q in orig], fill=SKIN, width=int(15 * ss), joint="curve")
            poly = [P(tuple(q)) for q in list(orig) + list(curve[::-1])]
            d.polygon(poly, fill=SKIN)
            width = 9
        pts = [P(tuple(q)) for q in curve]
        d.line(pts, fill=(2, 34, 22), width=int(width * ss), joint="curve")
        for q in (pts[0], pts[-1]):
            r = width * ss / 2
            d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=(2, 34, 22))
        # outer-corner lash flick
        f0, f1 = flick
        dy = 0 if amount < 0.99 else (cy + 4 - p0[1]) * 0.6
        d.line([P(f0, dy), P(f1, dy)], fill=(2, 34, 22), width=int(4 * ss))
        patch = patch.resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS)
        out.paste(patch, box[:2])
    return out


class PortraitData:
    """Glyph grid for one framing of the portrait (crop + cell size)."""

    def __init__(self, cw, ch, size, crop, rows, weight="Bold", extra_chars=""):
        self.cw, self.ch, self.size = cw, ch, size
        self.crop = crop
        self.R = rows
        x0, y0, x1, y1 = crop
        self.scale = rows * ch / (y1 - y0)
        self.C = int(np.ceil((x1 - x0) * self.scale / cw))
        self.atlas = GlyphAtlas(PORTRAIT_GLYPHS + extra_chars, cw, ch, size, weight=weight)
        self.W, self.H = self.C * cw, self.R * ch

    # source px -> layer px
    def to_layer(self, sx, sy):
        x0, y0 = self.crop[:2]
        return (sx - x0) * self.scale, (sy - y0) * self.scale

    def build(self, im, fg, iris_mode="open"):
        cw, ch, R, C = self.cw, self.ch, self.R, self.C
        k = 2
        x0, y0, x1, y1 = self.crop
        x1 = x0 + C * cw / self.scale
        Wp, Hp = C * cw * k, R * ch * k
        rgb = np.asarray(im.resize((Wp, Hp), Image.LANCZOS, box=(x0, y0, x1, y1))).astype(np.float32) / 255
        m = np.asarray(Image.fromarray((fg * 255).astype(np.uint8)).resize(
            (Wp, Hp), Image.BILINEAR, box=(x0, y0, x1, y1))).astype(np.float32) / 255
        L = rgb @ LUMW
        cls = np.digitize(L, [0.17, 0.42, 0.68, 0.86])
        yy, xx = np.mgrid[0:Hp, 0:Wp]
        sx = x0 + xx / (k * self.scale)
        sy = y0 + yy / (k * self.scale)
        iris = np.zeros_like(L, bool)
        if iris_mode != "closed":
            for cx, cy, rx, ry in IRIS:
                e = ((sx - cx) / rx) ** 2 + ((sy - cy) / ry) ** 2 < 1
                if iris_mode == "half":
                    e &= sy > cy - 2
                iris |= e
        cls = np.where(iris & (cls >= 1) & (cls <= 3), 5, cls)
        cls = np.where(iris & (cls == 4), 6, cls)
        n = 7
        ink = ((cls == 0) * m).astype(np.float32)

        def cells(x):
            return x.reshape(R, ch, k, C, cw, k).mean(axis=(2, 5)).transpose(0, 2, 1, 3)

        inkc = cells(ink)
        fgc = cells(m).mean(axis=(2, 3))
        inkf = inkc.mean(axis=(2, 3))
        hist = np.stack([((cls == c) * m).reshape(R, ch * k, C, cw * k).mean(axis=(1, 3)) for c in range(n)], -1)
        h2 = hist.copy()
        h2[..., 0] = 0
        dom = np.argmax(h2, -1)
        at = self.atlas
        gl = np.array([at.index[g] for g in CLASS_GLYPH])
        cols = _class_colors()
        idx = gl[dom]
        col = cols[dom].copy()
        # line glyphs from the ink pattern
        li = at.idx(LINE_SET)
        T = _blur3(at.tiles[li]).reshape(len(li), -1)
        Tn = T / np.linalg.norm(T, axis=1, keepdims=True)
        P = _blur3(inkc).reshape(R * C, -1)
        Pn = P / (np.linalg.norm(P, axis=1, keepdims=True) + 1e-6)
        corr = Pn @ Tn.T
        be = np.argmax(corr, 1)
        bc = corr[np.arange(len(be)), be]
        thr = np.where(dom == 4, 0.03, 0.10)          # fine features (nose, mouth) inside cream skin
        isline = ((inkf > thr) & (inkf < 0.75)) & (bc.reshape(R, C) > 0.3)
        isline &= ~np.isin(dom, [5, 6]) | (inkf > 0.3)
        isdense = inkf >= 0.75
        idx = np.where(isline, li[be].reshape(R, C), idx)
        col = np.where(isline[..., None], LINE_COLOR, col)
        idx = np.where(isdense, at.index["#"], idx)
        col = np.where(isdense[..., None], LINE_COLOR * 0.6, col)
        on = fgc > 0.45
        idx = np.where(on, idx, 0)
        col = col * on[..., None]
        # tint (soft glow underlay) colour per cell
        tint = (hist[..., 1:, None] * cols[None, None, 1:]).sum(2) * on[..., None]
        return idx.astype(np.int32), col.astype(np.float32), tint.astype(np.float32), on, fgc

    def masks(self):
        R, C, cw, ch = self.R, self.C, self.cw, self.ch
        x0, y0 = self.crop[:2]
        cy, cx = np.mgrid[0:R, 0:C]
        sx = x0 + (cx + 0.5) * cw / self.scale
        sy = y0 + (cy + 0.5) * ch / self.scale
        hx, hy, hrx, hry = HEADPHONE
        d = np.sqrt(((sx - hx) / hrx) ** 2 + ((sy - hy) / hry) ** 2)
        self.headphone = np.clip(1.25 - d, 0, 1).astype(np.float32)          # soft weight
        self.headphone_ring = np.exp(-((d - 0.82) / 0.16) ** 2).astype(np.float32)
        eye = np.zeros((R, C), bool)
        for bx0, by0, bx1, by1 in EYE_BOX:
            eye |= (sx >= bx0 - 2) & (sx <= bx1 + 2) & (sy >= by0 - 2) & (sy <= by1 + 2)
        self.eye = eye
        self.headphone_px = self.to_layer(hx, hy)
        self.face_px = self.to_layer(370, 300)


def _blur3(x):
    k = np.array([0.25, 0.5, 0.25], np.float32)
    y = x.copy()
    y[..., 1:-1, :] = x[..., :-2, :] * k[0] + x[..., 1:-1, :] * k[1] + x[..., 2:, :] * k[2]
    z = y.copy()
    z[..., :, 1:-1] = y[..., :, :-2] * k[0] + y[..., :, 1:-1] * k[1] + y[..., :, 2:] * k[2]
    return z


def build_portrait(name, cw, ch, size, crop, rows, extra_chars="", light=None):
    """Build (or load from cache) the portrait grids for open / half / closed eyes."""
    p = PortraitData(cw, ch, size, crop, rows, extra_chars=extra_chars)
    p.masks()
    cache = os.path.join(CACHE_DIR, f"portrait_{name}_{cw}x{ch}_{rows}_v8.npz")
    if os.path.exists(cache):
        z = np.load(cache)
        for k in z.files:
            setattr(p, k, z[k])
    else:
        im, fg = load_source()
        res = {}
        for mode, amt in (("open", 0.0), ("half", 0.5), ("closed", 1.0)):
            src = im if amt == 0 else paint_blink(im, amt)
            idx, col, tint, on, fgc = p.build(src, fg, iris_mode=mode)
            res[f"idx_{mode}"] = idx
            res[f"col_{mode}"] = col
            res[f"tint_{mode}"] = tint
            if mode == "open":
                res["on"] = on
                res["fgc"] = fgc
        # outside the eye boxes the variants must be identical to the open grid
        for mode in ("half", "closed"):
            keep = ~p.eye
            res[f"idx_{mode}"][keep] = res["idx_open"][keep]
            res[f"col_{mode}"][keep] = res["col_open"][keep]
            res[f"tint_{mode}"][keep] = res["tint_open"][keep]
        os.makedirs(CACHE_DIR, exist_ok=True)
        np.savez_compressed(cache, **res)
        for k, v in res.items():
            setattr(p, k, v)
    # spatial light: soft key from above, fading towards the bottom of the frame
    R, C = p.R, p.C
    rr = np.linspace(0, 1, R)[:, None]
    cc = np.linspace(0, 1, C)[None, :]
    if light is None:
        light = (0.42, 0.78)
    a, b = light
    lf = np.clip(1.12 - b * np.clip((rr - a) / (1 - a), 0, 1) ** 1.2, 0, 1.2) * (1 - 0.12 * np.abs(cc - 0.5))
    p.light = lf.astype(np.float32)
    return p


def glow_underlay(p: PortraitData, tint_cells: np.ndarray, blur: float = 4.0) -> np.ndarray:
    """Soft per-cell colour image behind the glyphs (the glyphs' own light), at layer resolution."""
    t = np.clip(tint_cells * p.light[..., None], 0, 1)
    im = Image.fromarray((t * 255).astype(np.uint8)).resize((p.W, p.H), Image.BILINEAR)
    im = im.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(im).astype(np.float32) / 255.0
