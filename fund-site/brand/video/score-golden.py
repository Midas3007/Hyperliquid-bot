# "Golden Hour": an original warm lo-fi / nu-jazz groove. 92 BPM: electric-piano ninth chords, a laid-back
# soft beat, a round sub bass, vinyl crackle, a warm pad, and a flute + vibraphone melody. Mellow and melodic.
#   python3 score-golden.py out.wav
# A bar is 2.61 s. Timing follows golden.html: bars 0-1 keys and crackle (sunset), beat from bar 2, melody
# bars 4-11 (night, the lights), lift bars 12-14 (dawn), final chord from bar 16.
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 92
BEAT = 60 / BPM
BAR = 4 * BEAT
DUR = 44.0
rng = np.random.default_rng(92)
mix = np.zeros((int(SR * (DUR + 3)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
SWING = 0.1


def at(bar, beat=0.0):
    b = int(beat); fr_ = beat - b
    if abs(fr_ - 0.5) < 1e-6: fr_ = 0.5 + SWING
    return bar * BAR + (b + fr_) * BEAT


def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix): return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def rhodes(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.8))) / SR
    f = hz(m)
    idx = (1.0 * np.exp(-t / 0.28) + 0.18) * vel
    tone = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t)) + 0.05 * np.sin(2 * np.pi * f * 6.9 * t) * np.exp(-t / 0.04)
    trem = 1 - 0.12 * (0.5 + 0.5 * np.sin(2 * np.pi * 4.2 * t))
    env = (1 - np.exp(-t / 0.01)) * np.exp(-t / 2.2) * np.clip((dur + 1.5 - t) / 0.6, 0, 1)
    return onepole(tone * trem * env, 0.4) * vel


def sub(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.25))) / SR
    tone = np.sin(2 * np.pi * hz(m) * t) + 0.12 * np.sin(4 * np.pi * hz(m) * t)
    env = (1 - np.exp(-t / 0.015)) * np.exp(-t / 0.9) * np.clip((dur + 0.2 - t) / 0.2, 0, 1)
    return np.tanh(1.4 * tone * env) * vel


def kick(vel=1.0):
    t = np.arange(int(SR * 0.4)) / SR
    f = 48 + 55 * np.exp(-t / 0.035)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.15) * (1 - np.exp(-t / 0.003)) * vel


def snare(vel=1.0):  # soft and dusty: a low body plus filtered noise, no crack
    t = np.arange(int(SR * 0.3)) / SR
    body = np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.04)
    noise = rng.normal(0, 1, len(t)); n = onepole(noise - onepole(noise, 0.6), 0.55) * np.exp(-t / 0.09)
    return onepole((body * .6 + n * .7) * (1 - np.exp(-t / 0.004)), 0.3) * vel


def hat(vel=1.0):
    t = np.arange(int(SR * 0.06)) / SR
    noise = rng.normal(0, 1, len(t)); hp = noise - onepole(noise, 0.9)
    return onepole(hp, 0.25) * np.exp(-t / 0.015) * vel * 0.5


def pad(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.2))) / SR
    out = sum(2 * ((hz(m) * 2 ** (d / 12) * t + rng.random()) % 1.0) - 1 for m in notes for d in (-0.08, 0.0, 0.07))
    out = onepole(onepole(onepole(out, 0.95), 0.93), 0.7)
    return out * np.clip(t / 0.8, 0, 1) * np.clip((dur + 1.0 - t) / 1.0, 0, 1) * vel / (len(notes) * 3)


def flute(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.3))) / SR
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.0 * t) * np.clip((t - 0.2) / 0.3, 0, 1)
    ph = 2 * np.pi * np.cumsum(hz(m) * vib) / SR
    tone = np.sin(ph) + 0.16 * np.sin(2 * ph) + 0.04 * np.sin(3 * ph)
    breath = onepole(rng.normal(0, 1, len(t)), 0.9) * 0.06
    return (tone + breath) * np.clip(t / 0.07, 0, 1) * np.clip((dur + 0.25 - t) / 0.25, 0, 1) * vel


