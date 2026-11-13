# "Opus": an original cinematic score in D major, 72 BPM, 28 bars (93.3 s), timed to opus.html.
# A heartbeat in the dark; a piano theme; strings that swell bar by bar; a wordless choir with the galaxy; pulsing
# strings like a clock; a long crescendo under the share price; a thunderclap and the whole orchestra when the fan
# returns; the theme once more, high and quiet, under the name.
#   python3 score-opus.py out.wav
import sys, wave
import numpy as np
from scipy.signal import fftconvolve, lfilter, butter

SR = 44100; BEAT = 60 / 72; BAR = 4 * BEAT; NB = 28; DUR = NB * BAR
rng = np.random.default_rng(72)
mix = np.zeros((int(SR * (DUR + 6)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT

def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix): return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan)); mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))

def saws(f, t, det=(-0.1, -0.04, 0.03, 0.09)):
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.1 * t + rng.random() * 6) * np.clip(t / 0.8, 0, 1)
    return sum(2 * ((np.cumsum(np.full_like(t, f * 2 ** (d / 12))) * vib / SR + rng.random()) % 1.0) - 1 for d in det) / len(det)

def strings(notes, dur, vel=1.0, att=1.0, bright=0.9):
    t = np.arange(int(SR * (dur + 2.0))) / SR
    out = sum(saws(hz(m), t) for m in notes) / len(notes)
    for _ in range(3): out = onepole(out, bright)
    env = np.clip(t / att, 0, 1) ** 1.5 * np.clip((dur + 1.6 - t) / 1.6, 0, 1)
    return out * env * vel

FORM = [butter(2, [f * 0.85 / (SR / 2), f * 1.15 / (SR / 2)], btype='band') for f in (700, 1150, 2600)]
def choir(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 2.5))) / SR
    src = sum(saws(hz(m), t, det=(-0.12, 0, 0.12)) for m in notes) / len(notes)
    out = lfilter(*FORM[0], src) + 0.6 * lfilter(*FORM[1], src) + 0.25 * lfilter(*FORM[2], src)
    env = np.clip(t / 1.6, 0, 1) ** 2 * np.clip((dur + 2.0 - t) / 2.0, 0, 1)
    return out * env * vel * 2.2

def piano(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 2.5))) / SR; f = hz(m)
    tone = sum(a * np.sin(2 * np.pi * f * k * (1 + 0.0004 * k * k) * t) * np.exp(-t * (0.6 + k * 0.9)) for k, a in ((1, 1), (2, .45), (3, .22), (4, .1), (5, .05)))
    env = (1 - np.exp(-t / 0.002)) * np.clip((dur + 1.8 - t) / 1.8, 0, 1)
    return tone * env * vel

def celesta(m, vel=1.0):
    t = np.arange(int(SR * 2.2)) / SR; f = hz(m)
    return (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * f * 4.02 * t) * np.exp(-t * 6)) * np.exp(-t * 2.4) * vel

def heart(vel=1.0):
    t = np.arange(int(SR * 0.4)) / SR
    return np.sin(2 * np.pi * (42 + 25 * np.exp(-t * 20)) * t) * np.exp(-t * 14) * vel

def timp(m, vel=1.0, dur=3.0):
    t = np.arange(int(SR * dur)) / SR; f = hz(m)
    return (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * f * 1.5 * t)) * (1 - np.exp(-t / .004)) * np.exp(-t * 1.4) * vel

def thunder(t0, gain):
    t = np.arange(int(SR * 6)) / SR
    add(np.sin(2 * np.pi * (30 + 40 * np.exp(-t * 5)) * t) * np.exp(-t * .9), t0, gain=gain)
    n = onepole(rng.normal(0, 1, len(t)), 0.9) * np.exp(-t * 1.2); add(n, t0, gain=gain * .5)
    n = int(SR * 3); sw = onepole(rng.normal(0, 1, n), 0.7) * np.linspace(0, 1, n) ** 3; add(sw, t0 - 3, gain=gain * .18)

def riser(t0, length, gain):
    n = int(SR * length); x = rng.normal(0, 1, n); k = np.linspace(0, 1, n)
    out = np.zeros(n); a = 0.98; y = 0.0
    for i in range(0, n, 512):  # a filter that opens as it rises
        seg = x[i:i + 512]; a = 0.985 - 0.5 * k[i]; out[i:i + 512] = lfilter([1 - a], [1, -a], seg)
    add(out * k ** 2.5, t0, gain=gain)

# chords per bar: (bass, voicing) in D major
D = (38, [62, 66, 69, 73, 76]); Bm = (35, [59, 62, 66, 69, 74]); G = (31, [59, 62, 66, 71, 74]); A = (33, [57, 61, 64, 69, 71])
Asus = (33, [57, 62, 64, 69, 71]); Fsm = (30, [57, 61, 64, 66, 69]); Em = (28, [55, 59, 62, 66, 71]); Ac = (37, [57, 61, 64, 69, 73])
PROG = {3: D, 4: Bm, 5: G, 6: Asus, 7: A, 8: G, 9: A, 10: Fsm, 11: Bm, 12: G, 13: A, 14: D, 15: Bm, 16: G, 17: G, 18: A, 19: Bm,
        20: G, 21: A, 22: Asus, 23: D, 24: Ac, 25: Bm, 26: G, 27: D}
