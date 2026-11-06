# "Keep your phone. Join the fund.": an original, mellow amapiano groove at 112 BPM in F minor. Log drum bass,
# soft kick, shaker sixteenths, a rim on the off-beats, jazzy electric-piano chords, a warm pad, and a breathy
# lead. No guitars. Timing follows onedesk.html: bars 0-1 keys and pad, the groove from bar 2, the log drum from
# bar 4, a one-bar drop at the turn (bar 10), full groove with the lead from bar 11, softer for the proof
# (bars 16-18), the end chord from bar 19.
#   python3 score-onedesk.py out.wav
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 112
BEAT = 60 / BPM
BAR = 4 * BEAT  # 2.143 s
DUR = 48.0
rng = np.random.default_rng(112)
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


def logdrum(m, dur, vel=1.0):  # the amapiano log drum: a pitched, gliding, slightly driven sine
    t = np.arange(int(SR * (dur + 0.15))) / SR
    f = hz(m) * (1 + 0.35 * np.exp(-t / 0.025))
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.25 * np.sin(4 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.08)
    env = (1 - np.exp(-t / 0.003)) * np.exp(-t / 0.32) * np.clip((dur + 0.12 - t) / 0.12, 0, 1)
    return np.tanh(2.2 * tone * env) * vel


def kick(vel=1.0):
    t = np.arange(int(SR * 0.35)) / SR
    f = 50 + 70 * np.exp(-t / 0.03)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.13) * (1 - np.exp(-t / 0.002)) * vel


def shaker(vel=1.0):
    t = np.arange(int(SR * 0.08)) / SR
    n = rng.normal(0, 1, len(t)); n = n - onepole(n, 0.88)
    return n * np.sin(np.pi * np.clip(t / 0.08, 0, 1)) ** 2 * vel * 0.4


def rim(vel=1.0):
    t = np.arange(int(SR * 0.08)) / SR
    return (np.sin(2 * np.pi * 1750 * t) * np.exp(-t / 0.012) + onepole(rng.normal(0, 1, len(t)), 0.5) * np.exp(-t / 0.006) * 0.4) * vel


def keys(m, dur, vel=1.0):  # electric piano
    t = np.arange(int(SR * (dur + 1.2))) / SR
    f = hz(m)
    idx = (0.9 * np.exp(-t / 0.25) + 0.15) * vel
    tone = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
    env = (1 - np.exp(-t / 0.006)) * np.exp(-t / 1.6) * np.clip((dur + 1.0 - t) / 0.5, 0, 1)
    return onepole(tone * env * (1 - 0.1 * (0.5 + 0.5 * np.sin(2 * np.pi * 4.5 * t))), 0.35) * vel


def pad(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.2))) / SR
    out = sum(2 * ((hz(m) * 2 ** (d / 12) * t + rng.random()) % 1.0) - 1 for m in notes for d in (-0.08, 0.0, 0.08))
    out = onepole(onepole(onepole(out, 0.95), 0.93), 0.7)
    return out * np.clip(t / 0.9, 0, 1) * np.clip((dur + 1.0 - t) / 1.0, 0, 1) * vel / (len(notes) * 3)


def lead(m, dur, vel=1.0):  # a soft, breathy whistle-like lead
    t = np.arange(int(SR * (dur + 0.4))) / SR
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.3 * t) * np.clip((t - 0.15) / 0.3, 0, 1)
    ph = 2 * np.pi * np.cumsum(hz(m) * vib) / SR
    breath = onepole(rng.normal(0, 1, len(t)), 0.85) * 0.08
    return (np.sin(ph) + 0.1 * np.sin(2 * ph) + breath) * np.clip(t / 0.05, 0, 1) * np.clip((dur + 0.3 - t) / 0.3, 0, 1) * vel


# Fm9 | Dbmaj7 | Bbm9 | C7sus4  (warm, a little wistful; the turn lifts to Ab)
PROG = [(41, [56, 60, 63, 67]), (37, [56, 60, 61, 65]), (34, [56, 60, 61, 65]), (36, [55, 58, 60, 65])]
LOG = [(0, 0, .5), (0.75, 0, .4), (1.5, 7, .4), (2.5, 0, .5), (3.25, 12, .3), (3.5, 10, .4)]  # (beat, interval, length in beats)
for bar in range(19):
    root, v = PROG[bar % 4]
    drop = bar == 10
    soft = 16 <= bar <= 18
    # keys: syncopated stabs
    for b in (0, 1.5, 2.75) if not drop else (0,):
        for i, m in enumerate(v): add(keys(m, BEAT * (1.2 if b == 0 else .6), 0.6), at(bar, b) + i * 0.01, pan=-0.2 + 0.12 * i, gain=0.09)
    add(pad(v, BAR * .98, 0.8), at(bar), gain=0.14 if not drop else 0.22)
    if bar >= 2 and not drop:
        for k in range(16): add(shaker(1.0 if k % 4 == 2 else 0.5), at(bar, k * .25), pan=0.35, gain=0.1)
        for b in (1, 3): add(rim(0.7), at(bar, b) + 0.01, pan=-0.25, gain=0.07 if not soft else 0.05)
        for b in (0, 2): add(kick(0.9), at(bar, b), gain=0.26 if not soft else 0.18)
    if bar >= 4 and not drop:
        for b, iv, ln in LOG: add(logdrum(root - 12 + iv if root > 38 else root + iv, ln * BEAT, 0.9), at(bar, b), gain=0.26 if not soft else 0.18)
    if drop:  # a soft riser into the turn
        t = np.arange(int(SR * BAR)) / SR
        n = rng.normal(0, 1, len(t)); riser = onepole(n - onepole(n, 0.9), 0.4) * (t / BAR) ** 2
        add(riser, at(bar), pan=0.0, gain=0.08)
# the lead (original), from the turn
MEL = {11: [(0, 72, 1), (1, 75, .5), (1.5, 77, 1.5), (3, 75, 1)], 12: [(0, 72, 1.5), (1.5, 70, .5), (2, 68, 2)],
       13: [(0, 72, 1), (1, 75, .5), (1.5, 80, 1.5), (3, 79, 1)], 14: [(0, 77, 2), (2, 75, 1), (3, 72, 1)],
       15: [(0, 75, 1), (1, 77, .5), (1.5, 79, 1), (2.5, 77, .5), (3, 75, 1)], 16: [(0, 72, 3)],
       18: [(0, 75, 2), (2, 77, 2)]}
for bar, notes in MEL.items():
    for b, m, d in notes: add(lead(m, d * BEAT * .95, 0.85), at(bar, b), pan=0.15, gain=0.12)
# a short sparkle at the turn
for i, m in enumerate([80, 84, 87, 92]): add(keys(m, .8, 0.6), at(10, 3) + i * 0.09, pan=0.3, gain=0.06)
# the end: Abmaj9 rings out, a last log drum
T = at(19)
for i, m in enumerate([44, 56, 60, 63, 67, 70, 72]): add(keys(m, 4.5, 0.6), T + i * 0.04, pan=-0.3 + 0.1 * i, gain=0.11)
add(pad([56, 60, 63, 67, 70], 5.0, 0.9), T, gain=0.24); add(logdrum(32, 1.2, 0.9), T, gain=0.28)
for k in range(8): add(shaker(0.6 - k * 0.06), T + k * BEAT * .5, pan=0.35, gain=0.08)

n_ir = int(SR * 2.0)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.6), 0.55) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.3 * wet
out = out[: int(SR * DUR)]
fi = int(SR * 0.3); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.2); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.4
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-onedesk.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
