# "Small Wins": an original, warm and gentle piece. 100 BPM in A major: kalimba ostinato, marimba melody, soft
# congas and shaker, a round bass, and warm strings. No guitars. A glassy coin "tink" lands with each of the first
# eight coins (and a low muted one for the losing coin), synced to smallwins.html.
#   python3 score-smallwins.py out.wav
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 100
BEAT = 60 / BPM
BAR = 4 * BEAT  # 2.4 s
DUR = 40.0
rng = np.random.default_rng(260)
mix = np.zeros((int(SR * (DUR + 3)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT


def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix): return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def kalimba(m, vel=1.0):
    t = np.arange(int(SR * 1.6)) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.6) + 0.25 * np.sin(2 * np.pi * f * 5.4 * t) * np.exp(-t / 0.05) + 0.1 * np.sin(2 * np.pi * f * 2.01 * t) * np.exp(-t / 0.3)
    return tone * (1 - np.exp(-t / 0.002)) * vel


def marimba(m, vel=1.0):
    t = np.arange(int(SR * 1.4)) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.45) + 0.3 * np.sin(2 * np.pi * f * 3.9 * t) * np.exp(-t / 0.08) + 0.08 * np.sin(2 * np.pi * f * 9.2 * t) * np.exp(-t / 0.02)
    return onepole(tone * (1 - np.exp(-t / 0.003)), 0.2) * vel


def bass(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.2))) / SR
    tone = np.sin(2 * np.pi * hz(m) * t) + 0.15 * np.sin(4 * np.pi * hz(m) * t)
    return np.tanh(1.3 * tone * (1 - np.exp(-t / 0.01)) * np.exp(-t / 0.7) * np.clip((dur + 0.15 - t) / 0.15, 0, 1)) * vel


def conga(f0, vel=1.0):
    t = np.arange(int(SR * 0.35)) / SR
    f = f0 * (1 + 0.25 * np.exp(-t / 0.01))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.12) * (1 - np.exp(-t / 0.002)) * vel


def shaker(vel=1.0):
    t = np.arange(int(SR * 0.09)) / SR
    n = rng.normal(0, 1, len(t)); n = n - onepole(n, 0.85)
    return n * np.sin(np.pi * np.clip(t / 0.09, 0, 1)) ** 2 * vel * 0.4


def strings(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.5))) / SR
    out = sum(2 * ((hz(m) * 2 ** (d / 12) * (t + 0.002 * np.sin(2 * np.pi * 5 * t)) + rng.random()) % 1.0) - 1 for m in notes for d in (-0.1, 0.0, 0.09))
    out = onepole(onepole(onepole(out, 0.93), 0.9), 0.6)
    return out * np.clip(t / 1.2, 0, 1) * np.clip((dur + 1.2 - t) / 1.2, 0, 1) * vel / (len(notes) * 3)


def tink(vel=1.0, low=False):  # a coin landing in glass
    t = np.arange(int(SR * 1.2)) / SR
    if low: return (np.sin(2 * np.pi * 330 * t) * np.exp(-t / 0.08) + 0.4 * np.sin(2 * np.pi * 466 * t) * np.exp(-t / 0.05)) * vel
    tone = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t / d) for f, a, d in ((2637, 1, .35), (4186 * 1.02, .5, .18), (6200, .3, .08), (1760, .4, .5)))
    return tone * (1 - np.exp(-t / 0.0008)) * vel


