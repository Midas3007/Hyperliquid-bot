# "Possible Again": an original, hopeful score for "Newton's 4th Law". Same warm palette as Riviera (soft felt
# piano, electric piano, strings, a breathy lead), but brighter and building: 84 BPM in E major, a rising four-note
# bell motif, a soft heartbeat kick, and quiet clicks on every beat while the cradle swings. No sharp attacks.
#   python3 score-possible.py out.wav
# Timing follows newton.html: bars 0-1 piano alone (the three laws), bars 2-6 the cradle (clicks on the beat),
# bars 7-9 the fourth law (motif, full band), bars 10-11 the end card, final chord from bar 12.
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 84
BEAT = 60 / BPM
BAR = 4 * BEAT
DUR = 37.5
rng = np.random.default_rng(23)
mix = np.zeros((int(SR * (DUR + 3)), 2))
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
    """Felt piano: a few inharmonic-ish partials, soft 9 ms hammer, long gentle decay."""
    t = np.arange(int(SR * (dur + 2.2))) / SR
    f = hz(m)
    tone = sum(a * np.sin(2 * np.pi * f * k * (1 + 0.0004 * k * k) * t) * np.exp(-t * (0.6 + 0.9 * k)) for k, a in [(1, 1), (2, .42), (3, .18), (4, .08)])
    env = (1 - np.exp(-t / 0.009)) * np.clip((dur + 2.0 - t) / 0.8, 0, 1)
    return onepole(tone * env, 0.45) * vel


def epiano(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.6))) / SR
    f = hz(m)
    idx = (0.9 * np.exp(-t / 0.3) + 0.2) * vel
    tone = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
    env = (1 - np.exp(-t / 0.012)) * np.exp(-t / 2.0) * np.clip((dur + 1.4 - t) / 0.6, 0, 1)
    return onepole(tone * env, 0.35) * vel


def bell(m, dur, vel=1.0):
    """A glassy bell for the motif: FM at a 3.5 ratio, 10 ms attack so it rings rather than clicks."""
    t = np.arange(int(SR * (dur + 2.5))) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t + 1.2 * np.exp(-t / 0.6) * np.sin(2 * np.pi * f * 3.5 * t))
    env = (1 - np.exp(-t / 0.01)) * np.exp(-t / 1.6)
    return onepole(tone * env, 0.25) * vel


def strings(notes, dur, vel=1.0, attack=1.0):
    t = np.arange(int(SR * (dur + 1.5))) / SR
    out = np.zeros(len(t))
    for m in notes:
        for d in (-0.08, 0.0, 0.07):
            f = hz(m) * 2 ** (d / 12)
            out += 2 * ((f * t + rng.random()) % 1.0) - 1
    out = onepole(onepole(onepole(out, 0.94), 0.92), 0.6)
    env = np.clip(t / attack, 0, 1) ** 1.6 * np.clip((dur + 1.4 - t) / 1.4, 0, 1)
    return out * env * vel / (len(notes) * 3)


def bass(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.3))) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) + 0.22 * np.sin(4 * np.pi * f * t)
    env = (1 - np.exp(-t / 0.02)) * np.exp(-t / 1.1) * np.clip((dur + 0.25 - t) / 0.25, 0, 1)
    return onepole(np.tanh(tone * env), 0.4) * vel


def kick(vel=1.0):  # a soft heartbeat, felt more than heard
    t = np.arange(int(SR * 0.45)) / SR
    f = 46 + 40 * np.exp(-t / 0.04)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.16) * (1 - np.exp(-t / 0.004)) * vel


def click(vel=1.0):  # the cradle: a small, rounded "tock" of coin on coin
    t = np.arange(int(SR * 0.12)) / SR
    tone = np.sin(2 * np.pi * 2300 * t) * np.exp(-t / 0.018) + 0.6 * np.sin(2 * np.pi * 3550 * t) * np.exp(-t / 0.01)
    return onepole(tone * (1 - np.exp(-t / 0.0015)), 0.3) * vel


