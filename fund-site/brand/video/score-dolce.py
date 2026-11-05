# "Dolce Arbitraggio": an original swinging 1960s Italian film-title score (the lounge-jazz style of that era's
# Roman film composers; no existing piece is quoted). 120 BPM swing in B-flat: Hammond organ, harpsichord, a
# wordless "ba-da" vocal choir on the tune, muted trumpets, walking bass, swing ride and brushes; a flute and
# vibraphone bridge. Fully synthesized.
#   python3 score-dolce.py out.wav
# Timing follows dolce.html (a bar is 2 s): bars 0-1 organ and harpsichord, the band from bar 2, the vocal theme
# bars 4-11, the bridge bars 12-15, the reprise bars 16-19, the last chord from bar 20.
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter, butter

SR = 44100
BPM = 120
BEAT = 60 / BPM
BAR = 4 * BEAT
DUR = 44.0
rng = np.random.default_rng(60)
mix = np.zeros((int(SR * (DUR + 3)), 2))
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)


def at(bar, beat=0.0):  # swung eighths: the off-beat eighth lands two thirds of the way through the beat
    b = int(beat)
    frac = beat - b
    if abs(frac - 0.5) < 1e-6:
        frac = 2 / 3
    return bar * BAR + (b + frac) * BEAT


def add(sig, t, pan=0.0, gain=1.0):
    i = max(0, int(t * SR))
    if i >= len(mix):
        return
    sig = sig[: len(mix) - i] * gain
    mix[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    mix[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


def bandpass(x, lo, hi, order=2):
    b, a = butter(order, [lo / (SR / 2), hi / (SR / 2)], btype='band')
    return lfilter(b, a, x)


def organ(notes, dur, vel=1.0):
    """Hammond-style: drawbar partials with a rotating-speaker shimmer."""
    t = np.arange(int(SR * (dur + 0.3))) / SR
    out = np.zeros(len(t))
    for m in notes:
        f = hz(m)
        for k, a in [(.5, .5), (1, 1), (2, .7), (3, .35), (4, .3), (6, .12), (8, .1)]:
            out += a * np.sin(2 * np.pi * f * k * t + rng.random() * 6)
    leslie = 1 + 0.18 * np.sin(2 * np.pi * 6.1 * t)
    env = np.clip(t / 0.02, 0, 1) * np.clip((dur + 0.25 - t) / 0.25, 0, 1)
    return onepole(out * leslie * env, 0.4) * vel / (len(notes) * 3)


def harpsichord(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.8))) / SR
    f = hz(m)
    tone = sum(np.sin(2 * np.pi * f * k * t) * np.exp(-t * (2.5 + 1.6 * k)) / k ** .7 for k in range(1, 9))
    env = (1 - np.exp(-t / 0.002)) * np.clip((dur + 0.6 - t) / 0.3, 0, 1)
    return onepole(tone * env, 0.5) * vel  # filtered, so the pluck is bright but not sharp


def voice(m, dur, vel=1.0, syl='ba'):
    """A wordless jazz-choir syllable: a consonant onset, then the vowel 'ah' through three formants."""
    t = np.arange(int(SR * (dur + 0.25))) / SR
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.4 * t) * np.clip((t - 0.15) / 0.2, 0, 1)
    src = 2 * ((np.cumsum(hz(m) * vib) / SR) % 1.0) - 1
    out = np.zeros(len(t))
    for (lo, hi, a) in [(650, 950, 1.0), (1000, 1350, .55), (2600, 3100, .12)]:
        out += bandpass(src, lo, hi) * a
    onset = 0.018 if syl == 'ba' else 0.012
    env = np.clip((t - onset) / 0.03, 0, 1) * np.clip((dur + 0.2 - t) / 0.2, 0, 1)
    burst = bandpass(rng.normal(0, 1, len(t)), 300, 1500) * np.exp(-t / 0.008) * 0.15
    return (out * env + burst) * vel


def muted_trumpet(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.2))) / SR
    src = 2 * ((hz(m) * t + rng.random()) % 1.0) - 1
    out = bandpass(src, 900, 2200) + 0.4 * bandpass(src, 400, 700)
    env = np.clip(t / 0.03, 0, 1) * np.exp(-t / 0.6) * np.clip((dur + 0.15 - t) / 0.15, 0, 1)
    return out * env * vel


