# "The Fund Is Open": one score from the two the films were built on, for opening.html.
#   Part one, "Fiat Lux" (from Let There Be Liquidity), a semitone up in D major and at its own 70 BPM: a
#   wordless choir, felt piano, celesta, warm strings, a soft harp sweep into every morph.
#   At TB the coin has flipped into the watch and time settles to 60 BPM: "The Movement" in D minor, one soft
#   tick a second, harp arpeggios, pizzicato, the solo cello melody, a celesta chime on each step. The choir
#   comes back quietly under the last steps.
#   For the mark, a bar on the dominant with a 13-note harp run (the fan's 13 blades), then both ensembles
#   together on D major as the wordmark completes.
#   python3 score-opening.py out.wav
import sys
import wave
import numpy as np
from scipy.signal import fftconvolve, lfilter

SR = 44100
BEAT_A = 60 / 70
BAR_A = 4 * BEAT_A
TB = 8 * BAR_A          # 27.43 s
TC = TB + 40.0          # 67.43 s
T1 = TC + 4.0           # the wordmark completes
DUR = 80.0
rng = np.random.default_rng(1508)
N = int(SR * (DUR + 6))
wet = np.zeros((N, 2))  # goes through the hall
dry = np.zeros((N, 2))  # ticks and pizzicato: only a little hall
hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
onepole = lambda x, a: lfilter([1 - a], [1, -a], x)
atA = lambda bar, beat=0.0: bar * BAR_A + beat * BEAT_A
atB = lambda bar, beat=0.0: TB + bar * 4.0 + beat


def add(sig, t, pan=0.0, gain=1.0, bus=None):
    bus = wet if bus is None else bus
    i = max(0, int(t * SR))
    if i >= N: return
    sig = sig[: N - i] * gain
    bus[i:i + len(sig), 0] += sig * np.sqrt(0.5 * (1 - pan))
    bus[i:i + len(sig), 1] += sig * np.sqrt(0.5 * (1 + pan))


# ---------- instruments of "Fiat Lux" ----------
def reson(x, f, bw):
    r = np.exp(-np.pi * bw / SR)
    return lfilter([1 - r], [1, -2 * r * np.cos(2 * np.pi * f / SR), r * r], x)


def choir(notes, dur, vel=1.0):  # several detuned voices per note, through "oo" formants (~300, ~870 Hz)
    t = np.arange(int(SR * (dur + 2.0))) / SR
    src = np.zeros_like(t)
    for m in notes:
        for v in range(4):
            vib = 1 + 0.004 * np.sin(2 * np.pi * (4.6 + rng.random()) * t + rng.random() * 6)
            ph = np.cumsum(hz(m) * 2 ** ((rng.random() - .5) * 0.12 / 12) * vib) / SR
            src += 2 * (ph % 1.0) - 1
    voice = reson(src, 310, 90) * 1.0 + reson(src, 870, 140) * 0.45 + reson(src, 2300, 220) * 0.08
    breath = onepole(rng.normal(0, 1, len(t)), 0.7) * 0.02
    env = np.clip(t / 1.4, 0, 1) ** 1.5 * np.clip((dur + 1.8 - t) / 1.8, 0, 1)
    return (voice / (len(notes) * 4) * 6 + breath) * env * vel


def felt(m, dur, vel=1.0):
    t = np.arange(int(SR * (dur + 2.2))) / SR
    f = hz(m); tone = np.zeros_like(t)
    for k in range(1, 7):
        fk = f * k * np.sqrt(1 + 0.0003 * k * k)
        if fk > 9000: break
        tone += np.sin(2 * np.pi * fk * t) / k ** 1.7 * np.exp(-t * (0.45 + 0.85 * k) * (1 + m / 100))
    damp = np.clip((dur + 0.5 - t) / 0.5, 0, 1) * 0.85 + 0.15 * np.exp(-np.clip(t - dur, 0, None) * 2)
    return onepole(tone * (1 - np.exp(-t / 0.005)) * damp, 0.5) * vel


