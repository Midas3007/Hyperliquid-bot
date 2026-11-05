# "Coming soon" teaser: Kiyoshi, Midas's desk, the fund and the Genesis pass, cut on the beat of score-riviera.py.
# 1920 x 1080, 30 fps, 44 s. Uses clips of the earlier films plus a new pass scene and end card.
#   python3 coming-soon.py <pass-assets-dir with tok/ and meta.json> <riviera.wav> <out.mp4>
import json, os, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS, SCORE, OUT = sys.argv[1:4]
W, H, FPS, DUR = 1920, 1080, 30, 44.0
BEAT = 60 / 72
BG, WHITE, GREY, GOLD, GOLD2 = (8, 8, 7), (244, 244, 240), (150, 146, 138), (212, 175, 55), (243, 215, 122)
SERIF = '/usr/share/fonts/truetype/crosextra/Caladea-Bold.ttf'
MONO = '/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf'
F = lambda p, s: ImageFont.truetype(p, s)
ease = lambda x: (lambda y: y * y * (3 - 2 * y))(max(0.0, min(1.0, x)))

def spaced(d, xy, s, font, fill, sp=0, anchor='l'):
    w = sum(d.textlength(ch, font=font) + sp for ch in s) - sp
    x = xy[0] - (w if anchor == 'r' else w / 2 if anchor == 'm' else 0)
    for ch in s: d.text((x, xy[1]), ch, font=font, fill=fill); x += d.textlength(ch, font=font) + sp

def clip(name, start, beats):
    """Frames of an earlier film, as a generator of PIL images."""
    n = round(beats * BEAT * FPS)
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', os.path.join(HERE, name), '-frames:v', str(n),
                          '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)
    for _ in range(n):
        b = p.stdout.read(W * H * 3)
        if len(b) < W * H * 3: break
        yield Image.frombytes('RGB', (W, H), b)
    p.stdout.close(); p.wait()

# ---------- chapter overlays ----------
CH = {
    'I': ('I', 'THE ENGINE', 'Kiyoshi: Celo FX arbitrage, built by Mel'),
    'II': ('II', 'THE DESK', 'Midas edition: cloud, alerts, its own gas, smart sizing'),
    'III': ('III', 'THE FUND', 'Members share one bot. One share price, settled daily'),
    'IV': ('IV', 'THE PASS', 'Genesis: a limited collection, six one-of-ones'),
}
shade = Image.new('L', (W, H), 0)
sd = ImageDraw.Draw(shade)
for y in range(H): sd.line([(0, y), (W, y)], fill=int(170 * max(0, (y - H * 0.62) / (H * 0.38)) ** 1.4))
def chapter(im, key, lt):
    if lt > 3.2: return im
    a = ease(lt / 0.5) * (1 - ease((lt - 2.6) / 0.6))
    if a <= 0: return im
    black = Image.new('RGB', (W, H), (0, 0, 0))
    im = Image.composite(black, im, shade.point(lambda v: int(v * a)))
    d = ImageDraw.Draw(im)
    num, title, sub = CH[key]
    c = lambda col: tuple(int(col[k] * a) for k in range(3))
    spaced(d, (96, H - 214), f'CHAPTER {num}', F(MONO, 22), c(GOLD), 6)
    spaced(d, (96, H - 182), title, F(SERIF, 72), c(WHITE), 10)
    d.text((98, H - 92), sub, font=F(SERIF, 34), fill=c((212, 206, 194)))
    d.line([(98, H - 104), (98 + 520 * ease(lt / 0.9), H - 104)], fill=c(GOLD), width=2)
    return im

# ---------- the pass scene (chapter IV) and the end card ----------
meta = json.load(open(os.path.join(ASSETS, 'meta.json')))
cards = [Image.open(os.path.join(ASSETS, f'tok/card-{i}.png')).convert('RGBA') for i in range(6)]
small_cards = [c.resize((560, 350), Image.LANCZOS) for c in cards]
rand = [t['id'] for t in meta['tokens'] if t['label'] != '1 of 1']
TOKS = [Image.open(os.path.join(ASSETS, f'tok/t-{i}.png')).convert('RGB').resize((720, 720), Image.LANCZOS) for i in rand[:6]]
HERO = Image.open(os.path.join(ASSETS, 'tok/t-552.png')).convert('RGB').resize((760, 760), Image.LANCZOS)
def glow_bg():
    im = Image.new('RGB', (W, H), BG); g = Image.new('RGB', (W, H), BG)
    ImageDraw.Draw(g).ellipse((W / 2 - 700, H / 2 - 420, W / 2 + 700, H / 2 + 420), fill=(60, 48, 16))
    return Image.blend(im, g.filter(ImageFilter.GaussianBlur(160)), 0.9)
