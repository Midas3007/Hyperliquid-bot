# Minakiba teaser score: an original amapiano groove meets a Japanese koto. 112 BPM, 30 s.
# Warm fingered bass, congas, shekere, a soft kick and rim, warm pads (Am9 | Fmaj7 | Dm9 | E7sus), and a koto melody in the Japanese In scale
# (A Bb D E F) that plucks once for each blade of the fan. Timed to teaser.html.
#   python3 score-minakiba.py out.wav
import sys, wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100; BEAT = 60 / 112; BAR = 4 * BEAT; DUR = 30.0
rng = np.random.default_rng(112)
mix = np.zeros((int(SR * (DUR + 4)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT

def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix): return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan)); mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))

def koto(m, dur=1.6):  # bright Karplus-Strong pluck with a little pitch bend at the attack, like a koto string
    f = hz(m); n = int(SR * dur); p = max(2, int(SR / f))
    buf = rng.uniform(-1, 1, p); out = np.zeros(n); k = 0
    for i in range(n):
        out[i] = buf[k]; nk = (k + 1) % p; buf[k] = 0.4985 * (buf[k] + buf[nk]); k = nk
    t = np.arange(n) / SR
    return (out - onepole(out, 0.97)) * (1 + 0.6 * np.exp(-t * 30))

def fbass(m, dur, vel=1.0):  # a warm fingered bass: soft pluck, round body, a little string noise on the attack
    t = np.arange(int(SR * (dur + 0.25))) / SR; f = hz(m)
    body = sum(a * np.sin(2 * np.pi * f * k * t) * np.exp(-t * (1.8 + 2.5 * k)) for k, a in ((1, 1), (2, .5), (3, .18), (4, .07)))
    pluck = onepole(rng.normal(0, 1, len(t)), 0.6) * np.exp(-t * 90) * 0.08
    s = (body + pluck) * (1 - np.exp(-t / 0.004)) * np.clip((dur + 0.15 - t) / 0.15, 0, 1) * vel
    return onepole(s, 0.25)

def conga(m, kind='open', vel=1.0):  # hand drum: a skin tone that drops slightly in pitch; a slap adds a bright crack
    t = np.arange(int(SR * 0.6)) / SR; f = hz(m)
    ph = 2 * np.pi * np.cumsum(f * (1 + 0.12 * np.exp(-t * 40))) / SR
    tone = (np.sin(ph) + 0.25 * np.sin(2.3 * ph)) * np.exp(-t * (9 if kind == 'open' else 30))
    hit = onepole(rng.normal(0, 1, len(t)), 0.3) * np.exp(-t * (60 if kind == 'slap' else 140)) * (0.9 if kind == 'slap' else 0.25)
    return (tone + hit) * vel

def kick(vel=1.0):
    t = np.arange(int(SR * 0.3)) / SR
    return np.sin(2 * np.pi * (50 + 90 * np.exp(-t * 35)) * t) * np.exp(-t * 11) * vel

def shaker(vel=1.0):
    t = np.arange(int(SR * 0.07)) / SR; n = rng.normal(0, 1, len(t)); n = n - onepole(n, 0.5)
    return n * np.exp(-t * 70) * vel

def rim(vel=1.0):
    t = np.arange(int(SR * 0.05)) / SR
    return (np.sin(2 * np.pi * 1900 * t) * 0.6 + rng.normal(0, 1, len(t)) * 0.3) * np.exp(-t * 110) * vel

def pad(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.5))) / SR
    out = sum(2 * ((hz(m) * 2 ** (d / 12) * t + rng.random()) % 1.0) - 1 for m in notes for d in (-0.08, 0, 0.08))
    out = onepole(onepole(onepole(out, 0.95), 0.93), 0.7)
    return out * np.clip(t / 1.2, 0, 1) * np.clip((dur + 1.2 - t) / 1.2, 0, 1) * vel / (len(notes) * 3)

def boom(t0, gain):  # low hit with a short noise swell, for the coin and the end card
    t = np.arange(int(SR * 2.5)) / SR
    add(np.sin(2 * np.pi * (36 + 30 * np.exp(-t * 6)) * t) * np.exp(-t * 1.6), t0, gain=gain)
    n = int(SR * 0.8); sw = onepole(rng.normal(0, 1, n), 0.6) * np.linspace(0, 1, n) ** 2
    add(sw, t0 - 0.8, gain=gain * 0.12)

