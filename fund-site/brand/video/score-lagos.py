# "Lagos, 1 a.m.": an original late-night amapiano-style groove for "The desk never sleeps". 112 BPM in D minor:
# a warm log-drum bass, soft kick, swung shakers, a wooden rim on 2 and 4, soft marimba, a humming "ooh" choir pad
# and warm chords. Different from the lounge scores; still no sharp attacks. Fully synthesized.
#   python3 score-lagos.py out.wav
# Timing follows departures.html: bars 0-3 the board (shaker, marimba, pad), bars 4-7 groove in (kick, log drum),
# bars 8-11 the globe (choir), bars 12-14 the clock (full), bar 15 the end card, last chord rings from bar 16.
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter, butter

SR = 44100
BPM = 112
BEAT = 60 / BPM
BAR = 4 * BEAT
DUR = 37.0
SWING = 0.12  # sixteenth swing, as a share of a sixteenth
rng = np.random.default_rng(41)
mix = np.zeros((int(SR * (DUR + 3)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)


def at(bar, step=0.0):  # step in sixteenths, swung
    s = int(step)
    return bar * BAR + (s + (SWING if s % 2 else 0) + (step - s)) * BEAT / 4


def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix):
        return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def logdrum(m, dur, vel=1.0):
    """The amapiano log drum: a sine that drops into its note and swells a little, gently saturated."""
    t = np.arange(int(SR * (dur + 0.2))) / SR
    f = hz(m) * (1 + 0.5 * np.exp(-t / 0.018))
    ph = 2 * np.pi * np.cumsum(f) / SR
    tone = np.sin(ph) + 0.3 * np.sin(2 * ph) * np.exp(-t / 0.12)
    env = (1 - np.exp(-t / 0.006)) * (np.exp(-t / 0.42) * 0.8 + 0.2 * np.exp(-t / 1.2)) * np.clip((dur + 0.15 - t) / 0.15, 0, 1)
    return onepole(np.tanh(2.2 * tone * env), 0.35) * vel


def kick(vel=1.0):
    t = np.arange(int(SR * 0.35)) / SR
    f = 50 + 60 * np.exp(-t / 0.03)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.12) * (1 - np.exp(-t / 0.003)) * vel


def shaker(vel=1.0):
    n = int(SR * 0.07)
    t = np.arange(n) / SR
    noise = rng.normal(0, 1, n)
    hp = noise - onepole(noise, 0.85)
    return onepole(hp, 0.2) * np.exp(-t / 0.02) * (1 - np.exp(-t / 0.004)) * vel * 0.5


def rim(vel=1.0):  # a wooden, rounded rim: no crack
    t = np.arange(int(SR * 0.12)) / SR
    tone = np.sin(2 * np.pi * 820 * t) * np.exp(-t / 0.025) + 0.4 * np.sin(2 * np.pi * 1340 * t) * np.exp(-t / 0.015)
    return onepole(tone * (1 - np.exp(-t / 0.002)), 0.3) * vel


def marimba(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.8))) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.5) + 0.25 * np.sin(2 * np.pi * f * 4 * t) * np.exp(-t / 0.08)
    return onepole(tone * (1 - np.exp(-t / 0.005)), 0.4) * vel


def pad(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.2))) / SR
    out = np.zeros(len(t))
    for m in notes:
        for d in (-0.1, 0.0, 0.09):
            out += 2 * ((hz(m) * 2 ** (d / 12) * t + rng.random()) % 1.0) - 1
    out = onepole(onepole(onepole(out, 0.95), 0.93), 0.7)
    env = np.clip(t / 0.5, 0, 1) * np.clip((dur + 1.0 - t) / 1.0, 0, 1)
    return out * env * vel / (len(notes) * 3)