def celesta(m, vel=1.0, ring=1.1):
    t = np.arange(int(SR * 2.6)) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) * np.exp(-t / ring) + 0.3 * np.sin(2 * np.pi * f * 4 * t) * np.exp(-t / 0.18) + 0.1 * np.sin(2 * np.pi * f * 7.1 * t) * np.exp(-t / 0.05)
    return tone * (1 - np.exp(-t / 0.001)) * vel


def strings(notes, dur, vel=1.0, swell=1.6):
    t = np.arange(int(SR * (dur + 1.8))) / SR
    out = sum(2 * ((hz(m) * 2 ** (d / 12) * (t + 0.0015 * np.sin(2 * np.pi * 5.2 * t)) + rng.random()) % 1.0) - 1 for m in notes for d in (-0.08, 0.0, 0.08))
    out = onepole(onepole(onepole(out, 0.94), 0.92), 0.65)
    return out * np.clip(t / swell, 0, 1) * np.clip((dur + 1.5 - t) / 1.5, 0, 1) * vel / (len(notes) * 3)


def harp(m, vel=1.0):  # Karplus-Strong pluck, soft
    f = hz(m); n = int(SR * 2.2); p = max(2, int(SR / f))
    buf = onepole(rng.uniform(-1, 1, p), 0.45); out = np.zeros(n); k = 0
    for i in range(n):
        out[i] = buf[k]; nk = (k + 1) % p; buf[k] = 0.499 * (buf[k] + buf[nk]); k = nk
    return out * vel


# ---------- instruments of "The Movement" ----------
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
    env = np.clip(t / 0.18, 0, 1) * np.clip((dur + 0.5 - t) / 0.5, 0, 1) * (0.85 + 0.15 * np.sin(np.pi * np.clip(t / max(dur, .1), 0, 1)))
    return tone * env * vel


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


# Levels. Measured note by note, the choir of "Fiat Lux" is about 26 dB above everything else in that score (it IS
# that score's sound), and "The Movement" is an even chamber mix about 21 dB quieter overall. So:
CH = 0.0813  # the choir, brought down to sit level with the chamber ensemble (part one lands 1 dB under the steps)
FI = 0.33    # the felt piano, strings and celesta of part one: still under the choir, a little more present than before
GB, GC = 1.0, 0.88  # the mark a touch softer: a bloom, not a blast

# ---------- part one: "Fiat Lux" in D (70 BPM, 8 bars) ----------
# Dmaj9 | Bm9 | Gmaj7#11 | A6sus | Dmaj9 | Bm9 | Em9 | A13
PA = [(38, [54, 57, 61, 64]), (35, [54, 57, 61, 62]), (31, [54, 59, 62, 66]), (33, [54, 57, 61, 64]),
      (38, [54, 57, 61, 64]), (35, [54, 57, 61, 62]), (40, [55, 59, 62, 66]), (33, [55, 59, 61, 66])]
for bar, (root, v) in enumerate(PA):
    add(choir(v, BAR_A * .98, 0.9), atA(bar), gain=(0.12 if bar < 2 else 0.16) * CH)
    add(felt(root + 12, BAR_A * .95, 0.7), atA(bar), pan=-0.15, gain=0.15 * FI)
    if bar >= 2:
        for k, b in enumerate((0, 1.5, 2, 3)):  # the slow piano figure
            add(felt(v[[1, 2, 3, 2][k]] + 12, BEAT_A * 1.3, 0.55), atA(bar, b), pan=0.12, gain=0.11 * FI)
    if bar >= 4: add(strings(v, BAR_A * .99, 0.8), atA(bar), gain=(0.12 if bar < 6 else 0.16) * FI)
    for k in range(4):  # celesta sparkles, sparse
        if rng.random() < (0.35 if bar < 4 else 0.6):
            add(celesta(v[rng.integers(0, 4)] + 24, 0.6), atA(bar, k + rng.random() * .5), pan=0.35, gain=0.05 * FI)
for bar in (2, 4, 6):  # a soft harp sweep into each morph, ending on the downbeat
    notes = sorted({n + 12 * o for n in PA[bar][1] for o in (0, 1)})
    for i, m in enumerate(notes): add(harp(m, 0.6), atA(bar) - 1.1 + i * 0.13, pan=-0.4 + 0.1 * i, gain=0.2)
