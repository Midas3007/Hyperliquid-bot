# "The Loom": an original, soft lo-fi piece. 75 BPM: Rhodes electric piano, a warm upright bass, brushes on a
# snare, a very soft kick, a vibraphone melody, and a little vinyl crackle. Fmaj9 | Em7 | Dm9 | G13sus. 40 s.
#   python3 score-loom.py out.wav
import sys, wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100; BEAT = 60 / 75; BAR = 4 * BEAT; DUR = 40.0
rng = np.random.default_rng(75)
mix = np.zeros((int(SR * (DUR + 4)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT

def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix): return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan)); mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))

def rhodes(m, dur, vel=1.0):  # FM-ish electric piano: sine body, bell-like tine that fades fast, gentle tremolo
    t = np.arange(int(SR * (dur + 1.5))) / SR; f = hz(m)
    mod = np.sin(2 * np.pi * f * 14 * t) * 1.6 * np.exp(-t * 9)
    tone = np.sin(2 * np.pi * f * t + mod) + 0.25 * np.sin(2 * np.pi * f * 2 * t) * np.exp(-t * 2)
    env = (1 - np.exp(-t / 0.004)) * np.exp(-t * 0.9) * np.clip((dur + 0.4 - t) / 0.4, 0, 1)
    return tone * env * (1 + 0.12 * np.sin(2 * np.pi * 4.5 * t)) * vel

def bass(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.4))) / SR; f = hz(m)
    tone = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 6)
    return onepole(tone * (1 - np.exp(-t / 0.01)) * np.exp(-t * 1.6) * np.clip((dur + 0.2 - t) / 0.2, 0, 1), 0.3) * vel

def vibes(m, vel=1.0):
    t = np.arange(int(SR * 3.0)) / SR; f = hz(m)
    tone = np.sin(2 * np.pi * f * t) + 0.2 * np.sin(2 * np.pi * f * 4 * t) * np.exp(-t * 3)
    return tone * (1 - np.exp(-t / 0.002)) * np.exp(-t * 1.1) * (1 + 0.2 * np.sin(2 * np.pi * 5.5 * t)) * vel

def brush(vel=1.0):
    t = np.arange(int(SR * 0.25)) / SR; n = rng.normal(0, 1, len(t))
    return onepole(n - onepole(n, 0.6), 0.4) * np.exp(-t * 18) * vel

def kick(vel=1.0):
    t = np.arange(int(SR * 0.35)) / SR
    return np.sin(2 * np.pi * (48 + 60 * np.exp(-t * 30)) * t) * np.exp(-t * 9) * vel

PROG = [(41, [52, 57, 60, 64, 67]), (40, [50, 55, 59, 62, 67]), (38, [53, 57, 60, 64, 65]), (43, [53, 57, 60, 62, 64])]
NBARS = int(DUR / BAR) + 1
for bar in range(NBARS):
    root, v = PROG[bar % 4]
    end = bar >= 10
    # Rhodes: the chord, softly re-struck on the "and" of 2
    for b, vel in ((0, 0.9), (1.5, 0.55)):
        for i, m in enumerate(v): add(rhodes(m, BEAT * (1.5 if b == 0 else 2.4), vel), at(bar, b) + i * 0.012, pan=-0.2 + 0.1 * i, gain=0.05)
    if bar >= 1 and not end:  # bass, the beat and drums come in after the opening bar
        for b, m, d in ((0, root, 1.4), (1.5, root + 7, 0.5), (2, root + 12, 0.9), (3, root + 7, 0.9)): add(bass(m, d * BEAT), at(bar, b), gain=0.28)
        for b in (0, 2.5): add(kick(0.8), at(bar, b), gain=0.14)
        for b in (1, 3): add(brush(1.0), at(bar, b), pan=0.15, gain=0.09)
        for k in range(8): add(brush(0.35), at(bar, k * 0.5) + (0.03 if k % 2 else 0), pan=0.3, gain=0.05)  # lazy swing
# the vibraphone melody (original), bars 3-10
MEL = {3: [(0, 76, 1.5), (1.5, 74, .5), (2, 72, 2)], 4: [(0, 71, 2), (2, 74, 1), (3, 76, 1)],
       5: [(0, 77, 1.5), (1.5, 76, .5), (2, 74, 2)], 6: [(0, 72, 3), (3, 74, 1)],
       7: [(0, 76, 1), (1, 79, 1), (2, 77, 1), (3, 76, 1)], 8: [(0, 74, 2), (2, 71, 2)],
       9: [(0, 72, 1.5), (1.5, 74, .5), (2, 76, 2)], 10: [(0, 77, 4)]}
for bar, notes in MEL.items():
    for b, m, d in notes: add(vibes(m, 0.8), at(bar, b), pan=0.25, gain=0.07)
# the end: Fmaj9 rings out on Rhodes and vibes
T = at(10) + 2 * BAR / 4 * 0
for i, m in enumerate([41, 53, 57, 60, 64, 67, 72]): add(rhodes(m, 5.0, 0.8), at(11) + i * 0.05, pan=-0.3 + 0.1 * i, gain=0.055)
add(vibes(84, 0.6), at(11) + 0.8, pan=0.3, gain=0.06)
# vinyl crackle, very quiet, all the way through
n = len(mix); cr = np.zeros(n); pops = rng.integers(0, n, int(DUR * 22)); cr[pops] = rng.normal(0, 1, len(pops))
cr = onepole(cr, 0.2) * 0.5 + onepole(rng.normal(0, 1, n), 0.97) * 0.02
mix[:, 0] += cr * 0.05; mix[:, 1] += np.roll(cr, 37) * 0.05

n_ir = int(SR * 2.2); tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.7), 0.55) for _ in range(2)], axis=1); ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = onepole(mix + 0.3 * wet, 0.25)  # a soft tape roll-off on the top end
out = out[: int(SR * DUR)]
fi = int(SR * 0.5); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 3.0); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-loom.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
