# "Riviera": an original, mellow score for the Genesis pass video. 60s Italian lounge and old-money mood: slow
# (72 BPM), lush major-ninth harmony in D, soft electric piano, strings, a breathy flute melody, upright bass and
# light brushes. No plucked guitar and no sharp attacks. Fully synthesized, no samples.
#   python3 score-riviera.py out.wav
# Timing follows nft-reel.html: bars 0-2 the token builds (piano, bass), the melody enters at bar 3 as the reel
# starts shuffling, strings swell at bar 10 for the 1/1, the final chord rings from bar 12.
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BPM = 72
BEAT = 60 / BPM
BAR = 4 * BEAT
DUR = 44.0
rng = np.random.default_rng(11)
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


def epiano(m, dur, vel=1.0):
    """Soft electric piano: two-operator FM, a gentle tine, slow decay; 12 ms attack so nothing clicks."""
    t = np.arange(int(SR * (dur + 1.8))) / SR
    f = hz(m)
    idx = (1.1 * np.exp(-t / 0.35) + 0.25) * vel
    tone = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
    tine = 0.06 * np.sin(2 * np.pi * f * 7 * t) * np.exp(-t / 0.05)
    env = (1 - np.exp(-t / 0.012)) * np.exp(-t / 2.4) * np.clip((dur + 1.5 - t) / 0.6, 0, 1)
    return onepole((tone + tine) * env, 0.35) * vel


def upright(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.3))) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) + 0.28 * np.sin(4 * np.pi * f * t) + 0.08 * np.sin(6 * np.pi * f * t)
    env = (1 - np.exp(-t / 0.018)) * np.exp(-t / 0.9) * np.clip((dur + 0.25 - t) / 0.25, 0, 1)
    return onepole(np.tanh(1.1 * tone * env), 0.4) * vel


def flute(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.4))) / SR
    f = hz(m)
    vib = 1 + 0.0045 * np.sin(2 * np.pi * 4.8 * t) * np.clip((t - 0.35) / 0.4, 0, 1)
    ph = 2 * np.pi * np.cumsum(f * vib) / SR
    tone = np.sin(ph) + 0.14 * np.sin(2 * ph) + 0.04 * np.sin(3 * ph)
    breath = onepole(rng.normal(0, 1, len(t)), 0.92) * 0.07
    env = np.clip(t / 0.12, 0, 1) ** 1.5 * np.clip((dur + 0.35 - t) / 0.35, 0, 1)
    return (tone + breath) * env * vel


def strings(notes, dur, vel=1.0, attack=1.2):
    t = np.arange(int(SR * (dur + 1.5))) / SR
    out = np.zeros(len(t))
    for m in notes:
        for d in (-0.08, 0.0, 0.07):
            f = hz(m) * 2 ** (d / 12)
            out += 2 * ((f * t + rng.random()) % 1.0) - 1
    out = onepole(onepole(onepole(out, 0.94), 0.92), 0.6)
    env = np.clip(t / attack, 0, 1) ** 1.6 * np.clip((dur + 1.4 - t) / 1.4, 0, 1)
    return out * env * vel / (len(notes) * 3)