# the melody of "Fiat Lux", felt piano high and soft, from the hands to the coin
MEL_A = {4: [(0, 73, 2), (2, 76, 2)], 5: [(0, 78, 3), (3, 76, 1)], 6: [(0, 74, 2), (2, 73, 1), (3, 71, 1)], 7: [(0, 73, 4)]}
for bar, notes in MEL_A.items():
    for b, m, d in notes: add(felt(m, d * BEAT_A * .95, 0.7), atA(bar, b), pan=0.2, gain=0.08)

# ---------- part two: "The Movement" in D minor (60 BPM, 10 bars from TB) ----------
# Dm9 | Bbmaj7 | Gm9 | A7sus4, ... and Fmaj9 for the wide shot
PB = [(38, [53, 57, 60, 64]), (34, [53, 57, 62, 65]), (31, [53, 57, 58, 62]), (33, [55, 57, 62, 64])]
for k in range(44):  # the tick, every second from the moment the watch faces us; softer under the mark
    add(tick(k % 4 == 0), TB + k, pan=0.15, gain=(0.09 if k < 40 else 0.055) * GB, bus=dry)
notes = sorted({n + 12 * o for n in PB[0][1] for o in (0, 1)})  # the last harp sweep of part one lands on the first tick
for i, m in enumerate(notes): add(harp(m, 0.6), TB - 1.1 + i * 0.13, pan=-0.4 + 0.1 * i, gain=0.2)
for bar in range(10):
    root, v = PB[bar % 4]
    if bar == 9: root, v = 41, [57, 60, 64, 67]  # Fmaj9
    arp = [root + 12, v[0], v[1], v[2], v[3], v[2] + 12, v[3], v[1]]
    for k, m in enumerate(arp):  # harp, eighth notes
        add(pluck(m, 2.2, 0.55), atB(bar, k * .5), pan=-0.35 + 0.08 * k, gain=(0.14 if bar >= 2 else 0.22) * GB)
    if bar < 2:  # so the watch face is not left bare after the choir
        add(strings(v, 3.9, 0.7, swell=2.2), atB(bar), gain=0.2 * GB); add(pad(v, 3.92, 0.8), atB(bar), gain=0.1 * GB)
    if bar >= 2:
        for b in (0, 2): add(pluck(root, 1.4, 0.3), atB(bar, b), gain=0.3 * GB, bus=dry)    # pizzicato bass
        for b in (1, 3): add(pluck(v[0], 1.0, 0.35), atB(bar, b) + 0.01, pan=0.25, gain=0.14 * GB, bus=dry)
        add(pad(v, 3.92, 0.8), atB(bar), gain=0.16 * GB)
        add(celesta(v[3] + 24, 0.8, ring=0.9), atB(bar) + 0.02, pan=0.3, gain=0.07 * GB)  # a chime as each step arrives
    if bar >= 6: add(choir(v, 3.9, 0.8), atB(bar), gain=(0.06 + 0.015 * (bar - 6)) * CH)  # the choir returns, far off
# the cello melody of "The Movement", over the seven steps and the wide shot
MEL_B = {2: [(0, 62, 2), (2, 64, 1), (3, 65, 1)], 3: [(0, 67, 3), (3, 65, 1)],
         4: [(0, 62, 1.5), (1.5, 60, .5), (2, 58, 2)], 5: [(0, 57, 3), (3, 60, 1)],
         6: [(0, 62, 2), (2, 65, 1), (3, 69, 1)], 7: [(0, 67, 2), (2, 65, 1), (3, 64, 1)],
         8: [(0, 62, 1.5), (1.5, 64, .5), (2, 65, 2)], 9: [(0, 64, 2), (2, 67, 2)]}
for bar, notes in MEL_B.items():
    for b, m, d in notes: add(cello(m - 12, d * .97, 0.9), atB(bar, b), pan=-0.05, gain=0.13 * GB)

