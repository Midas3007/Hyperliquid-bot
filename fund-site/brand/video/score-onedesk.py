# "Keep your phone. Join the fund.": an original, understated cinematic score for a finance film. 96 BPM in
# B-flat: a felt piano motif, a soft string pulse in eighth notes, a cello line, a warm low heartbeat, and a
# single bell at the turn. No drum kit. Timing follows onedesk.html (a bar is 2.5 s): bars 0-1 piano alone,
# the pulse from bar 2, cello and heartbeat from bar 4, a darker turn for "one gap" (bars 8-9), the lift at
# "pool it" (bar 10), full and warm for the terms and the record (bars 12-17), the close from bar 18.
#   python3 score-onedesk.py out.wav
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 96
BEAT = 60 / BPM
BAR = 4 * BEAT  # 2.5 s
DUR = 50.0
rng = np.random.default_rng(96)
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


def felt(m, dur, vel=1.0):  # a felt (soft-hammer) piano: warm partials, quick top roll-off, gentle thump
    t = np.arange(int(SR * (dur + 2.0))) / SR
    f = hz(m); tone = np.zeros_like(t)
    for k in range(1, 7):
        fk = f * k * np.sqrt(1 + 0.0003 * k * k)
        if fk > 9000: break
        tone += np.sin(2 * np.pi * fk * t) / k ** 1.6 * np.exp(-t * (0.5 + 0.9 * k) * (1 + m / 100))
    thump = onepole(rng.normal(0, 1, len(t)), 0.85) * np.exp(-t / 0.02) * 0.08
    damp = np.clip((dur + 0.4 - t) / 0.4, 0, 1) * 0.85 + 0.15 * np.exp(-np.clip(t - dur, 0, None) * 2)
    return onepole((tone + thump) * (1 - np.exp(-t / 0.004)) * damp, 0.45) * vel


def bowed(m, dur, vel=1.0, bright=0.86, attack=0.25):
    t = np.arange(int(SR * (dur + 0.8))) / SR
    vib = 1 + 0.0045 * np.sin(2 * np.pi * (5 + rng.random()) * t + rng.random() * 6) * np.clip((t - 0.3) / 0.5, 0, 1)
    ph = np.cumsum(hz(m) * vib) / SR
    tone = onepole(onepole(2 * (ph % 1.0) - 1, bright), 0.55) + 0.2 * np.sin(2 * np.pi * ph)
    env = np.clip(t / attack, 0, 1) * np.clip((dur + 0.7 - t) / 0.7, 0, 1)
    return tone * env * vel


def pulse(m, vel=1.0):  # a short, soft string spiccato for the eighth-note pulse
    t = np.arange(int(SR * 0.32)) / SR
    ph = hz(m) * t
    tone = onepole(onepole(2 * (ph % 1.0) - 1, 0.8), 0.6)
    return tone * np.clip(t / 0.012, 0, 1) * np.exp(-t / 0.09) * vel


def heartbeat(vel=1.0):
    t = np.arange(int(SR * 0.6)) / SR
    f = 46 + 20 * np.exp(-t / 0.04)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.22) * (1 - np.exp(-t / 0.004)) * vel


def bell(m, vel=1.0):
    t = np.arange(int(SR * 4.0)) / SR
    f = hz(m)
    return sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-t / d) for r, a, d in ((1, 1, 2.6), (2.0, .35, 1.2), (3.01, .2, .6), (4.2, .1, .3))) * (1 - np.exp(-t / 0.001)) * vel


def pad(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.5))) / SR
    out = sum(2 * ((hz(m) * 2 ** (d / 12) * t + rng.random()) % 1.0) - 1 for m in notes for d in (-0.07, 0.0, 0.07))
    out = onepole(onepole(onepole(out, 0.96), 0.94), 0.75)
    return out * np.clip(t / 1.2, 0, 1) * np.clip((dur + 1.2 - t) / 1.2, 0, 1) * vel / (len(notes) * 3)


