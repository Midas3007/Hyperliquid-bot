# "Two Ponds": an original piece in the Japanese in-scale (D Eb G A Bb), 66 BPM. Koto (plucked), a breathy
# shakuhachi-style flute with bends, a low bowed drone, wind chimes, a soft taiko heartbeat, and a water splash
# on every koi leap (times shared with ponds.html). Calm, melodic, unhurried.
#   python3 score-ponds.py out.wav
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 66
BEAT = 60 / BPM
BAR = 4 * BEAT  # 3.636 s
DUR = 44.0
rng = np.random.default_rng(11)
mix = np.zeros((int(SR * (DUR + 4)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT

LEAPS = [(12.6, 2.0), (16.4, 2.0)] + [(20.2 + k * .88, 1.4) for k in range(11)]


def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix): return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def koto(m, dur=2.6, vel=1.0, bend=0.0):  # Karplus-Strong with a bright pluck and an optional press-bend
    f = hz(m); n = int(SR * dur); p = max(2, int(SR / f))
    buf = rng.uniform(-1, 1, p); buf = buf - onepole(buf, 0.5) * 0.6
    out = np.zeros(n); k = 0
    for i in range(n):
        out[i] = buf[k]; nk = (k + 1) % p; buf[k] = 0.4992 * (buf[k] + buf[nk]); k = nk
    if bend:  # resample for a gentle upward press after the attack
        t = np.arange(n) / SR; ratio = 1 + (2 ** (bend / 12) - 1) * np.clip((t - 0.25) / 0.3, 0, 1)
        idx = np.cumsum(ratio); idx = idx[idx < n - 1]; out = np.interp(idx, np.arange(n), out)
    t = np.arange(len(out)) / SR
    return out * (1 - np.exp(-t / 0.001)) * vel


def flute(m, dur, vel=1.0, scoop=True):
    t = np.arange(int(SR * (dur + 0.5))) / SR
    bendc = -1.0 * np.exp(-t / 0.18) if scoop else 0  # scoop up into the note, a semitone below
    vib = 0.25 * np.sin(2 * np.pi * 4.8 * t) * np.clip((t - 0.5) / 0.6, 0, 1)
    f = hz(m) * 2 ** ((bendc + vib * 0.12) / 12)
    ph = 2 * np.pi * np.cumsum(f) / SR
    tone = np.sin(ph) + 0.12 * np.sin(2 * ph) + 0.03 * np.sin(3 * ph)
    breath = onepole(rng.normal(0, 1, len(t)), 0.75); breath = breath - onepole(breath, 0.98)
    env = np.clip(t / 0.12, 0, 1) * np.clip((dur + 0.45 - t) / 0.45, 0, 1) * (0.85 + 0.15 * np.sin(np.pi * np.clip(t / max(dur, .1), 0, 1)))
    return (tone * (0.8 + 0.2 * np.abs(breath) * 3) + breath * 0.22) * env * vel


def drone(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 2))) / SR
    out = sum(2 * ((hz(m + o) * 2 ** (d / 12) * t + rng.random()) % 1.0) - 1 for o in (0, 7, 12) for d in (-0.06, 0.06))
    out = onepole(onepole(onepole(out, 0.97), 0.95), 0.8)
    return out * np.clip(t / 2.5, 0, 1) * np.clip((dur + 1.8 - t) / 1.8, 0, 1) * vel / 6


def chime(m, vel=1.0):
    t = np.arange(int(SR * 3.5)) / SR
    f = hz(m)
    return sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-t / d) for r, a, d in ((1, 1, 2.2), (2.76, .4, .8), (5.4, .2, .3), (8.9, .08, .1))) * (1 - np.exp(-t / 0.001)) * vel


def taiko(vel=1.0, f0=62):
    t = np.arange(int(SR * 1.2)) / SR
    f = f0 * (1 + 0.6 * np.exp(-t / 0.03))
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.35)
    skin = onepole(rng.normal(0, 1, len(t)), 0.9) * np.exp(-t / 0.05) * 0.3
    return (body + skin) * (1 - np.exp(-t / 0.002)) * vel


def splash(vel=1.0):
    t = np.arange(int(SR * 0.9)) / SR
    n = rng.normal(0, 1, len(t)); n = n - onepole(n, 0.9)
    noise = onepole(n, 0.3) * np.exp(-t / 0.12)
    drops = np.zeros_like(t)
    for k in range(7):  # little pitched droplets after the splash
        t0 = 0.05 + rng.random() * 0.45; f = 900 + rng.random() * 1400; i0 = int(t0 * SR); tt = t[: len(t) - i0]
        drops[i0:] += np.sin(2 * np.pi * np.cumsum(f * (1 + 0.8 * np.exp(-tt / 0.01))) / SR) * np.exp(-tt / 0.03) * 0.3
    return (noise * 0.7 + drops) * vel