CH = [[57, 60, 64, 67, 71], [53, 57, 60, 64], [50, 53, 57, 60, 64], [52, 57, 59, 62]]  # Am9, Fmaj7, Dm9, E7sus
ROOT = [33, 29, 26, 28]
NB = 14
for bar in range(NB):
    ci = bar % 4
    if bar < 12: add(pad(CH[ci], BAR * .98, 0.9 if bar >= 5 else 0.6), at(bar), gain=0.10)
    if 2 <= bar < 12:  # shaker and soft kick from the fan
        for k in range(16): add(shaker(1.0 if k % 4 == 2 else 0.5), at(bar, k * .25), pan=0.3, gain=0.05)
        for b in range(4): add(kick(0.9), at(bar, b), gain=0.16 if bar >= 5 else 0.08)
    if 5 <= bar < 12:  # bass and congas enter with the name
        r = ROOT[ci] + 12
        for b, m, d in ((0, r, 1.2), (1.5, r + 7, .45), (2, r + 12, .8), (3, r + 7, .45), (3.5, r + 10, .45)):
            add(fbass(m, d * BEAT, 0.9), at(bar, b), gain=0.42)
        for b, m, kind, v in ((0.5, 62, 'open', .6), (1, 67, 'slap', .8), (1.75, 62, 'mute', .5), (2, 59, 'open', .8),
                              (2.5, 62, 'open', .6), (3, 67, 'slap', .8), (3.5, 64, 'open', .7), (3.75, 62, 'mute', .4)):
            add(conga(m - 12, kind, v), at(bar, b) + rng.normal(0, .006), pan=0.35 if m > 62 else -0.2, gain=0.15)
        for b in (1, 3): add(rim(1.0), at(bar, b), pan=-0.25, gain=0.05)
# the koto: the In-scale motif in the intro, one pluck per fan blade, then the melody over the groove
IN = [57, 58, 62, 64, 65, 69, 70, 74, 76, 77]
for b, n in ((0.5, 69), (1, 70), (1.5, 74), (2.5, 69), (3, 65), (4.5, 64), (5, 65), (5.5, 69), (6.5, 64)): add(koto(n, 1.8), at(0, b) + 0.6, pan=0.2, gain=0.11)
for i, n in enumerate((64, 65, 69, 70, 74)): add(koto(n, 1.6), at(2, 1 + i), pan=-0.3 + 0.15 * i, gain=0.12)
MEL = {5: [(0, 76), (.75, 74), (1.5, 70), (2.5, 69), (3, 70)], 6: [(0, 74), (1.5, 69), (2, 65), (3, 64)],
       7: [(0, 65), (.75, 69), (1.5, 70), (2.5, 74), (3.25, 76)], 8: [(0, 77), (1, 76), (1.5, 74), (2.5, 70)],
       9: [(0, 69), (.75, 70), (1.5, 74), (2.5, 76), (3, 74)], 10: [(0, 70), (1.5, 69), (2, 65), (3, 64)],
       11: [(0, 65), (.75, 69), (1.5, 70), (2, 74), (2.5, 76), (3, 77), (3.5, 81)]}
for bar, notes in MEL.items():
    for b, n in notes: add(koto(n, 1.4), at(bar, b), pan=0.25, gain=0.09)
boom(at(4, 2), 0.35); boom(at(12), 0.5)
# the end card: the chord rings and the koto motif comes back, slower
for i, m in enumerate([45, 57, 60, 64, 67, 71]): add(koto(m, 4.0), at(12) + i * 0.06, pan=-0.3 + 0.12 * i, gain=0.08)
add(pad(CH[0], 3.6, 1.0), at(12), gain=0.14)
for b, n in ((1, 69), (2, 70), (3, 74), (5, 69)): add(koto(n, 2.2), at(12, b), pan=0.2, gain=0.08)

n_ir = int(SR * 2.4); tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.7), 0.5) for _ in range(2)], axis=1); ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = (mix + 0.3 * wet)[: int(SR * DUR)]
fi = int(SR * 0.4); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.0); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-minakiba.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