# the harmony: Bbmaj9 | Gm9 | Ebmaj7 | F6  ... "one gap": Gm | Ebm6 ... the lift: Eb | F | Gm7 | Bb/D, then home
PROG = {0: (34, [50, 57, 60, 62]), 1: (31, [50, 53, 57, 58]), 2: (27, [51, 55, 58, 62]), 3: (29, [50, 53, 57, 60]),
        4: (34, [50, 57, 60, 62]), 5: (31, [50, 53, 57, 58]), 6: (27, [51, 55, 58, 62]), 7: (29, [50, 53, 57, 60]),
        8: (31, [50, 55, 58, 62]), 9: (27, [51, 54, 58, 60]),
        10: (27, [51, 55, 58, 62]), 11: (29, [53, 57, 60, 62]), 12: (31, [50, 53, 57, 58]), 13: (26, [50, 53, 58, 62]),
        14: (27, [51, 55, 58, 62]), 15: (29, [53, 57, 60, 62]), 16: (34, [50, 57, 60, 62]), 17: (29, [53, 57, 60, 64])}
MOTIF = [(0, 2), (1, 3), (1.5, 1), (2.5, 2)]  # (beat, chord-tone index): a gentle four-note figure, played an octave up
for bar in range(18):
    root, v = PROG[bar]
    dark = bar in (8, 9)
    lift = bar >= 10
    # felt piano: the motif every bar, and the bass on beat one
    for b, i in MOTIF: add(felt(v[i] + 12, BEAT * 1.2, 0.75 if b == 0 else 0.6), at(bar, b), pan=0.15, gain=0.16)
    add(felt(root + 12, BAR * .9, 0.7), at(bar), pan=-0.15, gain=0.15)
    add(pad(v, BAR * .98, 0.8), at(bar), gain=0.1 if bar < 4 else 0.14 if not lift else 0.2)
    if bar >= 2:  # the string pulse, eighth notes on the chord's fifth and root
        for k in range(8):
            m = (v[1] if k % 2 == 0 else v[0]) + (12 if lift and k in (3, 7) else 0)
            add(pulse(m, 0.9 if k % 4 == 0 else 0.6), at(bar, k * .5), pan=-0.3 + 0.08 * (k % 4), gain=0.07 if not lift else 0.09)
    if bar >= 4:  # cello and the heartbeat
        add(bowed(root + 12, BAR * .97, 0.8, bright=0.8), at(bar), pan=-0.25, gain=0.09 if not dark else 0.12)
        for b in (0, 2.5) if not dark else (0, 1, 2.5): add(heartbeat(0.9), at(bar, b), gain=0.22)
    if lift:  # warm violins hold the chord
        add(bowed(v[2] + 12, BAR * .98, 0.7), at(bar), pan=0.25, gain=0.06)
        add(bowed(v[3] + 12, BAR * .98, 0.7), at(bar), pan=0.3, gain=0.05)
# a melody on top for the terms and the record (original), on violin
MEL = {12: [(0, 74, 2), (2, 77, 2)], 13: [(0, 74, 1.5), (1.5, 72, .5), (2, 70, 2)], 14: [(0, 70, 2), (2, 74, 2)],
       15: [(0, 77, 3), (3, 79, 1)], 16: [(0, 77, 2), (2, 74, 2)], 17: [(0, 72, 4)]}
for bar, notes in MEL.items():
    for b, m, d in notes: add(bowed(m, d * BEAT * .97, 0.85, attack=0.3), at(bar, b), pan=0.18, gain=0.11)
# the turn: one bell as the phones become the desk
add(bell(82, 0.8), at(10) + 0.05, pan=0.3, gain=0.07); add(bell(77, 0.5), at(10, 1.5), pan=0.3, gain=0.04)
# the close: Bbmaj9 on felt piano and strings, a last bell
T = at(18)
for i, m in enumerate([34, 46, 53, 57, 60, 62, 65, 69]): add(felt(m, 5.5, 0.7), T + i * 0.06, pan=-0.3 + 0.08 * i, gain=0.15)
add(pad([50, 57, 60, 62, 65], 5.5, 0.9), T, gain=0.22); add(bowed(46, 5.0, 0.8, bright=0.8), T, pan=-0.25, gain=0.09)
add(bell(86, 0.7), T + 1.2, pan=0.3, gain=0.05)

n_ir = int(SR * 3.0)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.95), 0.5) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.38 * wet
out = out[: int(SR * DUR)]
fi = int(SR * 0.4); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.5); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-onedesk.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
