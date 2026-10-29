# Original score for the Midas × Kiyoshi Fund promo: a mellow 37 s bossa / 60s lounge piece in F
# (nylon guitar, upright bass, brushes, vibraphone, flute, strings), fully synthesized, no samples.
#   python3 score.py out.wav
# Timing follows the video: guitar alone 0-3.8 s, band from 3.8 s, melody from 7.5 s, strings from 18.8 s,
# a quiet turnaround under the terms (30-32 s) and the final chord as the logo forms at 32 s.
import os
import sys
import numpy as np
from scipy.signal import fftconvolve

SR = 44100
LONG = os.environ.get('SCORE_LONG') == '1'  # the 60 s version for the explainer: the theme twice
DUR = 60.0 if LONG else 37.0
BPM = 127.5
BEAT = 60 / BPM
BAR = 4 * BEAT
rng = np.random.default_rng(7)
mix = np.zeros((int(SR * (DUR + 2)), 2))

hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
at = lambda bar, beat=0.0: bar * BAR + beat * BEAT


def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix):
        return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def onepole(x, a):  # simple low-pass, a in (0,1): higher = darker
    from scipy.signal import lfilter
    return lfilter([1 - a], [1, -a], x)


# ---------- instruments ----------
def nylon(m, dur, vel=1.0):
    """Karplus-Strong pluck at 4x the sample rate (keeps every string in tune), warm nylon-like excitation."""
    from scipy.signal import resample_poly
    OS = 4
    sr = SR * OS
    f = hz(m)
    N = max(2, int(round(sr / f - 0.5)))  # the averaging filter adds half a sample of delay
    n = int(sr * (dur + 0.25))
    burst = onepole(onepole(rng.uniform(-1, 1, N), 0.93), 0.93)  # soft thumb, no pick: dark excitation
    burst -= np.roll(burst, N // 3)  # plucked nearer the middle: rounder tone
    burst *= np.clip(np.arange(N) / (0.004 * sr), 0, 1)  # 4 ms soft attack
    y = np.zeros(n)
    y[:N] = burst * vel
    g = 0.5 * 0.99935  # loss per period, tuned so notes ring about as long as a nylon string
    for k in range(N, n, N):
        e = min(k + N, n)
        prev = y[k - N:e - N]
        prev1 = y[k - N - 1:e - N - 1] if k > N else np.concatenate(([0.0], y[:e - N - 1]))
        y[k:e] = g * (prev + prev1)
    env = np.ones(n)
    end, rel = int(sr * dur), int(0.12 * sr)
    env[end:end + rel] = np.linspace(1, 0, len(env[end:end + rel]))
    env[end + rel:] = 0
    return onepole(resample_poly(y * env, 1, OS), 0.55)


def upright(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.15))) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
    env = (1 - np.exp(-t / 0.006)) * np.exp(-t / 0.55)
    env *= np.clip((dur + 0.12 - t) / 0.12, 0, 1)
    thump = np.exp(-t / 0.02) * np.sin(2 * np.pi * f * 0.5 * t) * 0.4
    return np.tanh(1.3 * (tone * env + thump)) * vel


def vibes(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.6))) / SR
    f = hz(m)
    partials = np.sin(2 * np.pi * f * t) * np.exp(-t / 2.2) \
        + 0.25 * np.sin(2 * np.pi * f * 3.98 * t) * np.exp(-t / 0.35) \
        + 0.08 * np.sin(2 * np.pi * f * 9.2 * t) * np.exp(-t / 0.08)
    trem = 1 - 0.28 * (0.5 + 0.5 * np.sin(2 * np.pi * 5.2 * t))  # motor tremolo
    env = (1 - np.exp(-t / 0.006)) * np.clip((dur + 1.2 - t) / 0.5, 0, 1)  # soft mallets
    return partials * trem * env * vel


def flute(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.25))) / SR
    f = hz(m)
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.0 * t) * np.clip((t - 0.25) / 0.3, 0, 1)
    ph = 2 * np.pi * np.cumsum(f * vib) / SR
    tone = np.sin(ph) + 0.18 * np.sin(2 * ph) + 0.05 * np.sin(3 * ph)
    breath = onepole(rng.normal(0, 1, len(t)), 0.9) * 0.05
    env = np.clip(t / 0.07, 0, 1) * np.clip((dur + 0.2 - t) / 0.2, 0, 1)
    return (tone + breath) * env * vel


