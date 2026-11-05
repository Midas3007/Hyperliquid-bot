# Genesis reel: Nº 0001 builds layer by layer, then the collection shuffles to the beat, a 1/1, and a closing grid.
# 1080 x 1350, 30 fps, 44 s, timed to score-riviera.py (72 BPM: beat 0.833 s, bar 3.333 s). Frames piped to ffmpeg.
import json, math, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

D = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1]
W, H, FPS, DUR = 1080, 1350, 30, 44.0
BEAT = 60 / 72; BAR = 4 * BEAT
BG, INK, WHITE, GREY, GOLD, GOLD2 = (13, 12, 11), (22, 20, 17), (244, 244, 240), (140, 140, 136), (212, 175, 55), (243, 215, 122)
F = lambda p, s: ImageFont.truetype(p, s)
SERIF = '/usr/share/fonts/truetype/crosextra/Caladea-Bold.ttf'
MONO = '/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf'
MONOB = '/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-SemiBold.ttf'
fTitle, fMono, fMonoB, fSmall = F(SERIF, 58), F(MONO, 22), F(MONOB, 22), F(MONO, 18)
meta = json.load(open(f'{D}/meta.json'))
TOK = {t['id']: t for t in meta['tokens']}
LAYERS = meta['layers']
S = 960
load = lambda name: Image.open(f'{D}/tok/{name}').convert('RGB').resize((S, S), Image.LANCZOS)
stages = [load(f'stage-{i}.png') for i in range(7)]
rand = [t['id'] for t in meta['tokens'] if t['label'] != '1 of 1']
SLOW, FAST = rand[:8], rand[8:20]
GRID = [rand[20], 59, rand[21], rand[22], 552, rand[23], rand[24], 235, rand[25]]
imgs = {i: load(f't-{i}.png') for i in set(SLOW + FAST + GRID + [552])}
TIER_COL = {'common': (169, 162, 150), 'uncommon': (143, 199, 166), 'rare': (143, 184, 240), 'legendary': GOLD2, 'one': GOLD2}

def ease(x): x = max(0, min(1, x)); return x * x * (3 - 2 * x)
def spaced(d, xy, s, font, fill, sp=0, anchor='la'):
    if anchor == 'ra':
        w = sum(d.textlength(ch, font=font) + sp for ch in s) - sp; xy = (xy[0] - w, xy[1])
    if anchor == 'ma':
        w = sum(d.textlength(ch, font=font) + sp for ch in s) - sp; xy = (xy[0] - w / 2, xy[1])
    x, y = xy
    for ch in s: d.text((x, y), ch, font=font, fill=fill); x += d.textlength(ch, font=font) + sp
def traits_line(t):
    return [t[L['key']] for L in LAYERS]
def frame_base():
    im = Image.new('RGB', (W, H), BG); d = ImageDraw.Draw(im)
    d.rounded_rectangle((44, 44, W - 44, 44 + S + 32), 14, outline=(70, 60, 32), width=2)
    return im, d
def pill(d, x, y, text, col):
    w = d.textlength(text.upper(), font=fSmall) + len(text) * 2 + 28
    d.rounded_rectangle((x - w, y, x, y + 36), 18, outline=col, width=2); spaced(d, (x - w + 14, y + 7), text.upper(), fSmall, col, 2)
def caption(d, tid, t, label, tier, alpha=1.0, layer_i=None):
    col = lambda c: tuple(int(BG[k] + (c[k] - BG[k]) * alpha) for k in range(3))
    d.text((60, 1090), f'Genesis Nº {tid:04d}', font=fTitle, fill=col(WHITE))
    pill(d, W - 60, 1104, label, col(TIER_COL.get(tier, GREY)))
    names = traits_line(t)
    y = 1180; x = 60
    for i, (L, n) in enumerate(zip(LAYERS, names)):
        on = layer_i is None or i <= layer_i
        cur = layer_i is not None and i == layer_i
        s = f"{L['label'].upper()}: {n}"
        w = d.textlength(s, font=fSmall) + 28
        if x + w > W - 60: x = 60; y += 46
        d.rounded_rectangle((x, y, x + w, y + 36), 8, fill=col((40, 34, 20)) if cur else None, outline=col(GOLD if cur else (60, 56, 48)) if on else col((32, 30, 26)), width=2)
        d.text((x + 14, y + 7), s, font=fSmall, fill=col(GOLD2 if cur else (WHITE if on else (70, 66, 60))))
        x += w + 10

ff = subprocess.Popen(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                       '-i', f'{D}/riviera.wav', '-c:v', 'libx264', '-preset', 'slow', '-crf', '21', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
                       '-shortest', '-movflags', '+faststart', OUT], stdin=subprocess.PIPE)