def brush(dur, vel=1.0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    noise = rng.normal(0, 1, n)
    return onepole(noise - onepole(noise, 0.7), 0.5) * np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2 * vel * 0.3


# ---------- harmony: E major, eight bars, a rising, hopeful loop ----------
PROG = [
    (40, [56, 59, 63, 66]),  # Emaj9 (G# B D# F#)
    (39, [54, 59, 63, 66]),  # B/D#
    (37, [56, 59, 63, 64]),  # C#m9
    (45, [56, 61, 64, 68]),  # Amaj9
    (44, [56, 59, 64, 66]),  # E/G#
    (42, [57, 61, 64, 68]),  # F#m11
    (45, [57, 59, 64, 66]),  # Asus2
    (47, [54, 57, 61, 64]),  # B13sus
]
BARS = 12

for bar in range(BARS):
    root, voic = PROG[bar % 8]
    # felt piano: a rising arpeggio through every bar
    arp = [voic[0], voic[1], voic[2], voic[3], voic[2] + 12, voic[3], voic[2], voic[1]]
    for k, m in enumerate(arp):
        add(felt(m, BEAT * 0.9, 0.55 + 0.1 * (k == 0)), at(bar, k * 0.5), pan=-0.3 + 0.08 * k, gain=0.17)
    # electric piano pads the chord from bar 2
    if bar >= 2:
        for i, m in enumerate(voic):
            add(epiano(m, BAR * 0.9, 0.5), at(bar) + i * 0.01, pan=0.2 - 0.12 * i, gain=0.09)
    # bass and the heartbeat kick from bar 2
    if bar >= 2:
        add(bass(root - 12 if root > 44 else root, BEAT * 1.8, 0.9), at(bar), gain=0.3)
        add(bass((root + 7) - 12 if root + 7 > 44 else root + 7, BEAT * 1.6, 0.7), at(bar, 2), gain=0.22)
        for b in (0, 2):
            add(kick(0.9), at(bar, b), gain=0.32)
    # the cradle: a click on every beat, bars 2-9
    if 2 <= bar <= 9:
        for b in range(4):
            add(click(0.8 if b % 2 else 1.0), at(bar, b), pan=(-0.35 if b % 2 else 0.35), gain=0.075)
    # brushes from bar 4
    if bar >= 4:
        for b in range(4):
            add(brush(BEAT * 0.9, 0.9 if b % 2 else 0.6), at(bar, b + 0.5), pan=0.3, gain=0.045)
    # strings from bar 4, lifting at bar 7 (the fourth law)
    if bar >= 4:
        lift = 1.5 if bar >= 7 else 1.0
        add(strings([m + 12 for m in voic[1:]], BAR * 0.98, 0.9, attack=1.4 if bar == 7 else 0.9), at(bar), gain=0.4 * lift)

# ---------- the motif (original): a rising "it's possible again", on bell then strings ----------
MOTIF = [(0, 76, 1), (1, 78, 1), (2, 80, 1), (3, 83, 2.5)]           # E F# G# B
ANSWER = [(0, 85, 1), (1, 83, 1), (2, 80, 1.5), (3.5, 78, .5)]       # C# B G# F#
for bar, notes in [(7, MOTIF), (8, ANSWER), (9, MOTIF), (10, [(0, 88, 3)])]:
    for b, m, d in notes:
        add(bell(m, d * BEAT, 0.8), at(bar, b), pan=0.15, gain=0.11)
        add(felt(m, d * BEAT, 0.5), at(bar, b), pan=-0.1, gain=0.08)

# ---------- ending: Emaj9 rings out ----------
T_END = at(12)
for i, m in enumerate([40, 52, 56, 59, 63, 66, 71]):
    add(felt(m, 4.0, 0.6), T_END + i * 0.05, pan=-0.25 + 0.08 * i, gain=0.17)
for i, m in enumerate([76, 80, 83, 88]):
    add(bell(m, 2.6, 0.5), T_END + 0.5 + i * BEAT * 0.5, pan=0.3, gain=0.07)
add(strings([68, 71, 75, 78], 4.0, 0.8, attack=0.8), T_END, gain=0.42)
add(bass(28, 3.8, 0.8), T_END, gain=0.3)

# ---------- room, master ----------
n_ir = int(SR * 2.8)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 1.0), 0.55) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.5 * wet
out = np.stack([onepole(out[:, c], 0.32) for c in range(2)], axis=1)
out = out[: int(SR * DUR)]
fi = int(SR * 0.3); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.4); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.4
out = np.tanh(out / np.max(np.abs(out)) * 0.85) / np.tanh(0.85) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-possible.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