# ---------- the mark ----------
# one bar on the dominant (A7sus4 -> A7) while the camera goes to the fan and it closes midway ...
for k, m in enumerate([45, 55, 57, 62, 64, 69, 64, 61]):
    add(pluck(m, 2.2, 0.55), TC + k * .5, pan=-0.35 + 0.08 * k, gain=0.14 * GC)
for b in (0, 2): add(pluck(33, 1.4, 0.3), TC + b, gain=0.3 * GC, bus=dry)
add(pad([55, 57, 62, 64], 1.9, 0.8), TC, gain=0.16 * GC); add(pad([55, 57, 61, 64], 1.9, 0.8), TC + 2, gain=0.17 * GC)
add(strings([45, 52, 55, 61, 64], 3.6, 0.9, swell=3.4), TC + .2, gain=0.3 * GC)
add(choir([57, 61, 64, 67], 3.6, 0.8), TC + .2, gain=0.09 * CH * GC)
add(cello(52, 1.95, 0.9), TC, pan=-0.05, gain=0.13 * GC); add(cello(49, 1.95, 0.9), TC + 2, pan=-0.05, gain=0.13 * GC)
# ... thirteen harp notes rising, one for each blade as it flies off to the M
RUN = [50, 54, 57, 59, 62, 64, 66, 69, 71, 74, 76, 78, 81]
for i, m in enumerate(RUN): add(harp(m, 0.6), TC + 2.42 + i * 0.118, pan=-0.45 + 0.075 * i, gain=(0.17 + 0.005 * i) * GC)
# ... and D major, both ensembles together, as the wordmark completes
for i, m in enumerate([38, 50, 57, 62, 66, 69, 73, 76]): add(pluck(m, 3.5, 0.5), T1 + i * 0.07, pan=-0.35 + 0.1 * i, gain=0.16 * GC)
add(pad([57, 62, 66, 69], 5.6, 0.9), T1, gain=0.3 * GC)
add(cello(50, 5.2, 0.8), T1, gain=0.12 * GC); add(cello(38, 5.2, 0.7), T1, gain=0.07 * GC)
add(choir([54, 57, 61, 64, 66], 6.6, 0.9), T1, gain=0.30 * CH * GC)
add(strings([50, 57, 61, 64], 6.6, 0.85), T1, gain=0.3 * GC)
for i, m in enumerate([26, 38, 50, 57, 61, 64, 66, 69]): add(felt(m, 6.0, 0.6), T1 + 0.15 + i * 0.07, pan=-0.3 + 0.08 * i, gain=0.10 * GC)
for i, m in enumerate([86, 90, 93]): add(celesta(m, 0.6), T1 + 1.6 + i * 0.4, pan=0.35, gain=0.05 * GC)

# ---------- the hall, levels, file ----------
n_ir = int(SR * 3.6)
tt = np.arange(n_ir) / SR
ir = np.stack([onepole(rng.normal(0, 1, n_ir) * np.exp(-tt / 1.25), 0.47) for _ in range(2)], axis=1)
ir /= np.sqrt((ir ** 2).sum(axis=0))
conv = lambda x: np.stack([fftconvolve(x[:, c], ir[:, c])[:N] for c in range(2)], axis=1)
out = wet + 0.46 * conv(wet) + dry + 0.16 * conv(dry)
out = out[: int(SR * DUR)]
fi = int(SR * 0.6); out[:fi] *= np.linspace(0, 1, fi)[:, None]
fo = int(SR * 3.0); out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
db = lambda a, b: 20 * np.log10(np.sqrt(np.mean(out[int(a * SR):int(b * SR)] ** 2)) + 1e-12)
print('levels (dB RMS, before normalising): liquidity %.1f | watch face %.1f | steps %.1f | dominant %.1f | bloom %.1f' % (db(8, 27), db(TB + .5, TB + 8), db(TB + 8, TB + 36), db(TC, T1), db(T1 + .3, T1 + 4.5)))
out = np.tanh(out / np.max(np.abs(out)) * 0.9) / np.tanh(0.9) * 0.88
with wave.open(sys.argv[1] if len(sys.argv) > 1 else 'score-opening.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((out * 32767).astype('<i2').tobytes())
print('wrote', DUR, 's')
