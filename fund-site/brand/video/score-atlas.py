# "The Atlas": an original slow waltz (3/4 at 84 BPM) in E-flat major. Piano, a string section, a solo violin
# melody and a cello counter-line, with a glockenspiel at the turning points and a soft timpani for the wax seal.
#   python3 score-atlas.py out.wav
# A bar is 2.143 s. Timing follows atlas.html: bars 0-2 piano alone (the map), 3-5 the waltz begins (Lagos),
# 6-8 the violin sets sail, 9-12 by wire, 13-15 full strings (thirty seconds), 16-18 the ledger, bar 19 the seal.
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 84
BEAT = 60 / BPM
BAR = 3 * BEAT
DUR = 48.0
rng = np.random.default_rng(1874)
mix = np.zeros((int(SR * (DUR + 4)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT


def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix): return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def piano(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.6))) / SR
    f = hz(m)
    B = 0.0004  # slight string stiffness: partials run a little sharp
    tone = np.zeros_like(t)
    for k in range(1, 9):
        fk = f * k * np.sqrt(1 + B * k * k)
        if fk > 12000: break
        tone += np.sin(2 * np.pi * fk * t) * (vel ** (0.4 + 0.15 * k)) / k ** 1.15 * np.exp(-t * (0.6 + 0.55 * k) * (1 + m / 90))
    ham = onepole(rng.normal(0, 1, len(t)), 0.6) * np.exp(-t / 0.006) * 0.05
    damp = np.clip((dur + 0.25 - t) / 0.25, 0, 1) * 0.9 + 0.1 * np.exp(-np.clip(t - dur, 0, None) * 3)
    return (tone + ham) * (1 - np.exp(-t / 0.002)) * damp * vel


def bowed(m, dur, vel=1.0, bright=0.82, vib_rate=5.6, vib_depth=0.006):
    t = np.arange(int(SR * (dur + 0.7))) / SR
    vib = 1 + vib_depth * np.sin(2 * np.pi * vib_rate * t + rng.random() * 6) * np.clip((t - 0.25) / 0.5, 0, 1)
    ph = np.cumsum(hz(m) * vib) / SR
    saw = 2 * (ph % 1.0) - 1
    tone = onepole(onepole(saw, bright), 0.5) + 0.25 * np.sin(2 * np.pi * ph)
    bow = onepole(rng.normal(0, 1, len(t)), 0.7) * 0.025
    swell = np.clip(t / 0.22, 0, 1) * np.clip((dur + 0.6 - t) / 0.6, 0, 1) * (0.8 + 0.2 * np.sin(np.pi * np.clip(t / max(dur, 0.1), 0, 1)))
    return (tone + bow) * swell * vel


def section(notes, dur, vel=1.0):  # a string section: several detuned bowed voices per note
    return sum(bowed(m, dur, vel, bright=0.88, vib_rate=5 + rng.random(), vib_depth=0.004) * 0.5 for m in notes for _ in range(2)) / len(notes)


def glock(m, vel=1.0):
    t = np.arange(int(SR * 2.4)) / SR
    f = hz(m)
    return (np.sin(2 * np.pi * f * t) * np.exp(-t / 1.1) + 0.3 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-t / 0.3) + 0.12 * np.sin(2 * np.pi * f * 5.4 * t) * np.exp(-t / 0.08)) * (1 - np.exp(-t / 0.0008)) * vel


def timpani(m, vel=1.0):
    t = np.arange(int(SR * 3.0)) / SR
    f = hz(m) * (1 + 0.05 * np.exp(-t / 0.05))
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.9) + 0.4 * np.sin(2 * np.pi * np.cumsum(f * 1.5) / SR) * np.exp(-t / 0.4)
    hit = onepole(rng.normal(0, 1, len(t)), 0.8) * np.exp(-t / 0.03) * 0.4
    return (tone + hit) * (1 - np.exp(-t / 0.003)) * vel


# Ebmaj7 | Cm9 | Abmaj7 | Bb7sus4-Bb7  (and Gm7 | Cm7 | Fm9 | Bb13 in the middle)
A = [(39, [55, 58, 62, 65]), (36, [55, 58, 62, 63]), (32, [55, 60, 63, 67]), (34, [53, 56, 60, 63])]
M = [(43, [53, 58, 62, 65]), (36, [55, 58, 63, 67]), (29, [56, 60, 63, 67]), (34, [55, 56, 60, 62])]
def chord(bar): return (M if 9 <= bar <= 12 else A)[bar % 4]