DYN = {3: .35, 4: .4, 5: .5, 6: .55, 7: .6, 8: .75, 9: .8, 10: .85, 11: .7, 12: .75, 13: .8, 14: .8, 15: .85, 16: .9, 17: .85, 18: .9, 19: .95,
       20: .9, 21: 1.0, 22: 1.15, 23: 1.5, 24: 1.5, 25: 1.0, 26: .9, 27: .8}

# 0-3: the dark, a heartbeat and a low D
add(strings([26, 38], at(3) + 2, 0.5, att=4.0, bright=0.97), 0, gain=0.35)
for bar in range(0, 4):
    for b, v in ((0, 1.0), (0.36, 0.6)): add(heart(v), at(bar, b * 1.2), gain=0.55 * (0.6 + 0.4 * bar / 3))
riser(at(2), BAR, 0.05)

for bar, (bass, v) in PROG.items():
    dyn = DYN[bar]
    add(strings(v, BAR * 1.02, dyn, att=1.4 if bar < 8 else 0.8), at(bar), gain=0.2)
    add(strings([bass, bass + 12], BAR * 1.02, dyn, att=1.0, bright=0.95), at(bar), gain=0.25)
    if 11 <= bar <= 13 or 17 <= bar <= 22:  # pulsing strings: the clock, the rivers, the climb
        for k in range(8):
            add(strings([v[0], v[2]], BEAT / 2 * .8, dyn * (1 if k % 2 == 0 else .7), att=0.03, bright=0.8), at(bar, k / 2), pan=0.3, gain=0.13)
    if 14 <= bar <= 16 or bar >= 23:  # the choir with the galaxy and the climax
        add(choir([m + 12 if i > 2 else m for i, m in enumerate(v[:4])], BAR * 1.02, dyn), at(bar), pan=-0.1, gain=0.12)
    if bar in (8, 11, 14, 17, 20): add(timp(bass + 12, 0.8), at(bar), gain=0.22)

# the piano theme (original), first statement bars 3-7
THEME = [(0, 78, 1), (1, 81, 1), (2, 76, 1.5), (3.5, 74, .5), (4, 71, 2), (6, 73, 1), (7, 74, 1),
         (8, 76, 1.5), (9.5, 78, .5), (10, 79, 1), (11, 78, 1), (12, 76, 3), (15, 73, 1)]
def theme(start_bar, shift=0, vel=1.0, gain=0.16, pan=0.15):
    for b, m, d in THEME: add(piano(m + shift, d * BEAT * .98, vel), at(start_bar, b), pan=pan, gain=gain)
theme(3, 0, 0.8, 0.14)
# arpeggios under the fan and the sphere
for bar in range(8, 14):
    _, v = PROG[bar]
    for k in range(8): add(piano(v[k % 5] + 12, BEAT * .6, 0.5), at(bar, k / 2), pan=-0.2 + 0.05 * (k % 5), gain=0.06)
# celesta stars with the galaxy
for k in range(36):
    bar = 14 + k / 12; _, v = PROG[int(bar)]
    add(celesta(v[int(rng.integers(0, 5))] + 24, 0.6 + 0.4 * rng.random()), at(14) + k * BAR / 12 + rng.random() * .1, pan=rng.uniform(-.7, .7), gain=0.05)
# the theme again on piano over the rivers, an octave up
theme(17, 12, 0.6, 0.09, pan=-0.15)
# the climb: rising arpeggios, a timpani roll and a long riser into the thunderclap
for bar in range(20, 23):
    _, v = PROG[bar]
    for k in range(16): add(piano(v[k % 5] + 12 + 12 * (k // 8), BEAT * .3, 0.4 + 0.4 * (bar - 20) / 2), at(bar, k / 4), pan=0.2, gain=0.05)
for k in range(48): add(timp(45, 0.25 + 0.75 * (k / 48) ** 2, dur=0.5), at(22) + k * BAR / 48, gain=0.18)
riser(at(21), 2 * BAR, 0.1)
thunder(at(23), 0.6)
# the climax: the theme in the strings and choir, two octaves of it
for b, m, d in THEME[:7]:
    add(strings([m, m - 12], d * BEAT, 1.3, att=0.12, bright=0.85), at(23, b), gain=0.16)
    add(choir([m - 12], d * BEAT, 1.0), at(23, b), gain=0.08)
# the name: the theme's opening once more, high and quiet, and a last D that rings out
for b, m, d in THEME[:5]: add(piano(m + 12, d * BEAT * 1.1, 0.55), at(25, b + 1), pan=0.2, gain=0.1)
for i, m in enumerate([38, 50, 62, 66, 69, 74, 78]): add(piano(m, BAR * 2, 0.6), at(27) + i * 0.09, pan=-0.3 + 0.1 * i, gain=0.09)
for k, m in enumerate((86, 90, 93, 98)): add(celesta(m, 0.5), at(27, 1 + k * .5), pan=0.4, gain=0.04)

n_ir = int(SR * 4.0); tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 1.3), 0.45) for _ in range(2)], axis=1); ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = (mix + 0.42 * wet)[: int(SR * DUR)]
fi = int(SR * 1.0); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.0); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
out = np.tanh(out / np.max(np.abs(out)) * 1.1) / np.tanh(1.1) * 0.9
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-opus.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', round(DUR, 2), 's')