def brush(dur, vel=1.0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    noise = rng.normal(0, 1, n)
    hp = onepole(noise - onepole(noise, 0.7), 0.5)
    env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2
    return hp * env * vel * 0.3


# ---------- harmony: eight bars, bass root and voicing ----------
PROG = [
    (50, [54, 57, 61, 64]),  # Dmaj9
    (47, [57, 61, 62, 66]),  # Bm9
    (40, [55, 59, 62, 66]),  # Em9
    (45, [55, 61, 66, 71]),  # A13
    (43, [54, 57, 59, 62]),  # Gmaj9
    (42, [52, 57, 61, 64]),  # F#m7(11)
    (40, [55, 59, 62, 66]),  # Em9
    (45, [55, 61, 64, 67]),  # A7
]
BARS = 12  # the last chord rings from bar 12

for bar in range(BARS):
    root, voic = PROG[bar % 8]
    # electric piano: a soft chord on 1, a lighter one on the "and" of 2
    for i, m in enumerate(voic):
        add(epiano(m, BEAT * 1.4, 0.7), at(bar) + i * 0.012, pan=-0.25 + 0.15 * i, gain=0.22)
        add(epiano(m + 12 if i == 3 else m, BEAT * 0.8, 0.45), at(bar, 2.5) + i * 0.01, pan=0.2 - 0.12 * i, gain=0.12)
    # upright bass from bar 1: root on 1, fifth on 3, a walk-up on 4 every other bar
    if bar >= 1:
        add(upright(root - 12 if root > 47 else root, BEAT * 1.8, 0.9), at(bar), gain=0.34)
        add(upright((root + 7) - 12 if root + 7 > 47 else root + 7, BEAT * 1.6, 0.75), at(bar, 2), gain=0.28)
        if bar % 2:
            add(upright((root + 11) - 12 if root + 11 > 47 else root + 11, BEAT * 0.8, 0.55), at(bar, 3.5), gain=0.18)
    # brushes from bar 3, very soft swishes on every beat
    if bar >= 3:
        for b in range(4):
            add(brush(BEAT * 0.9, 0.9 if b % 2 else 0.6), at(bar, b), pan=0.3, gain=0.05)
    # strings from bar 3, swelling for the 1/1 at bar 10
    if bar >= 3:
        lift = 1.6 if bar == 10 else 1.0
        add(strings([m + 12 for m in voic[1:]], BAR * 0.98, 0.9, attack=1.6 if bar == 10 else 1.0), at(bar), gain=0.5 * lift)

# ---------- melody (original): breathy flute from bar 3 ----------
MEL = {
    3: [(0, 76, 1.5), (1.5, 78, .5), (2, 81, 2)],
    4: [(0, 78, 3), (3, 76, 1)],
    5: [(0, 73, 2), (2, 76, 1), (3, 73, 1)],
    6: [(0, 74, 3.5)],
    7: [(0, 73, 1), (1, 76, 1), (2, 79, 2)],
    8: [(0, 78, 2), (2, 81, 1), (3, 83, 1)],
    9: [(0, 81, 3), (3, 78, 1)],
    10: [(0, 79, 2), (2, 78, 1), (3, 76, 1)],
    11: [(0, 74, 1.5), (1.5, 73, .5), (2, 76, 2)],
}
for bar, notes in MEL.items():
    for b, m, d in notes:
        add(flute(m, d * BEAT * 0.95, 0.9), at(bar, b), pan=0.12, gain=0.16)

# ---------- ending: Dmaj9 rings, an electric-piano arpeggio climbs ----------
T_END = at(12)
for i, m in enumerate([50, 57, 61, 64, 66, 69]):
    add(epiano(m, 3.6, 0.55), T_END + i * 0.03, pan=-0.2 + 0.08 * i, gain=0.2)
add(upright(38, 3.5, 0.8), T_END, gain=0.32)
for i, m in enumerate([69, 73, 76, 78, 81]):
    add(epiano(m, 2.6, 0.35), T_END + 0.6 + i * BEAT * 0.5, pan=0.3, gain=0.12)
add(strings([66, 69, 73, 76], 3.8, 0.8, attack=0.8), T_END, gain=0.55)

# ---------- room, master ----------
n_ir = int(SR * 2.6)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.9), 0.55) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.55 * wet
out = np.stack([onepole(out[:, c], 0.38) for c in range(2)], axis=1)  # warm, rounded top end
out = out[: int(SR * DUR)]
fade_in = int(SR * 0.4)
out[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
fade = int(SR * 2.2)
out[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 1.4
out = np.tanh(out / np.max(np.abs(out)) * 0.85) / np.tanh(0.85) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-riviera.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