def upright(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.2))) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t) + 0.1 * np.sin(6 * np.pi * f * t)
    env = (1 - np.exp(-t / 0.01)) * np.exp(-t / 0.45) * np.clip((dur + 0.15 - t) / 0.15, 0, 1)
    return onepole(np.tanh(1.2 * tone * env), 0.35) * vel


def ride(vel=1.0):
    t = np.arange(int(SR * 0.9)) / SR
    metal = sum(np.sin(2 * np.pi * f * t + rng.random() * 6) for f in (3120, 4370, 5210, 6830)) / 4
    noise = rng.normal(0, 1, len(t))
    hp = noise - onepole(noise, 0.9)
    return onepole((metal * .6 + hp * .4) * np.exp(-t / 0.35) * (1 - np.exp(-t / 0.002)), 0.3) * vel


def brush(dur, vel=1.0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    noise = rng.normal(0, 1, n)
    return onepole(noise - onepole(noise, 0.7), 0.5) * np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2 * vel * 0.3


def flute(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 0.3))) / SR
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.0 * t) * np.clip((t - 0.2) / 0.3, 0, 1)
    ph = 2 * np.pi * np.cumsum(hz(m) * vib) / SR
    tone = np.sin(ph) + 0.15 * np.sin(2 * ph) + 0.04 * np.sin(3 * ph)
    breath = onepole(rng.normal(0, 1, len(t)), 0.9) * 0.06
    env = np.clip(t / 0.06, 0, 1) * np.clip((dur + 0.25 - t) / 0.25, 0, 1)
    return (tone + breath) * env * vel


def vibes(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 1.4))) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) * np.exp(-t / 1.8) + 0.2 * np.sin(2 * np.pi * f * 3.98 * t) * np.exp(-t / 0.3)
    trem = 1 - 0.25 * (0.5 + 0.5 * np.sin(2 * np.pi * 5.5 * t))
    return tone * trem * (1 - np.exp(-t / 0.006)) * vel


# ---------- harmony: eight bars in B-flat ----------
PROG = [  # (bass walk for the bar, voicing)
    ([46, 50, 53, 55], [53, 57, 58, 62]),   # Bbmaj7
    ([43, 47, 50, 49], [53, 59, 62, 65]),   # G7
    ([48, 51, 55, 52], [55, 58, 60, 63]),   # Cm7
    ([41, 45, 48, 45], [53, 57, 60, 63]),   # F7
    ([50, 53, 57, 55], [53, 57, 60, 62]),   # Dm7
    ([43, 47, 50, 47], [53, 59, 62, 65]),   # G7
    ([48, 51, 41, 45], [55, 58, 60, 63]),   # Cm7 | F7
    ([46, 50, 53, 47], [53, 57, 58, 62]),   # Bbmaj7 (walking up to the turn)
]
BARS = 20
for bar in range(BARS):
    walk, voic = PROG[bar % 8]
    # organ: a sustained chord; stabs on the swung "and" of 2 once the band is in
    add(organ(voic, BAR * (1.0 if bar < 2 else .45), 0.9), at(bar), pan=-0.2, gain=0.5 if bar < 2 else 0.35)
    if bar >= 2:
        add(organ(voic, BEAT * .8, 0.8), at(bar, 2.5), pan=-0.2, gain=0.3)
    # harpsichord: a bright little figure on beats 1 and the "and" of 3
    for b, v in [(0, 3), (0.5, 2), (2.5, 1), (3, 3)]:
        add(harpsichord(voic[v] + 12, BEAT * .4, 0.7), at(bar, b), pan=0.35, gain=0.085)
    if bar >= 2:
        for b, m in enumerate(walk):
            add(upright(m - 12 if m > 47 else m, BEAT * .9, 0.85 + 0.1 * (b == 0)), at(bar, b), gain=0.3)
        # swing ride: ding, ding-da, ding, ding-da
        for b in (0, 1, 1.5, 2, 3, 3.5):
            add(ride(0.8 if b % 1 else 1.0), at(bar, b), pan=0.4, gain=0.05)
        for b in (1, 3):
            add(brush(BEAT * .8, 0.9), at(bar, b), pan=-0.3, gain=0.06)
    if bar >= 4 and bar % 2 == 1:  # muted trumpet stabs answering the voices
        for m in voic[1:]:
            add(muted_trumpet(m + 12, BEAT * .6, 0.7), at(bar, 3.5), pan=0.25, gain=0.045)

