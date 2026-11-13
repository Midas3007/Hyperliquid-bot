# "Shareholder Outlook": an original, sunny and soft bossa nova. 128 BPM (a bar is 1.875 s): nylon-string guitar comping
# the bossa rhythm, upright bass on root and fifth, a soft shaker and rim click, a breathy flute melody, warm pads under
# the projection. Fmaj9 | Gm9 C13 | Am7 D7b9 | Gm9 C13sus. 52 s.
#   python3 score-outlook.py out.wav
import sys, wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100; BEAT = 60 / 128; BAR = 4 * BEAT; DUR = 52.0
rng = np.random.default_rng(128)
mix = np.zeros((int(SR * (DUR + 4)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT

def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix): return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan)); mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))

def nylon(m, dur=1.2, bright=0.35):  # Karplus-Strong, dark and warm like a nylon string
    f = hz(m); n = int(SR * dur); p = max(2, int(SR / f))
    buf = onepole(rng.uniform(-1, 1, p), 1 - bright); out = np.zeros(n); k = 0
    for i in range(n):
        out[i] = buf[k]; nk = (k + 1) % p; buf[k] = 0.4975 * (buf[k] + buf[nk]); k = nk
    return onepole(out, 0.35)

def bass(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.3))) / SR; f = hz(m)
    tone = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 5)
    return tone * (1 - np.exp(-t / 0.008)) * np.exp(-t * 2.2) * np.clip((dur + 0.15 - t) / 0.15, 0, 1) * vel

def flute(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.4))) / SR; f = hz(m)
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - .2) / .3, 0, 1)
    ph = np.cumsum(f * vib) / SR
    tone = np.sin(2 * np.pi * ph) + 0.12 * np.sin(4 * np.pi * ph) + onepole(rng.normal(0, 1, len(t)), 0.85) * 0.05
    env = np.clip(t / 0.08, 0, 1) * np.clip((dur + 0.25 - t) / 0.25, 0, 1)
    return tone * env * vel

def pad(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.5))) / SR
    out = sum(2 * ((hz(m) * 2 ** (d / 12) * t + rng.random()) % 1.0) - 1 for m in notes for d in (-0.07, 0, 0.07))
    out = onepole(onepole(onepole(out, 0.95), 0.93), 0.7)
    return out * np.clip(t / 1.5, 0, 1) * np.clip((dur + 1.2 - t) / 1.2, 0, 1) * vel / (len(notes) * 3)

def shaker(vel=1.0):
    t = np.arange(int(SR * 0.08)) / SR; n = rng.normal(0, 1, len(t)); n = n - onepole(n, 0.5)
    return n * np.exp(-t * 60) * vel

def rim(vel=1.0):
    t = np.arange(int(SR * 0.05)) / SR
    return (np.sin(2 * np.pi * 1700 * t) * 0.6 + rng.normal(0, 1, len(t)) * 0.3) * np.exp(-t * 120) * vel

# each bar: two chords (beats 0 and 2), as (bass root, guitar voicing)
PROG = [[(41, [57, 60, 64, 67]), (41, [57, 60, 64, 67])], [(43, [58, 62, 65, 69]), (36, [58, 62, 64, 69])],
        [(45, [55, 60, 64, 67]), (38, [54, 57, 60, 63])], [(43, [58, 62, 65, 69]), (36, [58, 62, 65, 70])]]
NB = int(DUR / BAR) + 1
GUIT = [0, 1.5, 2.5, 3.5]  # the bossa comping rhythm (with the push into the next bar)
CLAVE = [0, 1.5, 3, 5, 6.5]  # a 2-bar bossa clave, in beats
for bar in range(NB - 2):
    chords = PROG[bar % 4]
    intro = bar < 2
    for b in GUIT:
        root, v = chords[0] if b < 2 else chords[1]
        for i, m in enumerate(v): add(nylon(m, 1.0), at(bar, b) + i * 0.011, pan=-0.25 + 0.06 * i, gain=0.07 if not intro else 0.06)
    if not intro:
        for b, (root, _) in ((0, chords[0]), (1.5, chords[0]), (2, chords[1]), (3.5, chords[1])):
            add(bass(root + (7 if b in (1.5, 3.5) else 0), BEAT * (1.4 if b in (0, 2) else 0.45)), at(bar, b), gain=0.26)
        for k in range(16): add(shaker(1.0 if k % 4 == 2 else 0.55), at(bar, k * 0.25), pan=0.35, gain=0.05)
        for c in CLAVE:
            if (bar % 2) * 4 <= c < (bar % 2) * 4 + 4: add(rim(1.0), at(bar, c - (bar % 2) * 4), pan=-0.3, gain=0.05)
    if 13 <= bar <= 22: add(pad(chords[0][1], BAR * .98, 0.8), at(bar), gain=0.08)  # warmth under the projection
# the flute melody (original), from the share-price chart to the cards
MEL = {4: [(0, 72, 1.5), (1.5, 74, .5), (2, 76, 2)], 5: [(0, 77, 1), (1, 76, 1), (2, 74, 2)],
       6: [(0, 72, 1.5), (1.5, 71, .5), (2, 69, 2)], 7: [(0, 70, 3), (3, 72, 1)],
       8: [(0, 76, 1.5), (1.5, 77, .5), (2, 79, 2)], 9: [(0, 81, 1), (1, 79, 1), (2, 77, 2)],
       10: [(0, 76, 1.5), (1.5, 74, .5), (2, 72, 2)], 11: [(0, 74, 4)],
       14: [(0, 77, 2), (2, 79, 2)], 15: [(0, 81, 3), (3, 79, 1)], 16: [(0, 77, 2), (2, 76, 1), (3, 74, 1)], 17: [(0, 76, 4)],
       18: [(0, 79, 1.5), (1.5, 81, .5), (2, 84, 2)], 19: [(0, 82, 2), (2, 81, 2)], 20: [(0, 79, 2), (2, 77, 1), (3, 76, 1)], 21: [(0, 77, 4)]}
for bar, notes in MEL.items():
    for b, m, d in notes: add(flute(m, d * BEAT * .95, 0.8), at(bar, b), pan=0.15, gain=0.06)
# the end card: Fmaj9 on guitar, pad and a last flute note
T = at(NB - 3)
for i, m in enumerate([41, 53, 57, 60, 64, 67, 72]): add(nylon(m, 4.0, 0.3), T + i * 0.07, pan=-0.3 + 0.1 * i, gain=0.08)
add(pad([57, 60, 64, 67], 4.0, 0.9), T, gain=0.12); add(flute(77, 3.0, 0.7), T + 0.5, pan=0.15, gain=0.05)

n_ir = int(SR * 2.4); tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.8), 0.5) for _ in range(2)], axis=1); ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = (mix + 0.32 * wet)[: int(SR * DUR)]
fi = int(SR * 0.5); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 3.0); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-outlook.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
