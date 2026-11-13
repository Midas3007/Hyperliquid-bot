"""HEVI — "She Waits".  Every section of the storyboard, drawn from glyphs.

Film.render(frame) -> uint8 RGB (H, W, 3).  All randomness is seeded, so every frame is deterministic and
frames can be rendered in any order / in parallel.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

import blockfont
from common import (W, H, FPS, BEAT, BG, TEAL, DEEP, MINT, CREAM, GREEN, RED, BLUE, DIM, WHITE,
                    prog, smoothstep, ease_in_out_cubic, ease_out_cubic, ease_in_cubic, ease_out_expo,
                    ease_out_quint, ease_in_out_sine, fade, beat_pulse, damped_overshoot, hash01, lerp, clamp01)
from glyphs import GlyphAtlas, compose_grid, compose_sparse, splat, add_layer, draw_text
from portrait import build_portrait, glow_underlay
from post import Finisher

# ------------------------------------------------------------------------------------------- text content
BOOT_LINES = ["> hevi --boot",
              "linking hyperliquid ........ ok",
              "tracking 200 whale wallets ... ok",
              "risk engine ................. armed",
              "mode: patient"]
CHECKS = ["stop beyond 4H structure",
          "1:3 after fees — or no trade",
          "isolated margin, stops on the exchange",
          "flat by friday 20:00 utc",
          "breakers after losing streaks",
          "real money only after the paper record proves an edge"]
STATEMENT = "most scans end in no trade. by design."
SUBTITLE = "autonomous hyperliquid trader"
TAGLINE = "she doesn't chase. she waits."
URL_LINE = "every trade, public · hevi-record.netlify.app"
COINS = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "SUI", "AVAX", "LINK", "BNB", "ADA", "LTC",
         "DOT", "TON", "NEAR", "APT", "ARB", "OP", "INJ", "TIA", "SEI", "WIF", "PEPE", "BONK",
         "JUP", "ENA", "ONDO", "AAVE", "UNI", "LDO", "PENDLE", "TAO", "RENDER", "FET", "WLD", "STX",
         "FIL", "ATOM", "ETC", "BCH", "TRX", "XLM", "HBAR", "KAS", "ORDI", "POPCAT", "MKR", "CRV"]

RAIN_SEGMENTS = ["BTC 91,234.5 ▲", "SOL 99.61 ▼", "HYPE 41.2", "ETH 3,412.8 ▲", "SOL 99.61 ▲", "HYPE 41.2 ▲",
                 "BTC 91,180.0 ▼", "XRP 2.4117", "DOGE 0.3821 ▼", "AVAX 38.06", "LINK 23.94 ▲", "SUI 4.512",
                 "0.0042", "91,234.5", "99.61", "41.2", "+1.8%", "-0.6%", "OI 1.2B", "FUND +0.011%",
                 "3412.8", "0.618", "1:3.4", "BTC", "SOL", "HYPE", "▲", "▼", "41.20 ▲", "99.58",
                 "99.64 ▲", "L/S 0.42", "$100k", "ENA 0.912", "TAO 482.1", "WIF 2.37 ▼", "1.50%"]
SCRAMBLE = "!#$%&*+-=?@ABCDEFGHJKLMNPRSTUVXYZ0123456789/\\<>[]{}"
RAIN_CHARS = "".join(sorted(set("".join(RAIN_SEGMENTS)))) + SCRAMBLE

# ------------------------------------------------------------------------------------------- layout consts
P_X, P_Y = 60, 24                    # hero portrait top-left (on the 6x12 screen grid)
GRID_CW, GRID_CH = 6, 12
GR, GC = H // GRID_CH, W // GRID_CW  # 90 x 320 fine screen grid

BLINKS = [16.5, 20.25, 28.75, 55.5]


def blink_mode(t):
    for tb in BLINKS:
        d = t - tb
        if 0 <= d < 0.04:
            return "half"
        if 0.04 <= d < 0.115:
            return "closed"
        if 0.115 <= d < 0.165:
            return "half"
    return "open"


def phosphor(age, base_col, flash_col=MINT, decay=0.12):
    """New glyphs flash bright then settle to their colour."""
    k = np.exp(-np.maximum(age, 0) / decay)[..., None] if np.ndim(age) else np.exp(-max(age, 0) / decay)
    return base_col * (1 - 0.35 * k) + flash_col * 1.25 * k


def resize_layer(img: np.ndarray, scale: float, method=Image.BICUBIC):
    """Scale an (h,w,3) float layer.  Returns (scaled, exact float size)."""
    h, w = img.shape[:2]
    ow, oh = max(1, int(np.ceil(w * scale))), max(1, int(np.ceil(h * scale)))
    img = np.pad(img, ((0, 3), (0, 3), (0, 0)))           # box may reach slightly past the edge
    out = np.empty((oh, ow, 3), np.float32)
    box = (0, 0, ow / scale, oh / scale)
    for c in range(3):
        ch = Image.fromarray(np.ascontiguousarray(img[..., c], dtype=np.float32), "F")
        out[..., c] = np.asarray(ch.resize((ow, oh), method, box=box))
    return np.maximum(out, 0)


class Film:
    def __init__(self, verbose=False):
        rng = np.random.default_rng(20251113)
        self.fin_post = Finisher()
        # ---------------------------------------------------------------- portraits
        self.hero = build_portrait("hero", 6, 12, 10, (100, 20, 690, 780), 86, extra_chars=RAIN_CHARS)
        self.final = build_portrait("final", 8, 16, 13.4, (112, 18, 652, 640), 62, extra_chars=SCRAMBLE,
                                    light=(0.30, 0.92))
        hp = self.hero
        rr = np.arange(hp.R)[:, None]
        hp.light = (hp.light * (1 - 0.9 * smoothstep((rr - (hp.R - 30)) / 30.0))).astype(np.float32)
        self.hero_glow = {m: glow_underlay(hp, getattr(hp, f"tint_{m}")) for m in ("open", "half", "closed")}
        fp = self.final
        self.final_glow = {m: glow_underlay(fp, getattr(fp, f"tint_{m}"), blur=5) for m in ("open", "half", "closed")}
        self.fine = hp.atlas                                      # 6x12 Bold — rain shares the portrait grid
        # ---------------------------------------------------------------- atlases
        self.term = GlyphAtlas("".join(BOOT_LINES) + "█▌_", 18, 48, 30, weight="Regular", proc="")
        self.near = GlyphAtlas(RAIN_CHARS, 12, 24, 19, weight="Regular")
        self.hud = GlyphAtlas("".join(chr(c) for c in range(32, 127)) + "·•●○◂▸◀◆→−━─│┌┐└┘╌▌█✓—▲▼",
                              11, 24, 17, weight="Regular")
        self.hudb = GlyphAtlas("".join(chr(c) for c in range(32, 127)) + "·→", 11, 24, 17, weight="SemiBold")
        self.small = GlyphAtlas("".join(chr(c) for c in range(32, 127)) + "·•●○", 9, 18, 14, weight="Regular")
        self.caps = GlyphAtlas("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ·", 15, 24, 15, weight="Medium")
        self.sub = GlyphAtlas(SUBTITLE + "▌", 25, 40, 24, weight="Light", proc="")
        self.block = GlyphAtlas(blockfont.CHARS, 26, 52, 26, proc_weight=1.2)
        self.check = GlyphAtlas("".join(CHECKS) + "✓", 16, 48, 26, weight="Regular")
        self.checkbig = GlyphAtlas("✓", 48, 64, 52, weight="Bold")
        self.statement = GlyphAtlas(STATEMENT, 19, 48, 30, weight="Light")
        self.title = GlyphAtlas("HEVI", 64, 96, 66, weight="Light")
        self.tag = GlyphAtlas(TAGLINE, 18, 44, 28, weight="Regular")
        self.url = GlyphAtlas(URL_LINE, 11, 24, 16, weight="Regular")
        self.tpat = GlyphAtlas("TP filled · on-exchange · isolated", 15, 30, 21, weight="Regular")
        self._build_candle_atlas()
        # ---------------------------------------------------------------- precomputed pieces
        self._build_boot()
        self._build_rain(rng)
        self._build_reveal(rng)
        self._build_blocks()
        self._build_whales(rng)
        self._build_flow(rng)
        self._build_funnel(rng)
        self._build_chart(rng)
        self._build_finale(rng)
        # screen grid cell centres (for shockwave / radial effects)
        gy, gx = np.mgrid[0:GR, 0:GC]
        self.gcx = (gx + 0.5) * GRID_CW
        self.gcy = (gy + 0.5) * GRID_CH
        self.shock_set = self.fine.idx("·:+*=#%@x")

    # ===================================================================================== helpers
    def text(self, cv, atlas, s, x, y, col, alpha=1.0, subpixel=False):
        draw_text(cv, atlas, s, x, y, col, alpha, subpixel=subpixel)

    def text_center(self, cv, atlas, s, cx, y, col, alpha=1.0):
        x = cx - len(s) * atlas.cw / 2
        draw_text(cv, atlas, s, round(x), y, col, alpha)

    def typed(self, cv, atlas, s, x, y, cols, t, t0, dur, flash=MINT, decay=0.12, alpha=1.0, ease=None):
        """Type a string in from t0 over dur: each glyph appears with a phosphor flash.
        cols: (3,) or per-char (n,3).  Returns number of visible characters (for cursors)."""
        n = len(s)
        if t < t0 or n == 0:
            return 0
        u = (t - t0) / dur
        if ease is not None:
            u = float(ease(min(u, 1.0))) if u < 1 else 1.0
        shown = int(np.clip(np.floor(u * n + 1e-6), 0, n))
        if shown == 0:
            return 0
        cols = np.asarray(cols, np.float32)
        if cols.ndim == 1:
            cols = np.broadcast_to(cols, (n, 3))
        # time each char appeared (inverse of the typing curve, linear approx)
        if ease is None:
            appear = t0 + (np.arange(n) / n) * dur
        else:
            grid = np.linspace(0, 1, 256)
            appear = t0 + np.interp(np.arange(n) / n, ease(grid), grid) * dur
        age = t - appear[:shown]
        c = phosphor(age, cols[:shown], flash, decay)
        a = np.clip(age / 0.034 + 0.35, 0, 1) * alpha
        draw_text(cv, atlas, s[:shown], x, y, c, a)
        return shown

    # ===================================================================================== 1 BOOT
    def _build_boot(self):
        at = self.term
        self.boot_x = (W - 35 * at.cw) // 2
        self.boot_y = (H - 6 * at.ch) // 2 - 10
        cols = []
        for i, s in enumerate(BOOT_LINES):
            c = np.zeros((len(s), 3), np.float32)
            if i == 0:
                c[:] = CREAM * 0.92
                c[0] = MINT
            elif i == 4:
                c[:] = CREAM * 0.62
                c[6:] = MINT
            else:
                c[:] = CREAM * 0.78
                for j, ch in enumerate(s):
                    if ch == ".":
                        c[j] = DIM * 1.25
                st = s.split()[-1]
                k = len(s) - len(st)
                c[k:] = GREEN if st == "ok" else MINT
            cols.append(c)
        self.boot_cols = cols
        self.boot_t0 = [1.0, 1.5, 2.0, 2.5, 3.0]
        self.boot_dur = [0.40, 0.30, 0.31, 0.32, 0.26]
        rng = np.random.default_rng(5)
        self.boot_fall = [rng.random(len(s)) for s in BOOT_LINES]

    def draw_boot(self, cv, t):
        at = self.term
        x0, y0 = self.boot_x, self.boot_y
        melt = t >= 6.0
        cursor_line, cursor_col = 0, 0
        typing = False
        for i, s in enumerate(BOOT_LINES):
            t0, dur = self.boot_t0[i], self.boot_dur[i]
            if t < t0:
                break
            y = y0 + i * at.ch
            if not melt:
                n = self.typed(cv, at, s, x0, y, self.boot_cols[i], t, t0, dur, flash=MINT, decay=0.10)
                cursor_line, cursor_col = (i, n) if n < len(s) else (i + 1, 0)
                typing = n < len(s)
                # status word pop
                if i in (1, 2, 3) and t >= t0 + dur:
                    st = s.split()[-1]
                    k = len(s) - len(st)
                    age = t - (t0 + dur)
                    g = np.exp(-age / 0.18)
                    if g > 0.02:
                        col = (GREEN if st == "ok" else MINT) * 0.9 * g
                        self.text(cv, at, st, x0 + k * at.cw, y, col)
            else:
                # 6.0 →: the terminal melts into the rain (glyphs fall away, staggered)
                r = self.boot_fall[i]
                tf = 6.0 + 0.05 * i + 0.45 * r
                tau = np.maximum(t - tf, 0)
                ys = y + at.ch / 2 + 0.5 * 2600 * tau ** 2
                a = np.clip(1 - tau / 0.42, 0, 1) ** 1.5
                xs = x0 + at.cw * (np.arange(len(s)) + 0.5)
                cols = self.boot_cols[i] * (0.55 + 0.45 * a[:, None])
                keep = (a > 0.01) & (at.idx(s) != 0)
                splat(cv, at, xs[keep], ys[keep], at.idx(s)[keep], cols[keep] * a[keep, None])
        # cursor: blinks on the beat (solid while typing)
        if not melt:
            if typing:
                on = 1.0
            else:
                ph = t % BEAT
                on = float(smoothstep(prog(ph, 0.0, 0.03)) * (1 - smoothstep(prog(ph, 0.24, 0.30))))
            if on > 0:
                cx = x0 + cursor_col * at.cw
                cy = y0 + cursor_line * at.ch
                self.text(cv, at, "▌", cx, cy, MINT * 0.95, on)

    # ===================================================================================== 2 RAIN
    def _build_rain(self, rng):
        def tapes(atlas, ncols, length, p_active, gap):
            idx = np.zeros((ncols, length), np.int32)
            bri = np.zeros((ncols, length), np.float32)
            kind = np.zeros((ncols, length), np.int8)
            for c in range(ncols):
                if rng.random() > p_active:
                    continue
                i = int(rng.integers(0, gap[1]))
                while i < length:
                    s = RAIN_SEGMENTS[int(rng.integers(len(RAIN_SEGMENTS)))]
                    if rng.random() < 0.25:
                        s = "".join(rng.choice(list("0123456789.,"), size=int(rng.integers(3, 9))))
                    n = len(s)
                    for j, ch in enumerate(s):
                        if i + j >= length:
                            break
                        idx[c, i + j] = atlas.index.get(ch, 0)
                        bri[c, i + j] = 1.0 if j == n - 1 else 0.16 + 0.5 * (j / max(n - 1, 1)) ** 1.5
                        kind[c, i + j] = 3 if j == n - 1 else (1 if ch == "▲" else (2 if ch == "▼" else 0))
                    i += n + int(rng.integers(gap[0], gap[1]))
            return idx, bri, kind

        L = 400
        self.rf_idx, self.rf_bri, self.rf_kind = tapes(self.fine, GC, L, 0.62, (6, 44))
        self.rf_v = rng.uniform(6.0, 15.0, GC).astype(np.float32)
        self.rf_ph = rng.uniform(0, L, GC).astype(np.float32)
        self.rf_dim = rng.uniform(0.55, 1.0, GC).astype(np.float32)
        self.rf_jit = rng.uniform(0, 1, GC).astype(np.float32)
        nc = W // self.near.cw
        self.rn_idx, self.rn_bri, self.rn_kind = tapes(self.near, nc, L, 0.16, (10, 60))
        self.rn_v = rng.uniform(9.0, 17.0, nc).astype(np.float32)
        self.rn_ph = rng.uniform(0, L, nc).astype(np.float32)
        # rain clock: full speed until 10.0, then slows smoothly (integrated speed curve)
        ts = np.linspace(6.0, 14.0, 8001)
        s = np.where(ts < 10.0, 1.0, 1.0 - 0.75 * smoothstep((ts - 10.0) / 3.2))
        self.rain_ts = ts
        self.rain_T = np.concatenate([[0], np.cumsum((s[1:] + s[:-1]) / 2 * np.diff(ts))])

    def rain_time(self, t):
        return float(np.interp(t, self.rain_ts, self.rain_T))

    def _rain_layer(self, atlas, tidx, tbri, tkind, v, ph, T, gain, colmul=None, row_alpha=None):
        """Falling tapes with smooth per-column pixel offsets.  Returns (H, W, 3) image."""
        cw, ch = atlas.cw, atlas.ch
        ncol = tidx.shape[0]
        nrow = H // ch + 1
        L = tidx.shape[1]
        pos = ph + v * T
        base = np.floor(pos)
        frac = pos - base
        r = np.arange(-1, nrow - 1)[:, None]                     # glyph rows (-1 is the one sliding in)
        ti = ((r - base[None, :]).astype(np.int64)) % L
        cidx = np.arange(ncol)[None, :]
        idx = tidx[cidx, ti]
        b = tbri[cidx, ti]
        k = tkind[cidx, ti]
        trail = lerp(DEEP[None, None] * 1.5, TEAL[None, None] * 0.95, b[..., None])
        col = np.where((k == 3)[..., None], MINT[None, None] * 1.05, trail)
        col = np.where((k == 1)[..., None], lerp(trail, GREEN[None, None], 0.55), col)
        col = np.where((k == 2)[..., None], lerp(trail, RED[None, None], 0.55), col)
        a = b * gain
        if colmul is not None:
            a = a * colmul[None, :]
        if row_alpha is not None:
            # alpha per glyph at its current screen position
            yc = (r + frac[None, :] + 0.5)                          # in rows
            a = a * row_alpha(yc, cidx)
        img = compose_grid(atlas, idx, col * a[..., None])           # (nrow*ch, ncol*cw)
        off = np.round(frac * ch).astype(np.int64)                  # per column pixel offset
        rows = np.arange(H)[:, None] + ch - np.repeat(off, cw)[None, :]
        xs = np.arange(ncol * cw)[None, :]
        out = img[rows, xs]
        if out.shape[1] < W:
            out = np.pad(out, ((0, 0), (0, W - out.shape[1]), (0, 0)))
        return out[:, :W]

    def _build_reveal(self, rng):
        hp = self.hero
        r0, c0 = P_Y // GRID_CH, P_X // GRID_CW
        self.p_r0, self.p_c0 = r0, c0
        # target grid on the full fine screen grid
        self.tgt_idx = np.zeros((GR, GC), np.int32)
        self.tgt_on = np.zeros((GR, GC), bool)
        self.tgt_idx[r0:r0 + hp.R, c0:c0 + hp.C] = hp.idx_open
        self.tgt_on[r0:r0 + hp.R, c0:c0 + hp.C] = hp.on
        self.tgt_col = np.zeros((GR, GC, 3), np.float32)
        self.tgt_col[r0:r0 + hp.R, c0:c0 + hp.C] = hp.col_open * hp.light[..., None]
        # lock time per cell: an eased front sweeping top -> bottom with an organic edge
        gy, gx = np.mgrid[0:GR, 0:GC].astype(np.float32)
        jit = (2.2 + 1.5 * np.sin(gx * 0.043 + 0.7) + 1.0 * np.sin(gx * 0.121 + gy * 0.05 + 1.3)
               + 1.4 * hash01(gy.astype(int), gx.astype(int), 3))
        ts = np.linspace(10.0, 13.5, 2001)
        front = -7 + (GR + 9) * ease_in_out_cubic((ts - 10.0) / 3.5)
        self.t_lock = np.interp(gy + jit, front, ts).astype(np.float32)
        self.band = 0.30

    def draw_rain_reveal(self, cv, t):
        T = self.rain_time(t)
        # terminal melting + rain curtain dropping in
        cur = (t - 6.0) * 150.0

        def far_alpha(yc, cidx):
            a = np.clip((cur + self.rf_jit[cidx] * 18 - yc) / 4.0, 0, 1)
            if t >= 10.0 - self.band - 0.2:
                rr = np.clip(yc.astype(int), 0, GR - 1)
                cc = np.broadcast_to(cidx, rr.shape)
                dt = t - self.t_lock[rr, cc]
                a = a * np.clip((-self.band - dt) / 0.12, 0, 1)
            return a

        g = fade(t, 6.0, 6.5)
        far = self._rain_layer(self.fine, self.rf_idx, self.rf_bri, self.rf_kind, self.rf_v, self.rf_ph,
                               T, 0.95 * g, colmul=self.rf_dim, row_alpha=far_alpha)
        cv += far
        gn = fade(t, 6.15, 6.8, 9.9, 11.2)
        if gn > 0.002:
            def near_alpha(yc, cidx):
                return np.clip((cur * 0.5 - yc) / 2.0, 0, 1)
            near = self._rain_layer(self.near, self.rn_idx, self.rn_bri, self.rn_kind, self.rn_v * 1.0,
                                    self.rn_ph, T, 0.55 * gn, row_alpha=near_alpha)
            # defocus the near layer slightly (depth): 2x box down/up
            nh = near.reshape(H // 2, 2, W // 2, 2, 3).mean(axis=(1, 3))
            near = np.repeat(np.repeat(nh, 2, 0), 2, 1) * 0.6 + near * 0.4
            cv += near
        if t < 10.0 - self.band - 0.2:
            return
        # scramble band + lock
        dt = t - self.t_lock
        scr = (dt >= -self.band) & (dt < 0)
        lck = dt >= 0
        on = self.tgt_on
        idx = np.zeros((GR, GC), np.int32)
        col = np.zeros((GR, GC, 3), np.float32)
        # scramble glyphs cycle every 2 frames
        fr = int(np.floor(t * 30))
        sc = self.fine.idx(SCRAMBLE)
        rnd = hash01(np.arange(GR)[:, None], np.arange(GC)[None, :], fr)
        sidx = sc[(rnd * len(sc)).astype(int) % len(sc)]
        q = np.clip((dt + self.band) / self.band, 0, 1)
        sin_ = scr & on
        sout = scr & ~on
        idx[sin_] = sidx[sin_]
        cq = q[..., None]
        col_in = lerp(TEAL * 0.55, self.tgt_col * 1.2 + MINT * 0.35, cq) * (0.25 + 0.95 * cq ** 1.6)
        col[sin_] = col_in[sin_]
        idx[sout] = sidx[sout]
        col_out = TEAL[None, None] * 0.24 * (0.35 + 0.65 * cq) * (1 - cq ** 3)
        col[sout] = col_out[sout]
        lk = lck & on
        idx[lk] = self.tgt_idx[lk]
        d = np.maximum(dt, 0)[..., None]
        flash = np.exp(-d / 0.16)
        col_l = self.tgt_col * (1 + 1.6 * flash) + (CREAM * 0.75) * np.exp(-d / 0.10) + MINT * 0.35 * np.exp(-d / 0.25)
        col[lk] = col_l[lk]
        gain = 1.0 - 0.22 * float(ease_in_cubic(prog(t, 13.5, 14.0)))
        cv += compose_grid(self.fine, idx, col * gain)
        # glow underlay grows with the lock
        hp = self.hero
        r0, c0 = self.p_r0, self.p_c0
        lp = smoothstep(dt[r0:r0 + hp.R, c0:c0 + hp.C] / 0.5)
        glow = glow_underlay(hp, hp.tint_open * lp[..., None])
        add_layer(cv, glow, P_X, P_Y, 0.16 * gain)

    # ===================================================================================== 3 HERO
    def portrait_layer(self, p, glows, t, pulse=True, gain=1.0, mode=None):
        mode = mode or blink_mode(t)
        idx = getattr(p, f"idx_{mode}")
        col = getattr(p, f"col_{mode}") * p.light[..., None]
        if pulse and t >= 14.0:
            pb = beat_pulse(t, 0.15)
            col = col * (1 + 0.9 * pb * p.headphone[..., None]) + MINT * (0.75 * pb) * p.headphone_ring[..., None] * (p.on[..., None])
            col = col + MINT * 0.10 * p.headphone_ring[..., None] * p.on[..., None]
        lay = compose_grid(p.atlas, idx, col * gain)
        lay += glows[mode] * (0.16 * gain)
        return lay

    def headphone_rings(self, cv, t, cx, cy, scale=1.0, gain=1.0):
        """Faint sonar ring of dots leaving the headphone on every beat."""
        ph = t % BEAT
        u = ph / BEAT
        r = (26 + 92 * float(ease_out_cubic(u))) * scale
        a = 0.40 * (1 - u) ** 2 * gain
        if a < 0.01:
            return
        n = 44
        ang = np.linspace(0, 2 * np.pi, n, endpoint=False) + 0.3
        xs = cx + r * np.cos(ang)
        ys = cy + r * np.sin(ang) * 1.05
        splat(cv, self.small, xs, ys, np.full(n, self.small.index["·"]), np.tile(MINT * a, (n, 1)))

    def _build_blocks(self):
        self.letters = self._block_word("HEVI", grad=(MINT * 0.80, TEAL * 0.74), shadow=DEEP * 1.8)
        bw = self.letters["width"] * self.block.cw
        self.blocks_x = 1392 - bw // 2
        self.blocks_y = 318
        self.r34 = self._block_word("+3.4R", grad=(CREAM * 0.74, CREAM * 0.62), shadow=TEAL * 0.55)

    def _block_word(self, word, grad, shadow):
        at = self.block
        items, width = blockfont.layout(word)
        out = []
        for ch, x, rows in items:
            n_r, n_c = len(rows), len(rows[0])
            idx = np.zeros((n_r, n_c), np.int32)
            col = np.zeros((n_r, n_c, 3), np.float32)
            for r, row in enumerate(rows):
                for c, g in enumerate(row):
                    if g == " ":
                        continue
                    idx[r, c] = at.index[g]
                    if g == "█":
                        col[r, c] = lerp(grad[0], grad[1], r / 4.0)
                    else:
                        col[r, c] = shadow
            img = compose_grid(at, idx, col)
            out.append(dict(ch=ch, x=x, img=img, w=n_c))
        return dict(items=out, width=width)

    def draw_block_word(self, cv, word, x0, y0, t_slams, t, alpha=1.0, glint=None, dy=0.0, amp=0.32):
        at = self.block
        for it, ts in zip(word["items"], t_slams):
            u = t - ts
            if u < 0:
                continue
            s = damped_overshoot(u / 0.34, amp=amp, freq=9.0, damp=9.0)
            a = float(smoothstep(u / 0.05)) * alpha
            boost = 1 + 1.3 * np.exp(-u / 0.11)
            img = it["img"]
            if glint is not None:
                img = img * glint(x0 + it["x"] * at.cw, y0, img.shape)
            h, w = img.shape[:2]
            cx = x0 + it["x"] * at.cw + w / 2
            cy = y0 + h / 2 + dy
            if abs(s - 1) > 0.002:
                sc = resize_layer(img, s, Image.BILINEAR)
                add_layer(cv, sc, cx - sc.shape[1] / 2, cy - sc.shape[0] / 2, a * boost)
            else:
                add_layer(cv, img, cx - w / 2, cy - h / 2, a * boost)

    def draw_hero(self, cv, t):
        hp = self.hero
        breath = -2.0 * np.sin(2 * np.pi * (t - 14.0) / 4.0) * float(smoothstep(prog(t, 14.0, 14.8)))
        lay = self.portrait_layer(hp, self.hero_glow, t)
        add_layer(cv, lay, P_X, P_Y + breath)
        hx, hy = hp.headphone_px
        self.headphone_rings(cv, t, P_X + hx, P_Y + breath + hy)
        # block letters, each slamming in on its beat
        out = fade(t, 0, 0, 22.0, 22.3)

        def glint(x, y, shape):
            if t < 18.0 or t > 19.2:
                return 1.0
            h, w = shape[:2]
            xx = x + np.arange(w)[None, :] + 0.45 * (y + np.arange(h)[:, None])
            bw = self.letters["width"] * self.block.cw
            xb = self.blocks_x - 260 + (bw + 600) * float(ease_in_out_cubic(prog(t, 18.0, 19.0)))
            return (1 + 0.9 * np.exp(-((xx - xb) / 46.0) ** 2))[..., None]

        self.draw_block_word(cv, self.letters, self.blocks_x, self.blocks_y, [14.0, 14.5, 15.0, 15.5], t,
                             alpha=out, glint=glint, dy=-14 * float(ease_in_cubic(prog(t, 22.0, 22.3))))
        # subtitle types at 16.0
        if t >= 16.0:
            at = self.sub
            bw = self.letters["width"] * self.block.cw
            x = self.blocks_x + bw // 2 - len(SUBTITLE) * at.cw // 2
            y = self.blocks_y + 6 * self.block.ch + 34
            n = self.typed(cv, at, SUBTITLE, x, y, CREAM * 0.80, t, 16.0, 0.62, flash=MINT, alpha=out)
            if t < 18.0:
                on = 1.0 if n < len(SUBTITLE) else float(1 - smoothstep(prog(t % BEAT, 0.24, 0.30)))
                on *= 1 - float(smoothstep(prog(t, 17.6, 18.0)))
                self.text(cv, at, "▌", x + n * at.cw, y, MINT * 0.9, on * out)

    # ===================================================================================== 4 HUD
    PANEL = (96, 354, 0.36)        # destination top-left + scale of the small portrait panel

    def draw_portrait_panel(self, cv, t):
        hp = self.hero
        e = float(ease_in_out_cubic(prog(t, 22.0, 22.5)))
        px, py, ps = self.PANEL
        s = lerp(1.0, ps, e)
        x = lerp(P_X, px, e)
        breath = -2.0 * np.sin(2 * np.pi * (t - 14.0) / 4.0) * (1 - e) - 1.0 * np.sin(2 * np.pi * (t - 14.0) / 4.0) * e
        y = lerp(P_Y, py, e) + breath
        out = 1 - float(smoothstep(prog(t, 34.0, 34.35)))
        lay = self.portrait_layer(hp, self.hero_glow, t)
        sc = resize_layer(lay, s)
        add_layer(cv, sc, x, y, out)
        hx, hy = hp.headphone_px
        self.headphone_rings(cv, t, x + hx * s, y + hy * s, scale=s * 1.4, gain=0.8 * out)
        # frame + label
        fa = fade(t, 22.35, 22.9) * out
        if fa > 0.01:
            fw, fh = int(hp.W * ps) + 36, int(hp.H * ps) + 36
            fx, fy = px - 18, py - 18
            self.frame_box(cv, fx, fy, fw, fh, DIM * 1.35, fa, draw=float(ease_out_cubic(prog(t, 22.3, 23.0))))
            self.text(cv, self.caps, "HEVI", fx, fy + fh + 14, CREAM * 0.85, fa)
            dot = 0.35 + 0.65 * beat_pulse(t, 0.2)
            self.text(cv, self.small, "●", fx + fw - 9, fy + fh + 17, MINT * dot, fa)

    def frame_box(self, cv, x, y, w, h, col, alpha, draw=1.0):
        """Thin corner-bracket frame from box-drawing glyphs (corners grow along the edges as `draw`→1)."""
        at = self.hud
        L = int(26 + (min(w, h) * 0.5 - 26) * draw)
        n = max(1, L // at.cw)
        for (cx, cy, hx, vy, corner) in ((x, y, 1, 1, "┌"), (x + w, y, -1, 1, "┐"),
                                         (x, y + h, 1, -1, "└"), (x + w, y + h, -1, -1, "┘")):
            xs = cx + hx * at.cw * (np.arange(1, n) + 0.0)
            ys = np.full(len(xs), cy)
            a = np.linspace(1, 0.15, len(xs)) * alpha
            splat(cv, at, xs, ys, np.full(len(xs), at.index["─"]), col[None] * a[:, None], subpixel=False)
            m = max(1, int(L / at.ch * 2))
            ys2 = cy + vy * at.ch * 0.5 * np.arange(1, m)
            xs2 = np.full(len(ys2), cx)
            a2 = np.linspace(1, 0.15, len(ys2)) * alpha
            splat(cv, at, xs2, ys2, np.full(len(ys2), at.index["│"]), col[None] * a2[:, None], subpixel=False)
            splat(cv, at, [cx], [cy], [at.index[corner]], [col * alpha], subpixel=False)

    def hud_header(self, cv, t, t0, num, name, out):
        a = fade(t, t0, t0 + 0.35) * out
        if a <= 0.005:
            return
        x, y = 560, 150
        self.text(cv, self.caps, num, x, y, DIM * 1.6, a)
        self.typed(cv, self.caps, name, x + 3 * self.caps.cw, y, CREAM * 0.92, t, t0, 0.25, alpha=out)
        # rule
        x1 = x + (4 + len(name)) * self.caps.cw
        x2 = 1830
        k = float(ease_out_expo(prog(t, t0 + 0.05, t0 + 0.7)))
        n = int((x2 - x1) * k / self.hud.cw)
        if n > 0:
            xs = x1 + self.hud.cw * (np.arange(n) + 0.5)
            splat(cv, self.hud, xs, np.full(n, y + 12.0), np.full(n, self.hud.index["─"]),
                  np.tile(DIM * 0.9 * a, (n, 1)), subpixel=False)
        # panel progress dots
        for i in range(3):
            on = (i == int(num) - 1)
            self.text(cv, self.small, "●" if on else "○", 1790 + i * 14 - 28, y + 40,
                      (MINT if on else DIM * 1.2) * a)

    # -------------------------------------------------- (a) whales
    def _build_whales(self, rng):
        n = 200
        k = np.arange(n) + 0.5
        phi = np.arccos(1 - 2 * k / n)
        th = np.pi * (1 + 5 ** 0.5) * k
        pts = np.stack([np.cos(th) * np.sin(phi), np.cos(phi), np.sin(th) * np.sin(phi)], 1)
        pts += rng.normal(0, 0.05, pts.shape)
        pts /= np.linalg.norm(pts, axis=1, keepdims=True)
        pts *= rng.uniform(0.86, 1.0, (n, 1))
        self.wh_pts = pts
        side = np.zeros(n, int)
        side[rng.choice(n, 59, replace=False)] = 1          # 59 long / 141 short -> L/S 0.42
        self.wh_long = side
        self.wh_size = rng.random(n)
        # edges: 2 nearest neighbours
        d = np.linalg.norm(pts[:, None] - pts[None], axis=2)
        np.fill_diagonal(d, 9)
        nn = np.argsort(d, 1)[:, :2]
        edges = set()
        for i in range(n):
            for j in nn[i]:
                edges.add((min(i, j), max(i, j)))
        self.wh_edges = np.array(sorted(edges))
        self.wh_order = rng.permutation(n)
        self.wh_ping = rng.permutation(n)

    def draw_whales(self, cv, t):
        out = 1 - float(smoothstep(prog(t, 26.0, 26.3)))
        if out <= 0:
            return
        self.hud_header(cv, t, 22.3, "01", "WHALES", out)
        cx, cy, R = 1130, 540, 290
        tt = t - 22.25
        ang = 0.32 * (tt - 0.6 * (1 - np.exp(-tt / 0.6))) + 0.4      # eased spin-up
        ca, sa = np.cos(ang), np.sin(ang)
        tilt = 0.38
        p = self.wh_pts
        x = p[:, 0] * ca + p[:, 2] * sa
        z = -p[:, 0] * sa + p[:, 2] * ca
        y = p[:, 1] * np.cos(tilt) - z * np.sin(tilt)
        z = p[:, 1] * np.sin(tilt) + z * np.cos(tilt)
        f = 1 / (1 - 0.22 * z)
        sx = cx + R * x * f
        sy = cy + R * y * f
        depth = (z + 1) / 2
        # nodes pop in
        order_t = 22.4 + 1.0 * (np.argsort(self.wh_order) / 200.0)
        age = t - order_t
        vis = np.clip(age / 0.12, 0, 1)
        # edges (dotted, dim), drawn in progressively
        ek = float(ease_out_cubic(prog(t, 22.8, 24.0)))
        if ek > 0:
            e = self.wh_edges
            a, b = e[:, 0], e[:, 1]
            L = np.hypot(sx[b] - sx[a], sy[b] - sy[a])
            m = np.maximum((L / 7).astype(int), 1)
            tot = int(m.sum())
            seg = np.repeat(np.arange(len(e)), m)
            within = np.arange(tot) - np.repeat(np.cumsum(m) - m, m)
            u = (within + 0.5) / np.repeat(m, m)
            keep = u <= ek
            seg, u = seg[keep], u[keep]
            px = sx[a[seg]] + (sx[b[seg]] - sx[a[seg]]) * u
            py = sy[a[seg]] + (sy[b[seg]] - sy[a[seg]]) * u
            dd = (depth[a[seg]] + depth[b[seg]]) / 2
            al = (0.06 + 0.22 * dd ** 2) * out * np.minimum(vis[a[seg]], vis[b[seg]])
            splat(cv, self.small, px, py, np.full(len(px), self.small.index["·"]),
                  (TEAL * 0.9)[None] * al[:, None])
        # nodes
        gl = np.where(depth > 0.62, self.hud.index["●"], np.where(depth > 0.3, self.hud.index["•"], self.hud.index["·"]))
        big = self.wh_size > 0.86
        gl = np.where(big & (depth > 0.45), self.hud.index["●"], gl)
        col = np.where(self.wh_long[:, None] == 1, MINT[None], RED[None] * 0.95)
        bri = (0.22 + 0.85 * depth ** 1.4) * vis * out
        flash = np.exp(-np.maximum(age, 0) / 0.15) * (age > 0)
        c = col * bri[:, None] + CREAM[None] * (flash * 0.8 * out)[:, None]
        # beat pings: a front node rings on every beat
        if t >= 23.0:
            bi = int(np.floor(t / BEAT))
            u = (t % BEAT) / BEAT
            front = np.argsort(-depth)[:40]
            node = front[self.wh_ping[bi % 200] % 40]
            c[node] += CREAM * 0.9 * (1 - u) ** 2 * out
            rr = 6 + 30 * float(ease_out_cubic(u))
            nn = 24
            aa = np.linspace(0, 2 * np.pi, nn, endpoint=False)
            col_r = (MINT if self.wh_long[node] else RED) * 0.55 * (1 - u) ** 2 * out
            splat(cv, self.small, sx[node] + rr * np.cos(aa), sy[node] + rr * np.sin(aa),
                  np.full(nn, self.small.index["·"]), np.tile(col_r, (nn, 1)))
        order = np.argsort(depth)
        splat(cv, self.hud, sx[order], sy[order], gl[order], c[order])
        # label + L/S bar + legend
        la = out
        lab = "200 most profitable traders · L/S 0.42"
        cols = np.tile(CREAM * 0.86, (len(lab), 1))
        k = lab.index("L/S")
        cols[k:] = MINT
        x0 = cx - len(lab) * self.hud.cw // 2
        self.typed(cv, self.hud, lab, x0, 862, cols, t, 22.7, 0.55, alpha=la)
        bk = float(ease_out_expo(prog(t, 23.3, 24.1)))
        nb = int(56 * bk)
        if nb > 0:
            nl = int(round(56 * 59 / 200))
            xs = x0 + 4 + self.hud.cw * (np.arange(nb) + 0.5) * (len(lab) * self.hud.cw - 8) / (56 * self.hud.cw)
            cc = np.where((np.arange(nb) < nl)[:, None], MINT[None] * 0.9, RED[None] * 0.8) * la
            splat(cv, self.hud, xs, np.full(nb, 905.0), np.full(nb, self.hud.index["━"]), cc, subpixel=False)
        lg = fade(t, 23.4, 23.9) * out
        if lg > 0:
            self.text(cv, self.hud, "●", 1530, 440, MINT * lg)
            self.text(cv, self.hud, "long", 1530 + 2 * self.hud.cw, 440, CREAM * 0.75 * lg)
            self.text(cv, self.hud, "●", 1530, 474, RED * lg)
            self.text(cv, self.hud, "short", 1530 + 2 * self.hud.cw, 474, CREAM * 0.75 * lg)

    # -------------------------------------------------- (b) flow
    def _build_flow(self, rng):
        n = 9
        self.fl_side = np.array([1, 0, 1, 1, 0, 1, 0, 1, 1])
        self.fl_val = np.array([240, 130, 410, 180, 155, 620, 210, 120, 330])
        xs = np.linspace(0, 1, 400)
        oi = (0.30 + 0.42 * xs + 0.06 * np.sin(xs * 9.0) + 0.035 * np.sin(xs * 23.0 + 1.0)
              + 0.015 * np.sin(xs * 57.0))
        self.fl_oi = oi

    def draw_flow(self, cv, t):
        if t < 25.9:
            return
        out = fade(t, 26.0, 26.3, 30.0, 30.3)
        if out <= 0:
            return
        self.hud_header(cv, t, 26.1, "02", "FLOW", out)
        hud = self.hud
        x0 = 560
        # buy / sell pressure bars (segmented, breathing)
        grow = float(ease_out_expo(prog(t, 26.2, 26.9)))
        tt = t - 26.0
        buy = (0.60 + 0.05 * np.sin(2 * np.pi * tt / 2.0 + 0.4) + 0.015 * np.sin(2 * np.pi * tt / 0.5)) * grow
        sell = (0.40 - 0.04 * np.sin(2 * np.pi * tt / 2.0 + 0.4) + 0.012 * np.sin(2 * np.pi * tt / 0.5 + 1)) * grow
        maxw = 1100
        for row, (lab, v, col) in enumerate((("buy", buy, GREEN), ("sell", sell, RED))):
            y = 248 + row * 42
            self.text(cv, hud, lab, x0, y, CREAM * 0.8 * out)
            n = maxw / hud.cw
            m = int(n * v)
            fracp = n * v - m
            xs = x0 + 100 + hud.cw * (np.arange(m + 1) + 0.5)
            a = np.ones(m + 1)
            a[-1] = fracp
            a *= np.linspace(0.75, 1.0, m + 1)
            splat(cv, hud, xs, np.full(m + 1, y + 12.0), np.full(m + 1, hud.index["▌"]),
                  col[None] * 0.85 * a[:, None] * out, subpixel=False)
        # big orders tape
        self.typed(cv, hud, "big orders > $100k", x0, 400, CREAM * 0.86, t, 26.35, 0.35, alpha=out)
        ty = 470
        nseg = int((1830 - x0) / hud.cw)
        xs = x0 + hud.cw * (np.arange(nseg) + 0.5)
        splat(cv, hud, xs, np.full(nseg, float(ty)), np.full(nseg, hud.index["─"]),
              np.tile(DIM * 0.55 * out, (nseg, 1)), subpixel=False)
        beats = [26.5 + 0.5 * i for i in range(9)]
        for i, tb in enumerate(beats):
            if t < tb:
                continue
            age = t - tb
            # each new order enters at the right; older ones glide left one slot per beat
            pos_slot = sum(float(ease_out_cubic(prog(t, b, b + 0.3))) for b in beats if tb < b <= t)
            xb = 1760 - 132 * pos_slot
            side = self.fl_side[i]
            v = self.fl_val[i]
            colb = GREEN if side else RED
            pop = damped_overshoot(age / 0.3, 0.5)
            glyph = "●" if v >= 200 else "•"
            yb = ty - 26 if side else ty + 26
            a = float(smoothstep(age / 0.06)) * out
            splat(cv, hud, [xb], [yb], [hud.index[glyph]], [colb * a * (1 + 1.5 * np.exp(-age / 0.12))])
            lbl = f"${v}k"
            self.text(cv, self.small, lbl, round(xb - len(lbl) * self.small.cw / 2),
                      yb + (-36 if side else 14), (colb * 0.55 + CREAM * 0.25) * a)
            if pop > 1.02:
                rr = 8 + 26 * float(ease_out_cubic(age / 0.3))
                nn = 18
                aa = np.linspace(0, 2 * np.pi, nn, endpoint=False)
                splat(cv, self.small, xb + rr * np.cos(aa), yb + rr * np.sin(aa), np.full(nn, self.small.index["·"]),
                      np.tile(colb * 0.5 * (1 - age / 0.3) * out, (nn, 1)))
        # open interest line drawing left -> right
        self.typed(cv, hud, "open interest", x0, 590, CREAM * 0.86, t, 26.5, 0.3, alpha=out)
        cx0, cx1, cy0, cy1 = x0, 1830, 890, 660
        k = float(ease_in_out_cubic(prog(t, 26.6, 29.6)))
        n = len(self.fl_oi)
        m = int(n * k)
        if m > 1:
            xs = cx0 + (cx1 - cx0) * np.arange(m) / (n - 1)
            ys = cy0 + (cy1 - cy0) * (self.fl_oi[:m] - 0.22) / 0.62
            splat(cv, self.small, xs, ys, np.full(m, self.small.index["•"]), np.tile(MINT * 0.55 * out, (m, 1)))
            # faint fill below the line (sparse grid of dots)
            gx = np.arange(cx0, xs[-1], 22.0)
            for i, xg in enumerate(gx):
                yl = cy0 + (cy1 - cy0) * (np.interp(xg, xs, self.fl_oi[:m]) - 0.22) / 0.62
                yy = np.arange(yl + 14, cy0, 18.0)
                if len(yy):
                    al = 0.20 * out * np.linspace(1, 0.25, len(yy))
                    splat(cv, self.small, np.full(len(yy), xg), yy, np.full(len(yy), self.small.index["·"]),
                          (TEAL[None] * al[:, None]), subpixel=False)
            splat(cv, self.hud, [xs[-1]], [ys[-1]], [self.hud.index["●"]], [MINT * 1.2 * out])
        nb = int((cx1 - cx0) / hud.cw)
        splat(cv, hud, cx0 + hud.cw * (np.arange(nb) + 0.5), np.full(nb, cy0 + 16.0), np.full(nb, hud.index["─"]),
              np.tile(DIM * 0.6 * out * fade(t, 26.5, 27.0), (nb, 1)), subpixel=False)

    # -------------------------------------------------- (c) funnel
    def _build_funnel(self, rng):
        n = len(COINS)
        sol = COINS.index("SOL")
        order = [i for i in rng.permutation(n) if i != sol]
        s9 = [sol] + order[:8]
        s3 = [sol] + order[:2]
        self.fu_stage = np.zeros(n, int)          # stage at which the coin is rejected (1,2,3) ; 4 = survivor
        for i in range(n):
            self.fu_stage[i] = 4 if i == sol else (3 if i in s3 else (2 if i in s9 else 1))
        self.fu_delay = rng.uniform(0, 0.32, n)
        self.fu_sol = sol
        # radar blips (polar positions) for each coin
        self.fu_ang = rng.uniform(0, 2 * np.pi, n)
        self.fu_rad = rng.uniform(0.18, 0.95, n)
        self.fu_rad[sol] = 0.55

    def draw_funnel(self, cv, t):
        if t < 29.9:
            return
        out = fade(t, 30.0, 30.3, 34.0, 34.3)
        if out <= 0:
            return
        self.hud_header(cv, t, 30.1, "03", "FUNNEL", out)
        hud = self.hud
        # radar
        rcx, rcy, rR = 760, 560, 210
        ra = fade(t, 30.1, 30.6) * out
        beat_i = np.floor((t - 30.0) / BEAT)
        frac = ((t - 30.0) % BEAT) / BEAT
        sweep = (beat_i + float(ease_in_out_sine(frac))) * (np.pi / 2) - np.pi / 2
        # rings & crosshair
        for rr in (0.36, 0.68, 1.0):
            nn = int(2 * np.pi * rR * rr / 11)
            aa = np.linspace(0, 2 * np.pi, nn, endpoint=False)
            splat(cv, self.small, rcx + rR * rr * np.cos(aa), rcy + rR * rr * np.sin(aa),
                  np.full(nn, self.small.index["·"]), np.tile(DIM * 0.9 * ra, (nn, 1)))
        for ang in (0, np.pi / 2):
            ll = np.linspace(-rR, rR, 2 * int(rR / 14) + 1)
            splat(cv, self.small, rcx + ll * np.cos(ang), rcy + ll * np.sin(ang), np.full(len(ll), self.small.index["·"]),
                  np.tile(DIM * 0.55 * ra, (len(ll), 1)))
        # sweep wedge on the fine grid
        gx0, gy0 = (rcx - rR) // 6 * 6, (rcy - rR) // 12 * 12
        nr, nc = int(2 * rR / 12) + 2, int(2 * rR / 6) + 2
        gy, gx = np.mgrid[0:nr, 0:nc]
        px = gx0 + (gx + 0.5) * 6 - rcx
        py = gy0 + (gy + 0.5) * 12 - rcy
        rad = np.hypot(px, py) / rR
        ang = np.arctan2(py, px)
        d = (sweep - ang) % (2 * np.pi)
        trail = np.exp(-d / 0.55) * (rad < 1.0) * (rad > 0.03)
        lvl = np.clip((trail * 5).astype(int), 0, 4)
        chars = self.fine.idx(" .:+*")
        idx = np.where(trail > 0.04, chars[lvl], 0)
        col = (MINT[None, None] * 0.75 * trail[..., None] + CREAM[None, None] * 0.5 * (trail > 0.93)[..., None]) * ra
        rows, cols_ = np.nonzero(idx)
        compose_sparse(cv, self.fine, rows + gy0 // 12, cols_ + gx0 // 6, idx[rows, cols_], col[rows, cols_])
        # coin list
        lx, ly = 1040, 300
        cw_, rh = 196, 38
        n = len(COINS)
        stage_t = {1: 31.0, 2: 32.0, 3: 33.0}
        for i, name in enumerate(COINS):
            r, c = i // 4, i % 4
            x = lx + c * cw_
            y = ly + r * rh
            appear = 30.2 + 0.012 * i
            if t < appear:
                continue
            a = float(smoothstep((t - appear) / 0.12)) * out
            st = self.fu_stage[i]
            col = CREAM * 0.78
            dim = 1.0
            if st <= 3:
                tr = stage_t[st] + self.fu_delay[i] * 0.8
                if t >= tr:
                    u = t - tr
                    dim = 0.16 + 0.84 * np.exp(-u / 0.18)
                    col = lerp(RED * 0.9, CREAM * 0.5, float(smoothstep(u / 0.25)))
            # survivors brighten as the funnel narrows
            if st >= 2 and t >= 31.1:
                col = lerp(col, MINT * 0.95, 0.5 * fade(t, 31.1, 31.4))
            if i == self.fu_sol and t >= 33.0:
                g = fade(t, 33.0, 33.25)
                col = lerp(col, MINT * 1.25, g) + CREAM * 0.8 * np.exp(-(t - 33.0) / 0.2)
            self.text(cv, hud, name, x, y, col * dim * a)
            # radar blip
            if st >= 2 or t < stage_t[1] + 0.4:
                bx = rcx + rR * self.fu_rad[i] * np.cos(self.fu_ang[i])
                by = rcy + rR * self.fu_rad[i] * np.sin(self.fu_ang[i])
                dd = (sweep - self.fu_ang[i]) % (2 * np.pi)
                lit = np.exp(-dd / 0.6)
                ba = (0.25 + 0.9 * lit) * ra * (dim if st <= 3 else 1.0)
                bcol = MINT if i == self.fu_sol and t >= 33.0 else (TEAL if st >= 2 else DIM * 1.5)
                splat(cv, self.small, [bx], [by], [self.small.index["•" if st < 4 else "●"]], [bcol * ba])
        if t >= 33.0:
            # SOL survivor: bracket glow
            i = self.fu_sol
            r, c = i // 4, i % 4
            x = lx + c * cw_
            y = ly + r * rh
            g = fade(t, 33.0, 33.3) * out
            self.text(cv, hud, "[", x - 2 * hud.cw, y, MINT * 0.8 * g)
            self.text(cv, hud, "]", x + 4 * hud.cw, y, MINT * 0.8 * g)
        # counter: 48 coins → 9 → 3 → 1
        parts = [("48 coins", 30.25), (" → 9", 31.0), (" → 3", 32.0), (" → 1", 33.0)]
        x = lx
        y = ly + 12 * rh + 40
        for s, ts in parts:
            if t >= ts:
                age = t - ts
                cols = np.tile(CREAM * 0.88, (len(s), 1))
                for j, ch in enumerate(s):
                    if ch.isdigit():
                        cols[j] = MINT
                self.typed(cv, hud, s, x, y, cols, t, ts, 0.12, alpha=out)
            x += len(s) * hud.cw

    # ===================================================================================== 5 TRADE
    def _build_candle_atlas(self):
        custom = {}
        self.cbody = {}
        self.cwick = {}

        def mk_body(y0, y1):
            def f(cw, ch, ss):
                a = np.zeros((ch * ss, cw * ss), np.float32)
                a[int(y0 * ch * ss / 8):int(y1 * ch * ss / 8), ss * 1:cw * ss - ss * 1] = 1
                return a
            return f

        def mk_wick(y0, y1):
            def f(cw, ch, ss):
                a = np.zeros((ch * ss, cw * ss), np.float32)
                c = cw * ss // 2
                a[int(y0 * ch * ss / 8):int(y1 * ch * ss / 8), c - int(0.8 * ss):c + int(0.8 * ss)] = 1
                return a
            return f

        code = 0xE000
        for y0 in range(8):
            for y1 in range(y0 + 1, 9):
                custom[chr(code)] = mk_body(y0, y1)
                self.cbody[(y0, y1)] = chr(code)
                code += 1
                custom[chr(code)] = mk_wick(y0, y1)
                self.cwick[(y0, y1)] = chr(code)
                code += 1
        self.candle = GlyphAtlas("", 12, 24, 14, custom=custom)
        self.cline = GlyphAtlas("─╌·", 10, 20, 14, proc="─╌", proc_weight=1.1)

    def _build_chart(self, rng):
        self.ch_x0, self.ch_y0 = 144, 120               # grid origin (multiple of the 12x24 cell)
        self.ch_pitch = 24
        self.p_lo, self.p_hi = 96.8, 107.2
        self.y_lo, self.y_hi = 930.0, 150.0
        self.entry, self.sl, self.tp = 99.61, 99.61 * (1 - 0.015), 99.61 * (1 + 0.062)
        # history candles (32) ending at the entry
        n = 32
        base = np.concatenate([np.linspace(101.6, 100.4, 8), np.linspace(100.4, 98.95, 10),
                               np.linspace(98.95, 100.9, 8), np.linspace(100.9, 99.70, 6)])
        noise = rng.normal(0, 0.16, n)
        closes = base + noise
        closes[-1] = 99.70
        opens = np.concatenate([[101.9], closes[:-1]])
        highs = np.maximum(opens, closes) + rng.uniform(0.05, 0.38, n)
        lows = np.minimum(opens, closes) - rng.uniform(0.05, 0.38, n)
        lows = np.maximum(lows, 98.80)
        lows[17] = 98.82                                 # the 4H structure low (SL sits beyond it)
        self.hist = np.stack([opens, highs, lows, closes], 1)
        # live price path 36 -> 44 (keyframes + smooth noise), touches TP exactly at 44.0
        kt = np.array([36.0, 36.6, 37.3, 38.0, 38.7, 39.4, 40.0, 40.6, 41.2, 41.7, 42.0, 42.5, 43.0, 43.5, 44.0])
        kp = np.array([99.61, 99.98, 99.72, 100.18, 99.83, 99.58, 99.32, 98.88, 98.57, 98.36, 98.47, 99.45,
                       101.05, 103.35, self.tp])
        self.kt, self.kp = kt, kp
        self.live_t0 = 36.0
        self.live_dt = 1.0 / 3.0
        self.n_live = 24
        ts = np.linspace(36.0, 44.0, 24 * 80 + 1)
        self.path_t = ts
        self.path_p = self._path(ts)
        self.wick_ext = rng.uniform(0.0, 0.10, (self.n_live, 2))

    def _path(self, t):
        t = np.asarray(t, float)
        # monotone-ish cubic through keyframes (Catmull-Rom on uniform parameter)
        kt, kp = self.kt, self.kp
        i = np.clip(np.searchsorted(kt, t) - 1, 0, len(kt) - 2)
        u = (t - kt[i]) / (kt[i + 1] - kt[i])
        p0 = kp[np.maximum(i - 1, 0)]
        p1 = kp[i]
        p2 = kp[i + 1]
        p3 = kp[np.minimum(i + 2, len(kp) - 1)]
        m1 = (p2 - p0) / 2 * 0.8
        m2 = (p3 - p1) / 2 * 0.8
        h00, h10, h01, h11 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u, -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
        p = h00 * p1 + h10 * m1 + h01 * p2 + h11 * m2
        env = np.clip((t - 36.0) / 0.3, 0, 1) * np.clip((43.6 - t) / 0.6, 0, 1)
        p = p + env * (0.07 * np.sin(t * 13.1) + 0.05 * np.sin(t * 29.3 + 1.0) + 0.03 * np.sin(t * 61.0))
        return p

    def price_y(self, p):
        return self.y_lo + (np.asarray(p) - self.p_lo) / (self.p_hi - self.p_lo) * (self.y_hi - self.y_lo)

    def live_candles(self, t):
        """OHLC of live candles visible at t (the last one still forming)."""
        out = []
        tt = min(t, 44.0)
        for k in range(self.n_live):
            a = self.live_t0 + k * self.live_dt
            if tt < a:
                break
            b = min(a + self.live_dt, tt)
            m = (self.path_t >= a) & (self.path_t <= b)
            seg = self.path_p[m]
            pe = float(self._path(b))
            seg = np.append(seg, pe)
            o = float(self._path(a))
            c = pe
            hi, lo = float(seg.max()), float(seg.min())
            done = (b >= a + self.live_dt - 1e-9)
            if done:
                hi = min(hi + self.wick_ext[k, 0], self.tp - 0.04) if k < self.n_live - 1 else hi
                lo = max(lo - self.wick_ext[k, 1], 98.22)
            if k == self.n_live - 1 and t >= 44.0:
                hi = self.tp
            out.append((o, hi, lo, c))
        return out

    def draw_candles(self, cv, candles, first_slot, alpha_fn, dim=1.0):
        cw, ch = self.candle.cw, self.candle.ch
        rows, cols, idx, col = [], [], [], []
        for k, (o, h, l, c) in enumerate(candles):
            slot = first_slot + k
            a = alpha_fn(slot)
            if a <= 0.003:
                continue
            gc = (self.ch_x0 + slot * self.ch_pitch) // cw - self.ch_x0 // cw
            up = c >= o
            colr = (GREEN * 0.92 if up else RED * 0.92) * a * dim
            yb0, yb1 = sorted((float(self.price_y(o)), float(self.price_y(c))))
            if yb1 - yb0 < 2.5:
                yb1 = yb0 + 2.5
            yw0, yw1 = float(self.price_y(h)), float(self.price_y(l))
            cells = {}
            for (y0, y1, kind) in ((yw0, yw1, "w"), (yb0, yb1, "b")):
                r0 = int(np.floor((y0 - self.ch_y0) / ch))
                r1 = int(np.floor((y1 - self.ch_y0 - 1e-6) / ch))
                for r in range(r0, r1 + 1):
                    top = self.ch_y0 + r * ch
                    e0 = int(np.clip(round((max(y0, top) - top) / ch * 8), 0, 7))
                    e1 = int(np.clip(round((min(y1, top + ch) - top) / ch * 8), e0 + 1, 8))
                    g = (self.cbody if kind == "b" else self.cwick)[(e0, e1)]
                    cells[r] = self.candle.index[g]        # body (drawn second) wins over the wick
            for r, gi in cells.items():
                rows.append(r + self.ch_y0 // ch)
                cols.append(gc + self.ch_x0 // cw)
                idx.append(gi)
                col.append(colr)
        if rows:
            # body and wick can share a cell: the body glyph wins (it covers the wick's middle)
            compose_sparse(cv, self.candle, rows, cols, np.array(idx), np.array(col))

    def hline(self, cv, y, x0, x1, col, glyph="─", k=1.0):
        at = self.cline
        n = int((x1 - x0) * k / at.cw)
        if n <= 0:
            return
        xs = x0 + at.cw * (np.arange(n) + 0.5)
        splat(cv, at, xs, np.full(n, round(y) + 0.0), np.full(n, at.index[glyph]), np.tile(col, (n, 1)), subpixel=False)

    def draw_trade(self, cv, t):
        if t < 33.95 or t >= 46.4:
            return
        out = 1 - float(smoothstep(prog(t, 46.0, 46.35)))
        # tension 40-42: everything dims, heartbeat on the half-time kick
        tens = fade(t, 40.0, 40.35, 42.0, 42.5)
        hb = 0.0
        for tb in (40.0, 41.0):
            for off, amp in ((0.0, 1.0), (0.22, 0.6)):
                d = t - (tb + off)
                if d >= 0:
                    hb += amp * np.exp(-d / 0.11)
        hb *= tens
        post_hit = float(smoothstep(prog(t, 44.15, 44.6)))
        dim_all = (1 - 0.6 * tens) * (1 - 0.78 * post_hit) * out
        x_right = self.ch_x0 + (32 + self.n_live) * self.ch_pitch + 30
        # grid + price labels
        ga = fade(t, 34.0, 34.6) * dim_all
        for p in range(97, 108):
            y = float(self.price_y(p))
            k = float(ease_out_expo(prog(t, 34.0 + 0.03 * (p - 97), 34.8 + 0.03 * (p - 97))))
            n = int((x_right - self.ch_x0) * k / 16)
            if n > 0:
                xs = self.ch_x0 + 16 * np.arange(n) + 8
                splat(cv, self.small, xs, np.full(n, y), np.full(n, self.small.index["·"]),
                      np.tile(DIM * 0.55 * ga, (n, 1)), subpixel=False)
            self.text(cv, self.small, f"{p:.2f}", 52, round(y - 9), DIM * 1.3 * ga)
        # history candles draw left -> right 34.1 - 35.9
        rx = 32 * float(ease_in_out_cubic(prog(t, 34.1, 35.9)))

        def hist_alpha(slot):
            return float(np.clip(rx - slot, 0, 1))

        self.draw_candles(cv, self.hist, 0, hist_alpha, dim=dim_all * (0.78 if t >= 36 else 1.0))
        live = self.live_candles(t) if t >= 36.0 else []
        live_dim = (1 - 0.25 * tens) * (1 - 0.7 * post_hit) * out
        self.draw_candles(cv, live, 32, lambda s: 1.0, dim=live_dim)
        # setup lines lock at 36.0
        lab_x = 1828
        specs = [(self.entry, BLUE, "─", "ENTRY 99.61 · post-only", 36.0),
                 (self.sl, RED, "╌", "SL −1.50%", 36.08),
                 (self.tp, GREEN, "╌", "TP +6.2% · net 1:3.4", 36.16)]
        for p, colr, g, lab, t0 in specs:
            if t < t0:
                continue
            y = float(self.price_y(p))
            k = float(ease_out_expo(prog(t, t0, t0 + 0.45)))
            lock = np.exp(-(t - t0) / 0.2)
            gain = 1.0
            if colr is RED:
                gain = (1 - 0.6 * tens) + 1.0 * tens + 1.6 * hb
                gain *= (1 - 0.6 * post_hit)
            elif colr is GREEN:
                gain = (1 - 0.6 * tens) * (1 + 0.8 * float(smoothstep(prog(t, 42.0, 44.0))))
                if t >= 44.0:
                    gain *= 1 + 2.0 * np.exp(-(t - 44.0) / 0.3)
                gain *= (1 - 0.45 * post_hit)
            else:
                gain = (1 - 0.6 * tens) * (1 - 0.6 * post_hit)
            c = colr * 0.7 * gain * out + CREAM * 0.6 * lock * out
            x_from = self.ch_x0
            self.hline(cv, y, x_from, lab_x, c, g, k)
            # label right-aligned above the line
            lw = len(lab) * self.hud.cw
            cols = np.tile(colr * 0.95, (len(lab), 1))
            cols[len(lab.split()[0]):] = CREAM * 0.8
            self.typed(cv, self.hud, lab, lab_x - lw, round(y) - 30, cols * gain, t, t0 + 0.12, 0.3,
                       flash=colr, alpha=out)
        if t >= 36.0:
            ye = float(self.price_y(self.entry))
            xe = self.ch_x0 + 32 * self.ch_pitch + 5
            pop = damped_overshoot((t - 36.0) / 0.3, 0.6)
            a = (1 - 0.6 * tens) * (1 - 0.6 * post_hit) * out
            splat(cv, self.hud, [xe], [ye], [self.hud.index["◆"]], [BLUE * a * pop])
        # live price tag following the head
        if live:
            k = len(live) - 1
            pnow = live[-1][3] if t < 44.0 else self.tp
            y = float(self.price_y(pnow))
            x = self.ch_x0 + (32 + k) * self.ch_pitch + 22
            above = pnow >= self.entry
            colr = GREEN if above else RED
            ta = out * (1 - 0.5 * post_hit)
            self.text(cv, self.hud, "◀", x, round(y - 12), colr * ta)
            self.text(cv, self.hud, f"{pnow:.2f}", x + 14, round(y - 12), CREAM * 0.92 * ta)
        # red heartbeat glow at the frame edges
        if hb > 0.01:
            cv += self.edge_glow * (RED * 0.10 * hb)[None, None, :]
        # 44.0: TP touched -> shockwave, +3.4R, fill line
        if t >= 44.0:
            ox = self.ch_x0 + (32 + self.n_live - 1) * self.ch_pitch + 5
            oy = float(self.price_y(self.tp))
            self.shockwave(cv, t, 44.0, ox, oy, out)
            items, width = self.r34["items"], self.r34["width"]
            bx = W // 2 - width * self.block.cw // 2
            self.draw_block_word(cv, self.r34, bx, 262, [44.0] * 5, t, alpha=out, amp=0.42)
            s = "TP filled · on-exchange · isolated"
            cols = np.tile(CREAM * 0.86, (len(s), 1))
            cols[:9] = GREEN
            x = W // 2 - len(s) * self.tpat.cw // 2
            self.typed(cv, self.tpat, s, x, 628, cols, t, 44.5, 0.5, alpha=out)

    def shockwave(self, cv, t, t0, ox, oy, out=1.0):
        u = (t - t0) / 1.5
        if u >= 1:
            return
        d = np.hypot(self.gcx - ox, self.gcy - oy)
        total = np.zeros_like(d)
        colw = np.zeros(d.shape + (3,), np.float32)
        for delay, amp, wid, col in ((0.0, 1.0, 1.0, MINT), (0.10, 0.55, 0.6, GREEN)):
            uu = (t - t0 - delay) / 1.5
            if uu <= 0 or uu >= 1:
                continue
            r = 2300 * float(ease_out_cubic(uu)) ** 0.9
            w = (14 + 120 * uu) * wid
            ring = np.exp(-((d - r) / w) ** 2) * (d < r + 2 * w) * amp * (1 - uu) ** 1.6
            # bright leading edge
            lead = np.exp(-((d - r - w * 0.6) / (w * 0.35)) ** 2) * amp * (1 - uu) ** 1.2
            total += ring
            colw += col[None, None] * ring[..., None] + CREAM[None, None] * 0.7 * lead[..., None]
        m = total > 0.03
        if not m.any():
            return
        fr = int(np.floor(t * 30))
        rnd = hash01(np.arange(GR)[:, None], np.arange(GC)[None, :], fr // 2)
        lvl = np.clip((total * 9).astype(int), 0, 8)
        idx = np.where(m, self.shock_set[np.clip(lvl + (rnd * 2).astype(int) - 1, 0, 8)], 0)
        rows, cols = np.nonzero(idx)
        compose_sparse(cv, self.fine, rows, cols, idx[rows, cols], colw[rows, cols] * out)

    # ===================================================================================== 6 DISCIPLINE
    def draw_discipline(self, cv, t):
        if t < 46.3 or t >= 52.05:
            return
        out = 1 - float(smoothstep(prog(t, 51.6, 52.0)))
        at = self.check
        n_max = max(len(s) for s in CHECKS) + 3
        x0 = (W - n_max * at.cw) // 2
        y0 = 300
        lh = 60
        for i, s in enumerate(CHECKS):
            tb = 46.5 + 0.5 * i
            if t < tb - 0.02:
                continue
            age = t - tb
            y = y0 + i * lh
            # ✓ pops
            sc = damped_overshoot(age / 0.32, amp=0.7, freq=10, damp=9)
            a = float(smoothstep(age / 0.04)) * out
            tile = self.checkbig.tiles[self.checkbig.index["✓"]]
            img = tile[..., None] * (GREEN * (1 + 1.4 * np.exp(-age / 0.12)) + CREAM * 0.6 * np.exp(-age / 0.08))[None, None]
            s2 = sc * 0.62
            im2 = resize_layer(img, s2, Image.BILINEAR)
            cx, cy = x0 + at.cw * 0.5 + 4, y + at.ch * 0.55
            add_layer(cv, im2, cx - im2.shape[1] / 2, cy - im2.shape[0] / 2, a)
            # text reveals left -> right with a soft per-char fade and slide
            n = len(s)
            ci = np.arange(n)
            ta = np.clip((age - 0.05 - ci * 0.006) / 0.12, 0, 1)
            vis = ta > 0.002
            if vis.any():
                xs = x0 + at.cw * (3 + ci + 0.5) + 8 * (1 - ta) ** 2
                ys = np.full(n, y + at.ch * 0.5)
                cols = (CREAM * 0.84)[None] * ta[:, None] * out
                gi = at.idx(s)
                keep = vis & (gi != 0)
                splat(cv, at, xs[keep], ys[keep], gi[keep], cols[keep])
        # 50.5 statement, tracking in
        if t >= 50.5:
            st = self.statement
            age = t - 50.5
            a = float(smoothstep(age / 0.6)) * out
            trk = st.cw + 7 * (1 - float(ease_out_expo(age / 1.0)))
            n = len(STATEMENT)
            cx = W / 2
            xs = cx + (np.arange(n) - (n - 1) / 2) * trk
            ys = np.full(n, y0 + 6 * lh + 92.0)
            cols = np.tile(CREAM * 0.95, (n, 1))
            k = STATEMENT.index("by design.")
            cols[k:] = MINT * 1.0
            gi = st.idx(STATEMENT)
            keep = gi != 0
            splat(cv, st, xs[keep], ys[keep], gi[keep], cols[keep] * a)

    # ===================================================================================== 7 FINALE
    def _build_finale(self, rng):
        fp = self.final
        self.f_face = fp.to_layer(372, 300)
        self.f_x = W / 2 - self.f_face[0]
        self.f_y = 452 - self.f_face[1]
        # fade the body out below the face so the titles sit on darkness
        ys = self.f_y + (np.arange(fp.R) + 0.5) * fp.ch
        fade_rows = (1 - smoothstep((ys - 560) / 230.0))[:, None]
        fp.light = (fp.light * fade_rows).astype(np.float32)
        self.final_glow = {m: glow_underlay(fp, getattr(fp, f"tint_{m}"), blur=5) for m in ("open", "half", "closed")}
        gy, gx = np.mgrid[0:fp.R, 0:fp.C].astype(np.float32)
        cx = (gx + 0.5) * fp.cw
        cy = (gy + 0.5) * fp.ch
        d = np.hypot(cx - self.f_face[0], (cy - self.f_face[1]) * 0.9)
        dn = d / d.max()
        self.f_lock = (52.0 + 0.95 * dn ** 0.85 + 0.06 * hash01(gy.astype(int), gx.astype(int), 11)).astype(np.float32)
        # dissolve: release times (outside first, the face last) and drift params
        self.f_rel = (58.0 + 0.95 * (1 - dn) ** 1.2 + 0.28 * hash01(gy.astype(int), gx.astype(int), 12)).astype(np.float32)
        ang = np.arctan2(cy - self.f_face[1], cx - self.f_face[0])
        self.f_vx = (np.cos(ang) * rng.uniform(20, 70, dn.shape) + rng.normal(0, 14, dn.shape)).astype(np.float32)
        self.f_vy = (np.sin(ang) * rng.uniform(10, 40, dn.shape) - rng.uniform(20, 60, dn.shape)).astype(np.float32)
        self.f_ph = rng.uniform(0, 2 * np.pi, dn.shape).astype(np.float32)
        self.f_cx, self.f_cy = cx, cy
        # edge glow mask (heartbeat)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        nx = (xx - W / 2) / (W / 2)
        ny = (yy - H / 2) / (H / 2)
        self.edge_glow = np.clip((nx * nx * 0.8 + ny * ny) - 0.45, 0, 1)[..., None].astype(np.float32) ** 1.5

    def draw_finale(self, cv, t):
        if t < 51.9:
            return
        fp = self.final
        mode = blink_mode(t)
        idx = getattr(fp, f"idx_{mode}").copy()
        col = getattr(fp, f"col_{mode}") * fp.light[..., None]
        pb = beat_pulse(t, 0.18) * (1 - fade(t, 57.5, 58.5))
        col = col * (1 + 0.6 * pb * fp.headphone[..., None]) + MINT * 0.5 * pb * fp.headphone_ring[..., None] * fp.on[..., None]
        # reassembly (radial scramble -> lock from the face outwards)
        dt = t - self.f_lock
        band = 0.28
        scr = (dt >= -band) & (dt < 0) & fp.on
        pre = dt < -band
        q = np.clip((dt + band) / band, 0, 1)[..., None]
        if scr.any() or pre.any():
            sc = fp.atlas.idx(SCRAMBLE)
            fr = int(np.floor(t * 30))
            rnd = hash01(np.arange(fp.R)[:, None], np.arange(fp.C)[None, :], fr)
            sidx = sc[(rnd * len(sc)).astype(int) % len(sc)]
            idx = np.where(scr, sidx, idx)
            col = np.where(scr[..., None], lerp(TEAL * 0.5, col * 1.2 + MINT * 0.3, q) * (0.2 + q ** 1.5), col)
            idx = np.where(pre, 0, idx)
        lk = np.clip(dt, 0, None)[..., None]
        col = col * (1 + 1.1 * np.exp(-lk / 0.18)) + CREAM * 0.45 * np.exp(-lk / 0.1) * (dt >= 0)[..., None] * fp.on[..., None]
        # dissolve from 58.0: released cells leave the grid and drift away as particles
        rel = t >= self.f_rel
        if rel.any():
            idx_p = idx.copy()
            idx = np.where(rel, 0, idx)
        glow_gain = 0.20 * (1 - float(smoothstep(prog(t, 58.0, 59.3))))
        lay = compose_grid(fp.atlas, idx, col)
        lockp = smoothstep(np.clip(dt / 0.5, 0, 1))
        lay += self.final_glow[mode] * glow_gain * (0.3 + 0.7 * float(smoothstep(prog(t, 52.0, 53.0))))
        # slow push-in about the face
        s = 1.0 + 0.055 * float(ease_in_out_sine(prog(t, 52.0, 59.8)))
        fx, fy = self.f_face
        lay_s = resize_layer(lay, s) if s > 1.0005 else lay
        ox = self.f_x + fx - fx * s
        oy = self.f_y + fy - fy * s
        add_layer(cv, lay_s, ox, oy)
        hx, hy = fp.headphone_px
        if t < 58.0:
            self.headphone_rings(cv, t, ox + hx * s, oy + hy * s, scale=1.25, gain=0.6 * (1 - fade(t, 57.0, 58.0)))
        if rel.any():
            rr, cc = np.nonzero(rel & (idx_p > 0))
            tau = t - self.f_rel[rr, cc]
            life = np.maximum(59.75 - self.f_rel[rr, cc], 0.25)
            a = np.clip(1 - tau / np.minimum(life, 1.0), 0, 1) ** 1.3
            px = self.f_cx[rr, cc] + self.f_vx[rr, cc] * tau + 10 * np.sin(tau * 3.0 + self.f_ph[rr, cc]) * tau
            py = self.f_cy[rr, cc] + self.f_vy[rr, cc] * tau - 40 * tau ** 2
            sx = ox + px * s
            sy = oy + py * s
            gi = idx_p[rr, cc]
            # glyphs mutate once while drifting
            mut = tau > 0.35 + 0.3 * self.f_ph[rr, cc] / (2 * np.pi)
            sc = fp.atlas.idx(".:·*+")
            gi = np.where(mut, sc[(rr * 7 + cc * 3) % len(sc)], gi)
            cc_ = col[rr, cc] * (a * (1 + 0.6 * np.exp(-tau / 0.15)))[:, None]
            keep = a > 0.01
            splat(cv, fp.atlas, sx[keep], sy[keep], gi[keep], cc_[keep])
        # titles
        self.draw_finale_text(cv, t)

    def draw_finale_text(self, cv, t):
        diss = 58.3

        def chars_dissolve(atlas, s, xs, y, cols, seed):
            """Draw chars at xs (centres); after `diss` each char drifts up and fades."""
            n = len(s)
            gi = atlas.idx(s)
            r = hash01(np.arange(n), seed)
            tr = diss + 0.9 * r
            tau = np.maximum(t - tr, 0)
            a = np.clip(1 - tau / 0.75, 0, 1) ** 1.3
            ys = np.full(n, y + atlas.ch / 2) - 30 * tau - 50 * tau ** 2
            xx = xs + 14 * np.sin(r * 9 + tau * 2.5) * tau
            keep = (gi != 0) & (a > 0.01)
            splat(cv, atlas, xx[keep], ys[keep], gi[keep], cols[keep] * a[keep, None])

        if t >= 53.0:
            at = self.title
            age = t - 53.0
            a = float(smoothstep(age / 0.7))
            trk = at.cw + 26 * (1 - float(ease_out_expo(age / 1.4)))
            n = 4
            xs = W / 2 + (np.arange(n) - (n - 1) / 2) * trk
            cols = np.tile(CREAM * 1.0 * a, (n, 1)) * (1 + 0.6 * np.exp(-age / 0.3))
            chars_dissolve(at, "HEVI", xs, 742, cols, 1)
        if t >= 54.0:
            at = self.tag
            n = len(TAGLINE)
            ci = np.arange(n)
            ta = np.clip((t - 54.0 - ci * 0.018) / 0.35, 0, 1)
            xs = W / 2 + (ci - (n - 1) / 2) * at.cw
            cols = (CREAM * 0.92)[None] * ta[:, None]
            chars_dissolve(at, TAGLINE, xs, 858, cols, 2)
        if t >= 56.0:
            at = self.url
            n = len(URL_LINE)
            a = float(smoothstep((t - 56.0) / 0.6))
            ci = np.arange(n)
            xs = W / 2 + (ci - (n - 1) / 2) * at.cw
            cols = np.tile(CREAM * 0.55, (n, 1))
            k = URL_LINE.index("hevi-record")
            cols[k:] = TEAL * 0.9
            chars_dissolve(at, URL_LINE, xs, 990, cols * a, 3)

    # ===================================================================================== frame
    def content(self, t):
        cv = np.zeros((H, W, 3), np.float32)
        if t < 6.9:
            self.draw_boot(cv, t)
        if 6.0 <= t < 14.0:
            self.draw_rain_reveal(cv, t)
        if 14.0 <= t < 22.0:
            self.draw_hero(cv, t)
        if 22.0 <= t < 34.4:
            if t < 22.35:
                self.draw_hero_out(cv, t)
            self.draw_portrait_panel(cv, t)
            if t < 26.4:
                self.draw_whales(cv, t)
            self.draw_flow(cv, t)
            self.draw_funnel(cv, t)
        self.draw_trade(cv, t)
        self.draw_discipline(cv, t)
        self.draw_finale(cv, t)
        return cv

    def draw_hero_out(self, cv, t):
        """Block letters + subtitle fading out while the portrait glides away (22.0 - 22.35)."""
        out = fade(t, 0, 0, 22.0, 22.3)
        dy = -14 * float(ease_in_cubic(prog(t, 22.0, 22.3)))
        self.draw_block_word(cv, self.letters, self.blocks_x, self.blocks_y, [14.0, 14.5, 15.0, 15.5], t,
                             alpha=out, dy=dy)
        at = self.sub
        bw = self.letters["width"] * self.block.cw
        x = self.blocks_x + bw // 2 - len(SUBTITLE) * at.cw // 2
        y = self.blocks_y + 6 * self.block.ch + 34 + dy
        self.text(cv, at, SUBTITLE, x, round(y), CREAM * 0.80 * out)

    def post_params(self, t, fi):
        bloom = 0.85
        flash = 0.0
        ca = 0.0
        exposure = 1.0
        if t < 6.0:
            bloom = 0.9
        if 14.0 <= t < 40.0:
            bloom += 0.07 * beat_pulse(t, 0.18)
        if t >= 14.0:
            bloom += 1.4 * np.exp(-(t - 14.0) / 0.32) if t < 16 else 0
        if fi == 840:
            flash = 0.92
        elif fi == 841:
            flash = 0.42
        if 14.0 <= t < 14.6:
            ca = 7.0 * np.exp(-(t - 14.0) / 0.12)
        if 44.0 <= t < 44.7:
            ca = 6.0 * np.exp(-(t - 44.0) / 0.14)
            bloom += 1.1 * np.exp(-(t - 44.0) / 0.35)
        if 52.0 <= t:
            bloom = 1.05 + 0.25 * fade(t, 52.0, 54.0)
        # tension: global exposure breathes down
        if 40.0 <= t < 42.5:
            exposure *= 1 - 0.12 * fade(t, 40.0, 40.4, 42.0, 42.5)
        # the very end: everything to background by 59.8
        if t >= 59.2:
            exposure *= 1 - float(smoothstep(prog(t, 59.2, 59.8)))
        return dict(bloom_gain=bloom, flash=flash, ca=ca, exposure=exposure)

    def render(self, fi):
        t = fi / FPS
        cv = self.content(t)
        pp = self.post_params(t, fi)
        return self.fin_post.finish(cv, fi, **pp)