GLOW = glow_bg()
def pass_scene(lt):  # 12 beats: the six cards fan out, tokens shuffle on the beat, the 1/1
    im = GLOW.copy(); d = ImageDraw.Draw(im)
    if lt < 4 * BEAT:  # cards fan out
        for i, c in enumerate(small_cards):
            k = ease((lt - i * 0.16) / 0.9)
            if k <= 0: continue
            ang = (i - 2.5) * 9 * k; cx = W / 2 + (i - 2.5) * 210 * k; cy = H / 2 + 40 + abs(i - 2.5) * 26 * k
            r = c.rotate(-ang, resample=Image.BICUBIC, expand=True)
            if k < 1: r.putalpha(r.split()[3].point(lambda v: int(v * k)))
            im.paste(r, (int(cx - r.width / 2), int(cy - r.height / 2)), r)
        spaced(d, (W / 2, 120), 'SIX DESIGNS', F(MONO, 24), GOLD, 8, 'm')
    elif lt < 8 * BEAT:  # tokens shuffle, one per beat
        n = min(5, int((lt - 4 * BEAT) / (BEAT * 0.67)))
        im.paste(TOKS[n], ((W - 720) // 2, (H - 720) // 2 - 10))
        spaced(d, (W / 2, 70), 'GENESIS · LIMITED SUPPLY · 7 TRAIT LAYERS', F(MONO, 24), GOLD, 6, 'm')
    else:  # the 1/1
        k = ease((lt - 8 * BEAT) / 0.6)
        z = 1 + 0.04 * ease((lt - 8 * BEAT) / (4 * BEAT))
        h = HERO.resize((int(760 * z), int(760 * z)), Image.LANCZOS); o = (h.width - 760) // 2; h = h.crop((o, o, o + 760, o + 760))
        im.paste(Image.blend(TOKS[5].resize((760, 760)), h, k), ((W - 760) // 2, (H - 760) // 2 - 20))
        spaced(d, (W / 2, 1000), 'GOLD LEAF · ONE OF ONE', F(MONO, 26), GOLD2, 8, 'm')
    return im
def fan(d, cx, cy, r, p, col):
    import math
    A0, A1, NB = math.pi * 1.07, math.pi * 1.93, 13
    w = (A1 - A0) / NB
    for i in range(NB):
        s = A0 + i * w * p + .011; e = A0 + (i + 1) * w * p - .011 + (1 - p) * .03; ri = r * .06
        d.polygon([(cx + math.cos(s) * ri, cy + math.sin(s) * ri), (cx + math.cos(s) * r, cy + math.sin(s) * r),
                   (cx + math.cos(e) * r, cy + math.sin(e) * r), (cx + math.cos(e) * ri, cy + math.sin(e) * ri)], fill=col)
    d.ellipse((cx - r * .05, cy - r * .02, cx + r * .05, cy + r * .08), outline=col, width=4)
def end_card(lt):
    im = GLOW.copy(); d = ImageDraw.Draw(im)
    fan(d, W / 2, 470, 230, ease(lt / 1.1), GOLD)
    a = ease((lt - 0.5) / 0.6); c = lambda col: tuple(int(BG[k] + (col[k] - BG[k]) * a) for k in range(3))
    spaced(d, (W / 2, 560), 'MIDAS × KIYOSHI', F(SERIF, 64), c(WHITE), 14, 'm')
    b = ease((lt - 0.9) / 0.6); cb = lambda col: tuple(int(BG[k] + (col[k] - BG[k]) * b) for k in range(3))
    spaced(d, (W / 2, 668), 'THE ENGINE  ·  THE DESK  ·  THE FUND  ·  THE PASS', F(MONO, 24), cb(GREY), 5, 'm')
    e = ease((lt - 1.3) / 0.6); ce = lambda col: tuple(int(BG[k] + (col[k] - BG[k]) * e) for k in range(3))
    spaced(d, (W / 2, 760), 'COMING SOON', F(SERIF, 96), ce(GOLD2), 22, 'm')
    return im

# ---------- the cut list (beats of 0.833 s) ----------
PROMO, FUND, DESK = 'midas-kiyoshi-fund-wide.mp4', 'how-the-fund-works-wide.mp4', 'kiyoshi-midas-edition-wide.mp4'
CUTS = [
    ('I', PROMO, 7.4, 4), (None, PROMO, 11.6, 4), (None, DESK, 6.2, 4),            # the engine
    ('II', DESK, 19.9, 3), (None, DESK, 24.6, 3), (None, DESK, 29.3, 3), (None, DESK, 47.6, 3),  # the desk
    ('III', FUND, 24.6, 3), (None, FUND, 33.2, 3), (None, FUND, 41.6, 3), (None, FUND, 49.4, 3),  # the fund
    ('IV', 'PASS', 0, 12),                                                           # the pass
]
enc = subprocess.Popen(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                        '-i', SCORE, '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
                        '-shortest', '-movflags', '+faststart', OUT], stdin=subprocess.PIPE)
f = 0
def put(im):
    global f
    t = f / FPS
    if t < 0.5: im = Image.blend(Image.new('RGB', (W, H), (0, 0, 0)), im, ease(t / 0.5))
    if t > DUR - 0.9: im = Image.blend(im, Image.new('RGB', (W, H), (0, 0, 0)), ease((t - (DUR - 0.9)) / 0.9))
    enc.stdin.write(im.tobytes()); f += 1
chap_start, chap = 0, None
for key, src, start, beats in CUTS:
    if key: chap, chap_start = key, f / FPS
    n = round(beats * BEAT * FPS)
    frames = (pass_scene(i / FPS) for i in range(n)) if src == 'PASS' else clip(src, start, beats)
    for i, im in enumerate(frames):
        if i < 3 and f > 0: im = Image.blend(im, Image.new('RGB', (W, H), (30, 25, 12)), 0.22 * (1 - i / 3))  # a soft flash on the cut
        put(chapter(im, chap, f / FPS - chap_start))
while f < DUR * FPS: put(end_card(f / FPS - 40.0))
enc.stdin.close(); enc.wait(); print('done', f, 'frames')