def vibes(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.4))) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) * np.exp(-t / 1.8) + 0.2 * np.sin(2 * np.pi * f * 3.98 * t) * np.exp(-t / 0.3)
    return tone * (1 - 0.25 * (0.5 + 0.5 * np.sin(2 * np.pi * 5.5 * t))) * (1 - np.exp(-t / 0.006)) * vel


# Fmaj9 | Em7 | Dm9 | G13   (warm, a little wistful)
PROG = [(41, [52, 57, 60, 64, 67]), (40, [50, 55, 59, 62, 67]), (38, [53, 57, 60, 64, 65]), (43, [53, 57, 59, 64, 65])]
BARS = 16
for bar in range(BARS):
    root, voic = PROG[bar % 4]
    for i, m in enumerate(voic):  # keys: on 1, a lazy push on the "and" of 2
        add(rhodes(m, BEAT * 1.6, 0.6), at(bar) + i * 0.015, pan=-0.25 + 0.12 * i, gain=0.12)
        add(rhodes(m, BEAT * 1.0, 0.4), at(bar, 2.5) + i * 0.012, pan=0.25 - 0.12 * i, gain=0.07)
    add(pad(voic[1:], BAR * .98, 0.8), at(bar), gain=0.14 if bar < 12 else 0.22)
    if bar >= 2:
        add(sub(root - 12 if root > 40 else root, BEAT * 1.7, 0.9), at(bar), gain=0.32)
        add(sub(root - 12 if root > 40 else root, BEAT * 0.8, 0.7), at(bar, 2.5), gain=0.22)
        for b in (0, 2.5):
            add(kick(0.9), at(bar, b), gain=0.3)
        for b in (1, 3):
            add(snare(0.8), at(bar, b) + 0.012, pan=0.05, gain=0.14)  # a hair behind the beat
        for k in range(8):
            add(hat(0.9 if k % 2 else 0.55), at(bar, k * .5), pan=0.3, gain=0.07)
# the melody (original): flute with vibraphone underneath, bars 4-11, and a short reprise 12-14
MEL = {4: [(0, 76, 1.5), (1.5, 79, .5), (2, 81, 2)], 5: [(0, 79, 1), (1, 76, 1), (2, 74, 2)],
       6: [(0, 72, 1.5), (1.5, 74, .5), (2, 77, 1.5), (3.5, 76, .5)], 7: [(0, 74, 3), (3, 72, 1)],
       8: [(0, 76, 1.5), (1.5, 79, .5), (2, 84, 2)], 9: [(0, 83, 1), (1, 81, 1), (2, 79, 2)],
       10: [(0, 77, 1.5), (1.5, 76, .5), (2, 74, 1), (3, 72, 1)], 11: [(0, 71, 4)],
       12: [(0, 79, 2), (2, 81, 2)], 13: [(0, 84, 3), (3, 83, 1)], 14: [(0, 81, 4)]}
for bar, notes in MEL.items():
    for b, m, d in notes:
        add(flute(m, d * BEAT * .95, 0.85), at(bar, b), pan=0.12, gain=0.12)
        add(vibes(m - 12, d * BEAT, 0.5), at(bar, b), pan=-0.3, gain=0.05)
# ending: Fmaj9 rings
T_END = at(16)
for i, m in enumerate([41, 53, 57, 60, 64, 67, 72]):
    add(rhodes(m, 3.6, 0.55), T_END + i * 0.04, pan=-0.3 + 0.1 * i, gain=0.12)
add(pad([57, 60, 64, 67], 3.8, 0.8), T_END, gain=0.22); add(sub(29, 3.0, 0.8), T_END, gain=0.3)
# vinyl crackle all the way through
n = len(mix); crackle = np.zeros(n); idx = rng.integers(0, n, int(DUR * 35)); crackle[idx] = rng.normal(0, 1, len(idx))
crackle = onepole(crackle, 0.4) * 0.5 + onepole(rng.normal(0, 1, n), 0.995) * 0.004
mix[:, 0] += crackle * 0.06; mix[:, 1] += np.roll(crackle, 777) * 0.06

n_ir = int(SR * 2.2)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.7), 0.55) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.35 * wet
out = np.stack([onepole(out[:, c], 0.35) for c in range(2)], axis=1)  # a warm, lo-fi top
out = out[: int(SR * DUR)]
fi = int(SR * 0.4); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.2); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.4
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-golden.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
