# "The Movement": an original chamber piece. 60 BPM, one soft clock tick a second: harp arpeggios, pizzicato
# strings, a solo cello melody and a celesta, in D minor turning to F major. Mellow and melodic, no guitars.
#   python3 score-movement.py out.wav
# A bar is 4 s. Timing follows movement.html: bars 0-1 ticks and harp (the watch face), bar 2 onward the steps
# (one per bar, a celesta chime on each change), bar 9 the wide shot, bar 10 the end card.
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BEAT = 1.0
BAR = 4.0
DUR = 44.0
rng = np.random.default_rng(7)
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


def pluck(m, dur=2.5, bright=0.5):  # Karplus-Strong: harp / pizzicato
    f = hz(m); n = int(SR * dur); p = max(2, int(SR / f))
    buf = rng.uniform(-1, 1, p); buf = onepole(buf, 1 - bright)
    out = np.zeros(n); k = 0
    for i in range(n):
        out[i] = buf[k]; nk = (k + 1) % p; buf[k] = 0.4985 * (buf[k] + buf[nk]); k = nk
    return out * (1 - np.exp(-np.arange(n) / SR / 0.002))


def cello(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.6))) / SR
    vib = 1 + 0.005 * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - 0.25) / 0.4, 0, 1)
    ph = np.cumsum(hz(m) * vib) / SR
    saw = 2 * (ph % 1.0) - 1
    tone = onepole(onepole(saw, 0.82), 0.6) + 0.3 * np.sin(2 * np.pi * ph)
    env = np.clip(t / 0.18, 0, 1) * np.clip((dur + 0.5 - t) / 0.5, 0, 1) * (0.85 + 0.15 * np.sin(np.pi * clip01(t / max(dur, .1))))
    return tone * env * vel


def clip01(x): return np.clip(x, 0, 1)


def celesta(m, vel=1.0):
    t = np.arange(int(SR * 2.2)) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.9) + 0.35 * np.sin(2 * np.pi * f * 4 * t) * np.exp(-t / 0.2) + 0.15 * np.sin(2 * np.pi * f * 7.1 * t) * np.exp(-t / 0.05)
    return tone * (1 - np.exp(-t / 0.001)) * vel


def tick(accent=False):  # a soft, woody watch tick
    t = np.arange(int(SR * 0.05)) / SR
    n = rng.normal(0, 1, len(t)); n = n - onepole(n, 0.7)
    body = np.sin(2 * np.pi * (2900 if accent else 3400) * t) * np.exp(-t / 0.004)
    return (n * np.exp(-t / 0.003) * 0.5 + body) * (0.9 if accent else 0.6)


def pad(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.5))) / SR
    out = sum(2 * ((hz(m) * 2 ** (d / 12) * t + rng.random()) % 1.0) - 1 for m in notes for d in (-0.08, 0.0, 0.08))
    out = onepole(onepole(onepole(out, 0.95), 0.93), 0.7)
    return out * np.clip(t / 1.4, 0, 1) * np.clip((dur + 1.2 - t) / 1.2, 0, 1) * vel / (len(notes) * 3)


# Dm9 | Bbmaj7 | Gm9 | A7sus4 -> A7  |  ... ending on Fmaj9 -> Dmaj9 (a lift at the end)
PROG = [(38, [53, 57, 60, 64]), (34, [53, 57, 62, 65]), (31, [53, 57, 58, 62]), (33, [55, 57, 62, 64])]
for k in range(44):  # the tick, every second; louder on each bar
    add(tick(k % 4 == 0), k + 0.0, pan=0.15, gain=0.09 if k < 40 else 0.05)
for bar in range(10):
    root, v = PROG[bar % 4]
    if bar == 9: root, v = 41, [57, 60, 64, 67]  # Fmaj9 for the wide shot
    arp = [root + 12, v[0], v[1], v[2], v[3], v[2] + 12, v[3], v[1]]
    for k, m in enumerate(arp):  # harp, eighth notes
        add(pluck(m, 2.2, 0.55), at(bar, k * .5), pan=-0.35 + 0.08 * k, gain=0.14 if bar >= 2 else 0.17)
    if bar >= 2:
        for b in (0, 2): add(pluck(root, 1.4, 0.3), at(bar, b), pan=0.0, gain=0.3)   # pizzicato bass
        for b in (1, 3): add(pluck(v[0], 1.0, 0.35), at(bar, b) + 0.01, pan=0.25, gain=0.14)
        add(pad(v, BAR * .98, 0.8), at(bar), gain=0.16)
        add(celesta(v[3] + 24, 0.8), at(bar) + 0.02, pan=0.3, gain=0.07)  # a chime as each step arrives
# the cello melody (original), bars 2-9
MEL = {2: [(0, 62, 2), (2, 64, 1), (3, 65, 1)], 3: [(0, 67, 3), (3, 65, 1)],
       4: [(0, 62, 1.5), (1.5, 60, .5), (2, 58, 2)], 5: [(0, 57, 3), (3, 60, 1)],
       6: [(0, 62, 2), (2, 65, 1), (3, 69, 1)], 7: [(0, 67, 2), (2, 65, 1), (3, 64, 1)],
       8: [(0, 62, 1.5), (1.5, 64, .5), (2, 65, 2)], 9: [(0, 64, 2), (2, 67, 2)]}
for bar, notes in MEL.items():
    for b, m, d in notes:
        add(cello(m - 12, d * BEAT * .97, 0.9), at(bar, b), pan=-0.05, gain=0.13)
# end card: D major lifts out of the minor
T_END = at(10)
for i, m in enumerate([38, 50, 57, 62, 66, 69, 73, 76]):
    add(pluck(m, 3.5, 0.5), T_END + i * 0.07, pan=-0.35 + 0.1 * i, gain=0.16)
add(pad([57, 62, 66, 69], 3.6, 0.9), T_END, gain=0.24); add(cello(50, 3.4, 0.8), T_END, gain=0.12)
add(celesta(86, 0.8), T_END + 0.6, pan=0.3, gain=0.08)

n_ir = int(SR * 2.8)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.9), 0.5) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.38 * wet
out = out[: int(SR * DUR)]
fi = int(SR * 0.3); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.0); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.4
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-movement.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