def strings(notes, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.8))) / SR
    out = np.zeros(len(t))
    for m in notes:
        for d in (-0.07, 0.0, 0.06):  # detuned saws
            f = hz(m) * 2 ** (d / 12)
            out += 2 * ((f * t + rng.random()) % 1.0) - 1
    out = onepole(onepole(out, 0.93), 0.9)
    env = np.clip(t / 0.6, 0, 1) * np.clip((dur + 0.7 - t) / 0.7, 0, 1)
    return out * env * vel / (len(notes) * 3)


def brush(dur, vel=1.0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    noise = rng.normal(0, 1, n)
    hp = noise - onepole(noise, 0.6)
    env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 1.5
    return hp * env * vel * 0.35


def rim(vel=1.0):
    t = np.arange(int(SR * 0.08)) / SR
    tone = np.sin(2 * np.pi * 1700 * t) * np.exp(-t / 0.012) + 0.6 * np.sin(2 * np.pi * 820 * t) * np.exp(-t / 0.02)
    click = rng.normal(0, 1, len(t)) * np.exp(-t / 0.004)
    return (tone * 0.6 + click * 0.4) * vel


# ---------- harmony ----------
CH = {  # name: (bass root midi, upper voicing)
    'Fmaj9': (41, [57, 60, 64, 67]),
    'Bb13': (46, [56, 62, 67]),
    'Ab13': (44, [54, 60, 65]),
    'Gm9': (43, [58, 62, 65, 69]),
    'C13': (48, [58, 64, 69]),
    'Am7': (45, [55, 60, 64]),
    'D7b9': (50, [54, 60, 63, 69]),
    'C7b9': (48, [52, 58, 61, 67]),
    'Dbmaj7': (49, [53, 56, 60, 65]),
    'C9sus': (48, [58, 62, 65, 69]),
}
# one entry per bar; a list means two chords, half a bar each
PROG = ['Fmaj9', 'Fmaj9',
        'Fmaj9', 'Bb13', 'Fmaj9', 'Ab13', 'Gm9', 'C13', ['Am7', 'D7b9'], ['Gm9', 'C7b9'],
        'Fmaj9', 'Bb13', 'Fmaj9', 'Ab13', 'Dbmaj7', ['Gm9', 'C13'],
        'C9sus']
TURN = 16  # the quiet turnaround bar before the final chord
if LONG:  # A A' (bars 2-15), then A and half of A' again (bars 16-27), turnaround, final chord at 54.6 s
    PROG = PROG[:16] + PROG[2:14] + [PROG[16]]
    TURN = 28
FINAL_BAR = len(PROG)  # 17 -> 32.0 s (29 -> 54.6 s long)


def chords_in(bar):
    c = PROG[bar]
    return [(c[0], 0, 2), (c[1], 2, 2)] if isinstance(c, list) else [(c, 0, 4)]


# ---------- guitar comping (bossa pattern on an eighth-note grid) ----------
PAT = [[0, 3, 5], [2, 5, 7]]  # chord hits, alternating bars (3-3-2 feel)
for bar in range(FINAL_BAR):
    soft = 0.55 if bar < 2 else 0.45 if bar == TURN else 0.75
    for name, b0, blen in chords_in(bar):
        root, voic = CH[name]
        # thumb: root on the chord's first beat, fifth two beats later
        for beat, m in [(b0, root), (b0 + 2, root + 7 if root + 7 <= 52 else root - 5)]:
            if beat < b0 + blen:
                add(nylon(m, BEAT * 1.9, 0.9 * soft), at(bar, beat) + rng.normal(0, 0.004), pan=-0.25, gain=0.85)
        for e in PAT[bar % 2]:
            beat = e / 2
            if b0 <= beat < b0 + blen:
                for i, m in enumerate(voic):  # slight strum
                    add(nylon(m, BEAT * 0.9, 0.55 * soft), at(bar, beat) + i * 0.007 + rng.normal(0, 0.003), pan=-0.3 + 0.1 * i, gain=0.45)

# ---------- upright bass (from bar 2) ----------
for bar in range(2, FINAL_BAR):
    v = 0.6 if bar == TURN else 0.85
    for name, b0, blen in chords_in(bar):
        root = CH[name][0] - 12 if CH[name][0] > 45 else CH[name][0]
        fifth = root + 7
        hits = [(0, root, 1.4), (1.5, root, 0.4), (2, fifth, 1.4), (3.5, root + 12 if blen == 4 else fifth, 0.4)]
        for beat, m, d in hits:
            if beat < blen:
                add(upright(m, BEAT * d, v), at(bar, b0 + beat), pan=0.0, gain=0.27)

# ---------- brushes and rim (from bar 2, lighter in the turnaround) ----------
CLAVE = [[0, 6, 12], [4, 10]]  # sixteenths, 3-2 over two bars
for bar in range(2, FINAL_BAR):
    lvl = 0.5 if bar == TURN else 1.0
    for s16 in range(16):
        acc = 1.0 if s16 in (4, 12) else 0.55 if s16 % 2 == 0 else 0.35
        add(brush(BEAT * 0.45, acc * lvl), at(bar, s16 / 4), pan=0.35, gain=0.17)
    for s16 in CLAVE[bar % 2]:
        add(rim(0.8 * lvl), at(bar, s16 / 4), pan=0.25, gain=0.1)

# ---------- melody (original): vibraphone, with flute doubling the second half ----------
MEL = [
    (4, 1.0, .5, 76), (4, 1.5, .5, 77), (4, 2.0, 1.5, 79), (4, 3.5, .5, 76),
    (5, 0, 2, 77), (5, 2, .5, 75), (5, 2.5, 1.5, 72),
    (6, .5, .5, 74), (6, 1, .5, 77), (6, 1.5, 1.5, 81), (6, 3.5, .5, 79),
    (7, 0, 2, 81), (7, 2, 1, 76), (7, 3, 1, 70),
    (8, 0, 1.5, 72), (8, 1.5, .5, 76), (8, 2, 1, 75), (8, 3, 1, 78),
    (9, 0, 1.5, 77), (9, 1.5, .5, 74), (9, 2, 1, 73), (9, 3, 1, 70),
    (10, 0, 2.5, 69), (10, 2.5, .5, 72), (10, 3, 1, 76),
    (11, 0, 1.5, 79), (11, 1.5, .5, 77), (11, 2, 2, 74),
    (12, .5, .5, 81), (12, 1, .5, 79), (12, 1.5, .5, 76), (12, 2, 2, 79),
    (13, 0, 1.5, 77), (13, 1.5, .5, 75), (13, 2, 2, 72),
    (14, 0, 1, 77), (14, 1, 1, 80), (14, 2, 2, 84),
    (15, 0, 1.5, 81), (15, 1.5, .5, 79), (15, 2, 2, 76),
    (16, 2, 2, 74),
]
if LONG:  # the turnaround note moves to the new turnaround; bars 4-13 come back 14 bars later
    MEL = [m for m in MEL if m[0] != 16] + [(b + 14, bt, d, m) for b, bt, d, m in MEL if 4 <= b <= 13] + [(TURN, 2, 2, 74)]
for bar, beat, d, m in MEL:
    add(vibes(m, BEAT * d, 0.9), at(bar, beat), pan=0.3, gain=0.17)
    if 10 <= bar <= 15 or (LONG and 18 <= bar < TURN):
        add(flute(m, BEAT * d * 0.95, 0.8), at(bar, beat) + 0.01, pan=-0.15, gain=0.1)

# ---------- strings (from bar 10, swelling into the record scene) ----------
for bar in range(10, FINAL_BAR):
    for name, b0, blen in chords_in(bar):
        lvl = 0.5 + 0.5 * min(1, (bar - 10) / 4)
        add(strings([m + 12 for m in CH[name][1]], BEAT * blen, lvl), at(bar, b0), pan=0.0, gain=1.1)

# ---------- ending: Fmaj9 on the logo, vibes arpeggio, everything rings out ----------
T_END = at(FINAL_BAR)
add(upright(29, 3.5, 0.9), T_END, gain=0.27)  # low F
for i, m in enumerate([41, 48, 57, 60, 64, 67]):
    add(nylon(m, 4.5, 0.6), T_END + i * 0.045, pan=-0.3 + 0.1 * i, gain=0.65)
for i, m in enumerate([65, 69, 72, 76, 79, 84, 88]):
    add(vibes(m, 2.5, 0.8), T_END + 0.35 + i * BEAT * 0.25, pan=0.3, gain=0.13)
add(strings([57 + 12, 64 + 12, 67 + 12, 72 + 12], 4.0, 0.8), T_END, gain=0.9)
add(rim(0.5), T_END, pan=0.25, gain=0.08)

# ---------- room, master ----------
n_ir = int(SR * 1.8)
tt = np.arange(n_ir) / SR
ir = np.stack([rng.normal(0, 1, n_ir) * np.exp(-tt / 0.6) for _ in range(2)], axis=1)
ir[:, 0] = onepole(ir[:, 0], 0.5)
ir[:, 1] = onepole(ir[:, 1], 0.5)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.42 * wet
out = np.stack([onepole(out[:, c], 0.3) for c in range(2)], axis=1)  # warm top end
out = out[: int(SR * DUR)]
fade = int(SR * 1.4)
out[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 1.5
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.89

import wave
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score.wav', 'wb') as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