def choir(notes, dur, vel=1.0):
    """A humming 'ooh': saws through two vowel formants (about 400 Hz and 800 Hz), with slow vibrato."""
    t = np.arange(int(SR * (dur + 1.2))) / SR
    src = np.zeros(len(t))
    for m in notes:
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * t + rng.random() * 6)
        src += 2 * ((np.cumsum(hz(m) * vib) / SR + rng.random()) % 1.0) - 1
    out = np.zeros(len(t))
    for fc, q, a in [(400, 6, 1.0), (800, 8, 0.45), (2600, 10, 0.08)]:
        b, aa = butter(2, [fc / (1 + 1 / q) / (SR / 2), fc * (1 + 1 / q) / (SR / 2)], btype='band')
        out += lfilter(b, aa, src) * a
    env = np.clip(t / 0.8, 0, 1) ** 1.5 * np.clip((dur + 1.0 - t) / 1.0, 0, 1)
    return out * env * vel / len(notes)


# ---------- harmony: D minor, four bars, looped ----------
PROG = [
    (38, [53, 57, 60, 64]),  # Dm9   (F A C E)
    (43, [53, 58, 62, 65]),  # Gm9   (F Bb D F)
    (46, [53, 57, 62, 64]),  # Bbmaj9-ish (F A D E)
    (45, [52, 55, 61, 64]),  # A7sus/A7 (E G C# E)
]
LOG = [0, 3, 6, 10, 11, 14]  # log-drum hits in sixteenths (the amapiano bounce)
MAR = [(0, 0), (3, 2), (6, 1), (8, 3), (11, 2), (14, 1)]  # (sixteenth, voicing index)
BARS = 16
for bar in range(BARS):
    root, voic = PROG[bar % 4]
    add(pad(voic, BAR * 0.98, 0.9), at(bar), gain=0.22)
    for s in range(16):  # shakers throughout, softer on the off sixteenths
        add(shaker(0.9 if s % 4 == 2 else 0.55), at(bar, s), pan=0.35, gain=0.09)
    if bar >= 1:
        for s, v in MAR:
            add(marimba(voic[v] + 12, BEAT * 0.6, 0.7), at(bar, s), pan=-0.3 + 0.1 * v, gain=0.09)
    if bar >= 4:
        for b in (0, 8):
            add(kick(0.9), at(bar, b), gain=0.3)
        for b in (4, 12):
            add(rim(0.8), at(bar, b), pan=-0.15, gain=0.08)
        for k, s in enumerate(LOG):
            m = root - 12 + (7 if k in (2, 4) else 0) + (12 if k == 5 else 0)
            add(logdrum(m, BEAT * 0.9, 0.9), at(bar, s), gain=0.26)
    if bar >= 8:
        add(choir([v + 12 for v in voic[1:]], BAR * 0.98, 0.9), at(bar), pan=0.1, gain=0.5)
    if bar >= 12:  # a soft lead line on marimba, an octave up, for the clock
        for s, v in [(0, 3), (2, 2), (4, 3), (7, 1), (10, 2), (12, 3)]:
            add(marimba(voic[v] + 24, BEAT * 0.5, 0.5), at(bar, s), pan=0.3, gain=0.05)
# ---------- ending: Dm9 rings with the choir ----------
T_END = at(16)
add(pad([50, 53, 57, 60, 64], 4.0, 0.9), T_END, gain=0.26)
add(choir([65, 69, 72, 76], 4.0, 0.9), T_END, gain=0.55)
add(logdrum(26, 2.0, 0.9), T_END, gain=0.26)
for i, m in enumerate([62, 65, 69, 72, 76]):
    add(marimba(m + 12, 1.4, 0.5), T_END + 0.2 + i * BEAT * 0.5, pan=0.3, gain=0.07)

# ---------- room, master ----------
n_ir = int(SR * 2.2)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.7), 0.55) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.35 * wet
out = np.stack([onepole(out[:, c], 0.25) for c in range(2)], axis=1)
out = out[: int(SR * DUR)]
fi = int(SR * 0.2); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.2); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.4
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-lagos.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
