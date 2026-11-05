# "Aurum": an original modern-classical score for the gold-dust film. 70 BPM: a felt-piano ostinato in A minor
# that turns to C major, a bowed cello line, a soft low pulse, strings that build, and a last C major chord.
# Quiet, cinematic, nothing like the lounge scores. Fully synthesized.
#   python3 score-aurum.py out.wav
# A bar is 3.43 s; the film's formations change on the bar lines (particles.html).
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 70
BEAT = 60 / BPM
BAR = 4 * BEAT
DUR = 44.0
rng = np.random.default_rng(77)
mix = np.zeros((int(SR * (DUR + 4)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)


def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix):
        return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def felt(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 2.5))) / SR
    f = hz(m)
    tone = sum(a * np.sin(2 * np.pi * f * k * (1 + 0.0003 * k * k) * t) * np.exp(-t * (0.5 + 0.8 * k)) for k, a in [(1, 1), (2, .38), (3, .15), (4, .06)])
    env = (1 - np.exp(-t / 0.01)) * np.clip((dur + 2.3 - t) / 0.9, 0, 1)
    return onepole(tone * env, 0.5) * vel


def cello(m, dur, vel=1.0):
    """Bowed: a saw with a slow bow attack, vibrato that grows, and a warm body filter."""
    t = np.arange(int(SR * (dur + 0.8))) / SR
    vib = 1 + 0.005 * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - 0.3) / 0.6, 0, 1)
    ph = np.cumsum(hz(m) * vib) / SR
    saw = 2 * (ph % 1.0) - 1
    body = onepole(onepole(saw, 0.8), 0.6)
    bow = onepole(rng.normal(0, 1, len(t)), 0.95) * 0.04
    env = np.clip(t / 0.35, 0, 1) ** 1.4 * np.clip((dur + 0.7 - t) / 0.7, 0, 1)
    return (body + bow) * env * vel


def strings(notes, dur, vel=1.0, attack=1.5):
    t = np.arange(int(SR * (dur + 2))) / SR
    out = np.zeros(len(t))
    for m in notes:
        for d in (-0.07, 0.0, 0.06):
            out += 2 * ((hz(m) * 2 ** (d / 12) * t + rng.random()) % 1.0) - 1
    out = onepole(onepole(onepole(out, 0.95), 0.93), 0.65)
    env = np.clip(t / attack, 0, 1) ** 1.6 * np.clip((dur + 1.8 - t) / 1.8, 0, 1)
    return out * env * vel / (len(notes) * 3)


def pulse(vel=1.0):  # a soft low heartbeat
    t = np.arange(int(SR * 0.9)) / SR
    return np.sin(2 * np.pi * 55 * t) * np.exp(-t / 0.35) * (1 - np.exp(-t / 0.03)) * vel


# Am | F | C | G  (twice), then F | G | Am | C, and the final C
PROG = [(45, [57, 60, 64]), (41, [57, 60, 65]), (48, [55, 60, 64]), (43, [55, 59, 62])] * 2 + [(41, [57, 60, 65]), (43, [55, 59, 62]), (45, [57, 60, 64]), (48, [55, 60, 64])]
for bar, (root, tri) in enumerate(PROG):
    # felt piano ostinato: eighths through the chord, low to high and back
    arp = [tri[0], tri[1], tri[2], tri[1] + 12, tri[2], tri[1], tri[0] + 12, tri[2]]
    for k, m in enumerate(arp):
        add(felt(m, BEAT * .8, 0.55 + 0.12 * (k == 0)), at(bar, k * .5), pan=-0.35 + 0.1 * k, gain=0.16)
    add(felt(root - 12, BAR * .9, 0.7), at(bar), pan=-0.1, gain=0.18)
    if bar >= 2:
        for b in (0, 2):
            add(pulse(0.9), at(bar, b), gain=0.22)
    if bar >= 4:
        lift = 1.0 if bar < 8 else 1.5
        add(strings([m + 12 for m in tri], BAR * .98, 0.9, attack=1.6), at(bar), gain=0.36 * lift)
# the cello melody (original), from bar 2
MEL = {2: [(0, 52, 2), (2, 55, 2)], 3: [(0, 59, 3), (3, 57, 1)], 4: [(0, 57, 2), (2, 60, 2)], 5: [(0, 64, 3), (3, 62, 1)],
       6: [(0, 60, 2), (2, 59, 1), (3, 57, 1)], 7: [(0, 55, 4)], 8: [(0, 57, 2), (2, 60, 1), (3, 64, 1)], 9: [(0, 67, 3), (3, 65, 1)],
       10: [(0, 64, 2), (2, 62, 2)], 11: [(0, 60, 4)]}
for bar, notes in MEL.items():
    for b, m, d in notes:
        add(cello(m, d * BEAT * .97, 0.9), at(bar, b), pan=0.1, gain=0.2)
# ending: C major rings out
T_END = at(12)
for i, m in enumerate([36, 48, 55, 60, 64, 67, 72]):
    add(felt(m, 4.5, 0.6), T_END + i * 0.06, pan=-0.3 + 0.1 * i, gain=0.16)
add(strings([60, 64, 67, 72], 4.5, 0.8, attack=1.0), T_END, gain=0.45)
add(cello(48, 4.0, 0.8), T_END, gain=0.18)

n_ir = int(SR * 3.2)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 1.15), 0.55) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.55 * wet
out = np.stack([onepole(out[:, c], 0.3) for c in range(2)], axis=1)
out = out[: int(SR * DUR)]
fi = int(SR * 0.3); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.6); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.4
out = np.tanh(out / np.max(np.abs(out)) * 0.85) / np.tanh(0.85) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-aurum.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