def plip(vel=1.0):  # the fish leaving the water
    t = np.arange(int(SR * 0.25)) / SR
    return np.sin(2 * np.pi * np.cumsum(500 * (1 + 2.5 * np.exp(-t / 0.015))) / SR) * np.exp(-t / 0.05) * vel


IN = [50, 51, 55, 57, 58, 62, 63, 67, 69, 70, 74]  # D Eb G A Bb, over two octaves
# 0-5.5 s: the ink drop and the enso
add(koto(62, 3.5, 0.9), 0.8, pan=-0.1, gain=0.22)
add(taiko(0.5, 70), 1.2, gain=0.18)
add(drone(38, 44, 0.9), 0.0, gain=0.3)
for i, tt in enumerate(rng.uniform(0.5, 43, 34)):  # wind chimes, sparse, all the way through
    add(chime(int(rng.choice([86, 87, 91, 93, 94, 98])), 0.5 + rng.random() * 0.4), tt, pan=rng.uniform(-.6, .6), gain=0.025)
# 5.5-20 s: slow koto arpeggios (the ponds), quarter notes
PAT = [[0, 4, 7, 5], [1, 4, 6, 8], [0, 3, 7, 9], [2, 4, 8, 6]]
for bar in range(1, 6):
    for k, ix in enumerate(PAT[bar % 4]):
        add(koto(IN[ix], 2.6, 0.75, bend=1.0 if (bar, k) in ((2, 3), (4, 1)) else 0), at(bar, k + 0.5 * (bar == 1)), pan=-0.35 + 0.15 * k, gain=0.24)
# 20-30 s: the day passes; eighth notes, a soft heartbeat
for bar in range(5, 9):
    for k in range(8):
        ix = PAT[bar % 4][k % 4] + (1 if k >= 4 else 0)
        add(koto(IN[min(ix, len(IN) - 1)], 1.8, 0.7 if k % 2 == 0 else 0.5), at(bar, k * .5) + 0.13, pan=-0.3 + 0.08 * k, gain=0.14)
    for b in (0, 2.5): add(taiko(0.6 if b == 0 else 0.4), at(bar, b) + 0.13, gain=0.2)
# the shakuhachi melody (original), entering with the first leap
MEL = [(12.6, 69, 1.6), (14.4, 70, .8), (15.2, 69, 1.0), (16.4, 67, 1.6), (18.2, 62, 1.6),
       (20.4, 69, 1.0), (21.5, 74, 1.4), (23.1, 70, .8), (24.0, 69, 1.6), (25.8, 67, 1.0), (26.9, 70, 1.0), (28.0, 69, 2.0),
       (30.8, 62, 2.4), (33.6, 63, 1.0), (34.7, 62, 3.0)]
for t0, m, d in MEL:
    add(flute(m, d * 1.0, 0.9, scoop=m >= 67), t0, pan=0.15, gain=0.13)
# the koi: a plip as it leaves, a splash as it lands
for t0, d in LEAPS:
    add(plip(0.6), t0, pan=-0.2, gain=0.05)
    add(splash(0.9 if d > 1.6 else 0.6), t0 + d, pan=0.2, gain=0.12)
# 30-37 s: calm; long koto notes under the flute
for t0, m in ((30.6, 50), (32.4, 57), (34.2, 55)):
    add(koto(m, 3.5, 0.6), t0, pan=-0.2, gain=0.16)
# 37-44 s: the enso and the seal: a rolled koto chord, a last taiko, a chime
T = 36.6
for i, m in enumerate([38, 50, 57, 62, 63, 69, 74]): add(koto(m, 5.0, 0.8), T + 2.0 + i * 0.09, pan=-0.35 + 0.1 * i, gain=0.16)
add(taiko(0.8, 55), T + 3.2 + 0.25 * 0 + 0.0, gain=0.26)  # with the seal
add(chime(98, 0.8), T + 3.3, pan=0.3, gain=0.05)
add(flute(74, 3.0, 0.7), T + 2.4, pan=0.15, gain=0.09)

n_ir = int(SR * 3.6)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 1.2), 0.4) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.45 * wet
out = out[: int(SR * DUR)]
fi = int(SR * 0.4); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.5); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-ponds.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