# Amaj9 | F#m9 | Dmaj9 | E6sus  (bright, warm)
PROG = [(45, [57, 61, 64, 68, 71]), (42, [54, 57, 61, 64, 68]), (38, [54, 57, 61, 64, 69]), (40, [56, 59, 61, 64, 69])]
ARP = [0, 2, 3, 4, 3, 2, 1, 2]
for bar in range(16):
    root, v = PROG[bar % 4]
    section = 'aside' if 12.5 <= bar < 15 else 'full'
    for k in range(8):  # kalimba ostinato in eighths, all the way through
        m = v[ARP[k]] + (12 if bar >= 8 and section == 'full' and k in (3, 4) else 0)
        add(kalimba(m, 0.9 if k % 2 == 0 else 0.6), at(bar, k * .5) + rng.normal(0, 0.004), pan=-0.35 + 0.1 * (k % 4), gain=0.11)
    if bar >= 2 and section == 'full':
        add(bass(root - 12 if root > 40 else root, BEAT * 1.4), at(bar), gain=0.34)
        add(bass(root - 12 if root > 40 else root, BEAT * 0.5, 0.7), at(bar, 1.5), gain=0.2)
        add(bass(v[0] - 12, BEAT * 1.2, 0.8), at(bar, 2.5), gain=0.22)
        for k in range(16): add(shaker(1.0 if k % 4 == 2 else 0.55), at(bar, k * .25), pan=0.35, gain=0.12)
    if bar >= 4 and section == 'full':
        for b, f, vv in ((0, 200, .9), (1.5, 260, .6), (2, 200, .5), (2.75, 300, .5), (3, 260, .8), (3.5, 200, .5)):
            add(conga(f, vv), at(bar, b), pan=-0.2, gain=0.16)
    if bar >= 8:
        add(strings(v[1:], BAR * .98, 0.9), at(bar), gain=0.2 if section == 'full' else 0.28)
# the melody (original), marimba, bars 4-11 doubled an octave down softly from bar 8
MEL = {4: [(0, 76, 1), (1, 78, .5), (1.5, 80, 1.5), (3, 78, 1)], 5: [(0, 76, 1.5), (1.5, 73, .5), (2, 74, 2)],
       6: [(0, 78, 1), (1, 76, .5), (1.5, 74, 1), (2.5, 73, .5), (3, 71, 1)], 7: [(0, 73, 3), (3, 71, 1)],
       8: [(0, 76, 1), (1, 78, .5), (1.5, 80, 1.5), (3, 83, 1)], 9: [(0, 81, 1.5), (1.5, 80, .5), (2, 78, 2)],
       10: [(0, 78, 1), (1, 80, .5), (1.5, 81, 1), (2.5, 80, .5), (3, 78, 1)], 11: [(0, 76, 2), (2, 73, 1), (3, 76, 1)],
       13: [(0, 80, 2), (2, 81, 2)], 14: [(0, 83, 3), (3, 80, 1)]}
for bar, notes in MEL.items():
    for b, m, d in notes:
        reps = max(1, int(d * 2)) if d >= 1 else 1  # marimba rolls long notes
        for r in range(reps):
            add(marimba(m, 0.9 if r == 0 else 0.45), at(bar, b + r * .5), pan=0.18, gain=0.16)
            if bar >= 8: add(marimba(m - 12, 0.5 if r == 0 else 0.25), at(bar, b + r * .5), pan=-0.1, gain=0.08)
# coin tinks: the first eight, and the losing coin
for i, t0 in enumerate([5.0, 6.2, 7.4, 8.6, 9.8, 11.0, 12.2, 13.4]):
    add(tink(0.9), t0 + 0.55, pan=-0.25, gain=0.07)
add(tink(1.0, low=True), 15.55, pan=-0.2, gain=0.12)
# ending: A major rings out
T_END = at(15.4)
for i, m in enumerate([33, 45, 57, 61, 64, 68, 71, 76]):
    add(kalimba(m, 0.7), T_END + i * 0.05, pan=-0.3 + 0.08 * i, gain=0.12)
add(strings([57, 61, 64, 68], 3.5, 0.9), T_END, gain=0.26); add(bass(33, 3.0), T_END, gain=0.3)

n_ir = int(SR * 2.4)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.75), 0.5) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.32 * wet
out = out[: int(SR * DUR)]
fi = int(SR * 0.3); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.0); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.4
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-smallwins.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