# ---------- the tune (original): wordless voices, two phrases, sung bars 4-11 and again 16-19 ----------
THEME = [
    [(0, 77, 1.5, 'ba'), (1.5, 74, .5, 'da'), (2, 72, .5, 'da'), (2.5, 70, 1, 'ba')],
    [(0, 71, 1, 'ba'), (1, 74, 1, 'da'), (2, 77, 2, 'ba')],
    [(0.5, 75, .5, 'da'), (1, 74, .5, 'da'), (1.5, 72, 1, 'ba'), (3, 70, 1, 'da')],
    [(0, 69, 1.5, 'ba'), (1.5, 72, .5, 'da'), (2, 75, 2, 'ba')],
    [(0, 74, 1, 'ba'), (1, 77, 1, 'da'), (2, 81, 1.5, 'ba'), (3.5, 79, .5, 'da')],
    [(0, 77, 1, 'ba'), (1, 74, .5, 'da'), (1.5, 71, 1.5, 'ba')],
    [(0, 72, 1, 'da'), (1, 75, 1, 'da'), (2, 77, .5, 'ba'), (2.5, 75, .5, 'da'), (3, 72, 1, 'ba')],
    [(0, 70, 3, 'ba')],
]
for start in (4, 16):
    for k, notes in enumerate(THEME[:8 if start == 4 else 4] if start == 4 else THEME[:4]):
        bar = start + k
        for b, m, d, syl in notes:
            for dv, pan in [(0, -0.15), (-3, 0.15), (-8, 0)]:  # a small choir in thirds and a lower line
                add(voice(m + dv, d * BEAT * .95, 0.9 if dv == 0 else 0.6, syl), at(bar, b), pan=pan, gain=0.11 if dv == 0 else 0.06)
# bar 12-15 bridge: flute and vibes
BRIDGE = [[(0, 79, 2), (2, 77, 1), (3, 74, 1)], [(0, 75, 3), (3, 72, 1)], [(0, 77, 2), (2, 74, 2)], [(0, 72, 1), (1, 74, 1), (2, 77, 2)]]
for k, notes in enumerate(BRIDGE):
    for b, m, d in notes:
        add(flute(m, d * BEAT * .95, 0.85), at(12 + k, b), pan=0.1, gain=0.13)
        add(vibes(m - 12, d * BEAT, 0.6), at(12 + k, b), pan=-0.3, gain=0.07)

# ---------- ending: Bbmaj9, the choir on "ah", a harpsichord run up ----------
T_END = at(20)
add(organ([46, 53, 57, 60, 62], 3.6, 0.9), T_END, gain=0.5)
for m, pan in [(74, -0.15), (77, 0.15), (81, 0)]:
    add(voice(m, 3.0, 0.8, 'ba'), T_END, pan=pan, gain=0.09)
add(upright(34, 2.5, 0.9), T_END, gain=0.32)
for i, m in enumerate([58, 62, 65, 69, 70, 74, 77, 81]):
    add(harpsichord(m + 12, 0.6, 0.6), T_END + 0.1 + i * 0.07, pan=0.35, gain=0.07)
add(ride(1.0), T_END, pan=0.4, gain=0.07)

# ---------- room, master ----------
n_ir = int(SR * 1.8)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 0.55), 0.5) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
wet = np.stack([fftconvolve(mix[:, c], ir[:, c])[:len(mix)] for c in range(2)], axis=1)
out = mix + 0.38 * wet
out = np.stack([onepole(out[:, c], 0.22) for c in range(2)], axis=1)  # a warm, slightly vintage top end
out = out[: int(SR * DUR)]
fi = int(SR * 0.15); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 2.0); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.4
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-dolce.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