t1 = meta['stage1']
STAGE_T = 1.25
T_SLOW, T_FAST, T_HERO, T_GRID = 10.0, 10.0 + 8 * 2 * BEAT, 10.0 + 8 * 2 * BEAT + 12 * BEAT, 10.0 + 8 * 2 * BEAT + 12 * BEAT + BAR
for f in range(int(DUR * FPS)):
    t = f / FPS
    im, d = frame_base()
    if t < T_SLOW:  # Nº 0001 builds, one layer every 1.25 s
        i = min(6, int(t / STAGE_T)); k = ease((t - i * STAGE_T) / 0.35)
        cur = stages[i] if i == 0 or k >= 1 else Image.blend(stages[i - 1], stages[i], k)
        if t < .6: cur = Image.blend(Image.new('RGB', (S, S), BG), cur, ease(t / .6))
        im.paste(cur, (60, 60))
        if t < 7 * STAGE_T:
            caption(d, 1, t1['traits'], f"layer {i + 1} of 7", 'one' if False else 'legendary', layer_i=i)
        else:
            caption(d, 1, t1['traits'], t1['rank']['label'], t1['rank']['tier'])
    elif t < T_HERO:  # the shuffle: every two beats, then every beat
        if t < T_FAST: n = int((t - T_SLOW) / (2 * BEAT)); tid = SLOW[n]; lt = (t - T_SLOW) - n * 2 * BEAT; prev = SLOW[n - 1] if n else 1
        else: n = int((t - T_FAST) / BEAT); tid = FAST[min(n, len(FAST) - 1)]; lt = (t - T_FAST) - n * BEAT; prev = (FAST[n - 1] if n else SLOW[-1])
        k = ease(lt / 0.18)
        a = imgs.get(prev, stages[6]) if prev != 1 else stages[6]
        cur = Image.blend(a, imgs[tid], k) if k < 1 else imgs[tid]
        im.paste(cur, (60, 60))
        tk = TOK[tid]; caption(d, tid, tk['traits'], tk['label'], tk['tier'])
        if lt < 0.12:  # a soft flash on the cut
            im = Image.blend(im, Image.new('RGB', (W, H), (40, 34, 20)), 0.25 * (1 - lt / 0.12))
    elif t < T_GRID:  # the 1/1, slowly pushing in with a gold glow
        lt = t - T_HERO; z = 1 + 0.05 * ease(lt / BAR)
        hero = imgs[552].resize((int(S * z), int(S * z)), Image.LANCZOS)
        off = (hero.width - S) // 2; hero = hero.crop((off, off, off + S, off + S))
        glow = Image.new('RGB', (W, H), BG); gd = ImageDraw.Draw(glow); gd.rounded_rectangle((40, 40, W - 40, 60 + S + 20), 30, fill=(120, 96, 30)); glow = glow.filter(ImageFilter.GaussianBlur(40))
        im = Image.blend(im, glow, 0.35 * ease(lt / 0.8)); d = ImageDraw.Draw(im)
        im.paste(hero if lt > 0.2 else Image.blend(imgs[FAST[-1]], hero, ease(lt / 0.2)), (60, 60))
        d.text((60, 1090), 'Gold Leaf · 1 of 1', font=fTitle, fill=GOLD2)
        pill(d, W - 60, 1104, '1 of 1', GOLD2)
        spaced(d, (60, 1190), f'GENESIS Nº 0552 · ONE OF SIX ONE-OF-ONES', fMono, GOLD, 3)
        spaced(d, (60, 1236), 'THE SET I DESIGNS, REDRAWN SQUARE', fSmall, GREY, 2)
    else:  # closing grid
        lt = t - T_GRID; im = Image.new('RGB', (W, H), BG); d = ImageDraw.Draw(im)
        cell = 300; gap = 18; x0 = (W - 3 * cell - 2 * gap) // 2; y0 = 70
        for j, tid in enumerate(GRID):
            k = ease((lt - j * 0.12) / 0.5)
            if k <= 0: continue
            tile = imgs[tid].resize((cell, cell), Image.LANCZOS)
            if k < 1: tile = Image.blend(Image.new('RGB', (cell, cell), BG), tile, k)
            im.paste(tile, (x0 + (j % 3) * (cell + gap), y0 + (j // 3) * (cell + gap) + int((1 - k) * 20)))
        a = ease((lt - 1.2) / 0.8)
        c = lambda col: tuple(int(BG[q] + (col[q] - BG[q]) * a) for q in range(3))
        spaced(d, (W // 2, 1060), 'GENESIS', F(SERIF, 84), c(WHITE), 14, 'ma')
        spaced(d, (W // 2, 1170), 'LIMITED SUPPLY · 6 ONE-OF-ONES · 7 TRAIT LAYERS', fMono, c(GOLD), 3, 'ma')
        spaced(d, (W // 2, 1222), 'MIDAS × KIYOSHI', F(SERIF, 34), c(GOLD2), 8, 'ma')
    if t > DUR - 1.6: im = Image.blend(im, Image.new('RGB', (W, H), (0, 0, 0)), ease((t - (DUR - 1.6)) / 1.6))
    ff.stdin.write(im.tobytes())
ff.stdin.close(); ff.wait(); print('done', OUT)
