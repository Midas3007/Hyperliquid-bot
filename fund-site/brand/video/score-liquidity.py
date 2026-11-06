# "Fiat Lux": an original, soft and luminous piece for "Let There Be Liquidity". 70 BPM in D-flat major: a
# wordless choir ("ooh", formant-filtered), felt piano, celesta, warm strings, and a soft harp sweep on every
# morph. No drums. A bar is 3.43 s; the morphs land on bars 2, 4, 6, 8, 10 and 12 (liquidity.html).
#   python3 score-liquidity.py out.wav
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 70
BEAT = 60 / BPM
BAR = 4 * BEAT
DUR = 48.0
rng = np.random.default_rng(1508)
mix = np.zeros((int(SR * (DUR + 5)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT


def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix): return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def reson(x, f, bw):
    r = np.exp(-np.pi * bw / SR)
    return lfilter([1 - r], [1, -2 * r * np.cos(2 * np.pi * f / SR), r * r], x)


def choir(notes, dur, vel=1.0):  # several detuned voices per note, through "oo" formants (~300, ~870 Hz)
    t = np.arange(int(SR * (dur + 2.0))) / SR
    src = np.zeros_like(t)
    for m in notes:
        for v in range(4):
            vib = 1 + 0.004 * np.sin(2 * np.pi * (4.6 + rng.random()) * t + rng.random() * 6)
            ph = np.cumsum(hz(m) * 2 ** ((rng.random() - .5) * 0.12 / 12) * vib) / SR
            src += 2 * (ph % 1.0) - 1
    voice = reson(src, 310, 90) * 1.0 + reson(src, 870, 140) * 0.45 + reson(src, 2300, 220) * 0.08
    breath = onepole(rng.normal(0, 1, len(t)), 0.7) * 0.02
    env = np.clip(t / 1.4, 0, 1) ** 1.5 * np.clip((dur + 1.8 - t) / 1.8, 0, 1)
    return (voice / (len(notes) * 4) * 6 + breath) * env * vel


def felt(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 2.2))) / SR
    f = hz(m); tone = np.zeros_like(t)
    for k in range(1, 7):
        fk = f * k * np.sqrt(1 + 0.0003 * k * k)
        if fk > 9000: break
        tone += np.sin(2 * np.pi * fk * t) / k ** 1.7 * np.exp(-t * (0.45 + 0.85 * k) * (1 + m / 100))
    damp = np.clip((dur + 0.5 - t) / 0.5, 0, 1) * 0.85 + 0.15 * np.exp(-np.clip(t - dur, 0, None) * 2)
    return onepole(tone * (1 - np.exp(-t / 0.005)) * damp, 0.5) * vel


def celesta(m, vel=1.0):
    t = np.arange(int(SR * 2.6)) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) * np.exp(-t / 1.1) + 0.3 * np.sin(2 * np.pi * f * 4 * t) * np.exp(-t / 0.18) + 0.1 * np.sin(2 * np.pi * f * 7.1 * t) * np.exp(-t / 0.05)
    return tone * (1 - np.exp(-t / 0.001)) * vel


def strings(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.8))) / SR
    out = sum(2 * ((hz(m) * 2 ** (d / 12) * (t + 0.0015 * np.sin(2 * np.pi * 5.2 * t)) + rng.random()) % 1.0) - 1 for m in notes for d in (-0.08, 0.0, 0.08))
    out = onepole(onepole(onepole(out, 0.94), 0.92), 0.65)
    return out * np.clip(t / 1.6, 0, 1) * np.clip((dur + 1.5 - t) / 1.5, 0, 1) * vel / (len(notes) * 3)


def harp(m, vel=1.0):  # Karplus-Strong pluck
    f = hz(m); n = int(SR * 2.2); p = max(2, int(SR / f))
    buf = onepole(rng.uniform(-1, 1, p), 0.45); out = np.zeros(n); k = 0
    for i in range(n):
        out[i] = buf[k]; nk = (k + 1) % p; buf[k] = 0.499 * (buf[k] + buf[nk]); k = nk
    return out * vel


# Dbmaj9 | Bbm9 | Gbmaj7#11 | Ab6sus  (soft and bright); the coin and certificate lift to Ebm9 | Ab13
PROG = [(37, [53, 56, 60, 63]), (34, [53, 56, 60, 61]), (30, [53, 58, 61, 65]), (32, [53, 56, 60, 63])]
LIFT = {8: (39, [54, 58, 61, 65]), 9: (32, [54, 58, 60, 65]), 10: (37, [53, 56, 60, 63]), 11: (30, [53, 58, 61, 65])}
for bar in range(13):
    root, v = LIFT.get(bar, PROG[bar % 4])
    add(choir(v, BAR * .98, 0.9), at(bar), pan=0.0, gain=0.12 if bar < 2 else 0.16)
    add(felt(root + 12, BAR * .95, 0.7), at(bar), pan=-0.15, gain=0.15)
    if bar >= 2:
        for k, b in enumerate((0, 1.5, 2, 3)):  # a slow piano figure
            add(felt(v[[1, 2, 3, 2][k]] + 12, BEAT * 1.3, 0.55), at(bar, b), pan=0.12, gain=0.11)
    if bar >= 4: add(strings(v, BAR * .99, 0.8), at(bar), pan=0.0, gain=0.12 if bar < 8 else 0.16)
    for k in range(4):  # celesta sparkles, sparse
        if rng.random() < (0.35 if bar < 6 else 0.6):
            add(celesta(v[rng.integers(0, 4)] + 24, 0.6), at(bar, k + rng.random() * .5), pan=0.35, gain=0.05)
# a soft harp sweep into each morph (bars 2, 4, 6, 8, 10, 12), ending on the downbeat
for bar in (2, 4, 6, 8, 10, 12):
    root, v = LIFT.get(bar, PROG[bar % 4])
    notes = sorted({n + 12 * o for n in v for o in (0, 1)})
    for i, m in enumerate(notes):
        add(harp(m, 0.6), at(bar) - 1.1 + i * 0.13, pan=-0.4 + 0.1 * i, gain=0.07)
# the melody (original), felt piano high and soft, from the spark
MEL = {6: [(0, 72, 2), (2, 75, 2)], 7: [(0, 77, 3), (3, 75, 1)], 8: [(0, 73, 2), (2, 72, 1), (3, 70, 1)], 9: [(0, 72, 4)],
       10: [(0, 75, 2), (2, 77, 2)], 11: [(0, 80, 3), (3, 77, 1)]}
for bar, notes in MEL.items():
    for b, m, d in notes: add(felt(m, d * BEAT * .95, 0.7), at(bar, b), pan=0.2, gain=0.12)
# the end: Dbmaj9 rings, choir and strings, a last celesta
T = at(12)
add(choir([53, 56, 60, 63, 65], 7.0, 0.9), T, gain=0.18); add(strings([49, 56, 60, 63], 7.0, 0.85), T, gain=0.16)
for i, m in enumerate([25, 37, 49, 56, 60, 63, 65, 68]): add(felt(m, 6.0, 0.6), T + 0.15 + i * 0.07, pan=-0.3 + 0.08 * i, gain=0.12)
for i, m in enumerate([85, 89, 92]): add(celesta(m, 0.6), T + 1.6 + i * 0.4, pan=0.35, gain=0.05)

n_ir = int(SR * 4.0)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 1.4), 0.45) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.5 * wet
out = out[: int(SR * DUR)]
fi = int(SR * 0.6); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 3.0); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-liquidity.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