for bar in range(19):
    root, v = chord(bar)
    if bar < 3:  # the map unrolls: piano alone, rolled chords and a soft upper line
        for i, m in enumerate([root + 12] + v): add(piano(m, BAR * .95, 0.55), at(bar) + i * 0.06, pan=-0.2 + 0.08 * i, gain=0.16)
        continue
    # the waltz: bass on one, chord on two and three
    add(piano(root, BEAT * 1.1, 0.8), at(bar), pan=-0.15, gain=0.26)
    add(piano(root + 7, BEAT * .6, 0.45), at(bar, 0) + 0.004, pan=-0.15, gain=0.08)
    for b in (1, 2):
        for i, m in enumerate(v[:3]): add(piano(m, BEAT * .7, 0.55 if b == 1 else 0.45), at(bar, b) + i * 0.008, pan=0.1, gain=0.11)
    strings_gain = 0.0 if bar < 4 else 0.14 if bar < 13 else 0.24 if bar < 16 else 0.15
    if strings_gain: add(section(v, BAR * .99, 0.8), at(bar), pan=0.0, gain=strings_gain)
    if bar >= 13: add(bowed(root - 12 + 24, BAR * .95, 0.7, bright=0.8), at(bar), pan=-0.3, gain=0.07)  # cellos
# the violin melody (original): sets sail at bar 6
MEL = {6: [(0, 70, 2), (2, 72, 1)], 7: [(0, 74, 1.5), (1.5, 72, .5), (2, 70, 1)], 8: [(0, 67, 3)],
       9: [(0, 70, 2), (2, 74, 1)], 10: [(0, 77, 2), (2, 75, 1)], 11: [(0, 74, 1.5), (1.5, 72, .5), (2, 70, 1)], 12: [(0, 72, 3)],
       13: [(0, 75, 2), (2, 77, 1)], 14: [(0, 79, 1.5), (1.5, 77, .5), (2, 75, 1)], 15: [(0, 82, 2), (2, 79, 1)],
       16: [(0, 77, 2), (2, 75, 1)], 17: [(0, 74, 1.5), (1.5, 75, .5), (2, 77, 1)], 18: [(0, 75, 3)]}
for bar, notes in MEL.items():
    for b, m, d in notes:
        add(bowed(m, d * BEAT * .98, 0.95), at(bar, b), pan=0.18, gain=0.15)
# a cello counter-line in the full section
CEL = {13: [(0, 51, 3)], 14: [(0, 53, 2), (2, 55, 1)], 15: [(0, 56, 3)], 16: [(0, 55, 2), (2, 53, 1)], 17: [(0, 51, 3)], 18: [(0, 50, 3)]}
for bar, notes in CEL.items():
    for b, m, d in notes: add(bowed(m, d * BEAT * .98, 0.85, bright=0.78, vib_rate=5.0), at(bar, b), pan=-0.25, gain=0.12)
# glockenspiel at the turning points: Lagos, the ship sails, the wire, thirty seconds, the ledger
for bar, m in ((3, 82), (6, 87), (9, 86), (13, 91), (16, 87)):
    add(glock(m, 0.8), at(bar), pan=0.35, gain=0.08)
    add(glock(m - 5, 0.6), at(bar, 1), pan=0.35, gain=0.05)
# the seal (bar 19): timpani and the home chord, ringing out
T = at(19)
add(timpani(39, 1.0), T, gain=0.32)
for i, m in enumerate([27, 39, 51, 55, 58, 62, 67, 70]): add(piano(m, 5.5, 0.7), T + i * 0.05, pan=-0.3 + 0.08 * i, gain=0.15)
add(section([55, 58, 62, 67], 5.5, 0.85), T, gain=0.24); add(bowed(75, 5.0, 0.8), T + 0.1, pan=0.18, gain=0.12)
add(glock(94, 0.7), T + 0.7, pan=0.35, gain=0.06)

n_ir = int(SR * 3.2)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 1.05), 0.45) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.42 * wet
out = out[: int(SR * DUR)]
fi = int(SR * 0.4); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.5); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-atlas.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
