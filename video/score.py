#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HEVI - "She Waits" - original 60-second score, synthesized from scratch.

    python3 video/score.py            (run from the repo root)

Writes
    video/out/score.wav          48 kHz / 16-bit / stereo / exactly 2,880,000 frames (60.000 s)
    video/out/score_report.txt   peak, loudness per section, measured onsets of every sync point
    video/out/score_wave.png     waveform + spectrogram + loudness overview (only if Pillow is installed)

Only numpy and the Python standard library are needed for the audio (Pillow is optional, PNG only).
No samples, no internet: every sound is generated here. Fully deterministic (all generators are seeded).

Musical frame (see video/STORYBOARD.md): 120 BPM, 4/4, beat = 0.5 s, bar = 2.0 s, 30 bars.
Key A minor. Groove loop Fmaj7 - G6 - Am9 - Em7, two bars each, anchored so that THE DROP (14.0) lands on
Fmaj7. The big hit (44.0) lands on the tonic Am9 after the bVI - bVII build (F -> G -> Am), and the finale
resolves Am9 -> Fmaj7 -> A major (picardy, C -> C# in the top voice).

Section map (seconds)
    0.0  BOOT        low A drone fades in, tick on every beat, low-passed arpeggio from 2.0
    6.0  RAIN        pads swell (Am9), digital "rain" droplets; from 10.0 riser (noise sweep, pitch riser,
                     rising 16th arpeggio over Em7) and a reverse swell that sucks into the drop
   13.875            full stop (one 16th of silence)
   14.0  THE DROP    kick + impact + crash + sub + Fmaj7 stab; full groove (kick on beats, offbeat hats,
                     sub on chord roots, pads, glassy 16th arpeggio with ping-pong delay)
   22.0  READS       + counter-melody; filter-reject pings at 30.5 / 31.5 / 32.5 / 33.5
   34.0  TRADE       groove; 40.0 everything low-passed, half-time heartbeat; 42.0 snare roll + riser,
                     filter opening; 44.0 BIG HIT (impact, crash, Am9 stab, held shimmer); groove to 46.0
   46.0  DISCIPLINE  pads + soft arp; checklist chimes 46.5 .. 49.0, softer tone at 50.5
   52.0  FINALE      soft impact + chord strike, Am9 -> Fmaj7 -> A, long tail, digital silence from 59.8
"""
import math
import os
import time
import wave

import numpy as np

T0_WALL = time.time()

# ----------------------------------------------------------------------------------------- constants
SR = 48000
N = 2_880_000                      # 60.000 s exactly
BPM = 120
BEAT = 60.0 / BPM                  # 0.5 s
BAR = 4 * BEAT                     # 2.0 s
T_DROP = 14.0
T_CUT = 13.875                     # pre-drop stop: one 16th of silence before the drop
T_HIT = 44.0
T_FINALE = 52.0
FADE_START = 56.8
FADE_END = 59.8                    # digital silence from here to 60.0

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
WAV_PATH = os.path.join(OUT_DIR, "score.wav")
REPORT_PATH = os.path.join(OUT_DIR, "score_report.txt")
PNG_PATH = os.path.join(OUT_DIR, "score_wave.png")

TT = np.arange(N) / SR             # global time axis


def log(msg):
    print(f"[{time.time() - T0_WALL:6.1f}s] {msg}", flush=True)


def S(t):
    """Seconds -> sample index (every musical position used here is an exact integer)."""
    return int(round(t * SR))


def hz(m):
    return 440.0 * 2.0 ** ((np.asarray(m, dtype=float) - 69.0) / 12.0)


def db(x):
    return 20.0 * math.log10(max(float(x), 1e-12))


def undb(d):
    return 10.0 ** (d / 20.0)


def nextpow2(n):
    return 1 << int(math.ceil(math.log2(max(2, n))))


def smoothstep(u):
    u = np.clip(u, 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def curve(points, t, log_interp=False):
    """Piecewise automation curve; log_interp=True interpolates in the log domain (frequencies)."""
    ts = np.array([p[0] for p in points], float)
    vs = np.array([p[1] for p in points], float)
    if log_interp:
        return np.exp(np.interp(t, ts, np.log(vs)))
    return np.interp(t, ts, vs)


# ----------------------------------------------------------------------------------------- DSP core
def saw(freq, n, phase0=0.0):
    """Band-limited (polyBLEP) sawtooth. freq: scalar or per-sample array (Hz)."""
    if np.ndim(freq) == 0:
        inc = float(freq) / SR
        ph = (phase0 + inc * np.arange(n)) % 1.0
        y = 2.0 * ph - 1.0
        m = ph < inc
        x = ph[m] / inc
        y[m] -= x + x - x * x - 1.0
        m = ph > 1.0 - inc
        x = (ph[m] - 1.0) / inc
        y[m] -= x * x + x + x + 1.0
        return y
    inc = np.asarray(freq, float) / SR
    ph = (phase0 + np.concatenate(([0.0], np.cumsum(inc[:-1])))) % 1.0
    y = 2.0 * ph - 1.0
    m = ph < inc
    x = ph[m] / inc[m]
    y[m] -= x + x - x * x - 1.0
    m = ph > 1.0 - inc
    x = (ph[m] - 1.0) / inc[m]
    y[m] -= x * x + x + x + 1.0
    return y


def phase_of(freq):
    """Running phase (radians) of a per-sample frequency array, starting at exactly 0."""
    ph = np.cumsum(freq) * (2.0 * np.pi / SR)
    return ph - ph[0]


def lp_mag(f, fc, order=2):
    return 1.0 / np.sqrt(1.0 + (f / fc) ** (2 * order))


def hp_mag(f, fc, order=2):
    r = f / fc
    return r ** order / np.sqrt(1.0 + r ** (2 * order))


def fft_filter(x, gain_fn, pad=None):
    """Static zero-phase filter (for noise sources and short buffers, before any envelope)."""
    n = len(x)
    nf = nextpow2(n + (pad if pad is not None else min(n, 16384)))
    g = gain_fn(np.fft.rfftfreq(nf, 1.0 / SR))
    if x.ndim == 1:
        return np.fft.irfft(np.fft.rfft(x, nf) * g, nf)[:n]
    return np.fft.irfft(np.fft.rfft(x, nf, axis=0) * g[:, None], nf, axis=0)[:n]


def fftconv(x, h, n_out=None):
    n = len(x) + len(h) - 1
    nf = nextpow2(n)
    y = np.fft.irfft(np.fft.rfft(x, nf) * np.fft.rfft(h, nf), nf)
    return y[: (n_out if n_out is not None else len(x))]


STFT_N = 2048
STFT_H = 512
_SQRT_HANN = np.sqrt(0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(STFT_N) / STFT_N))
STFT_F = np.fft.rfftfreq(STFT_N, 1.0 / SR)


def stft_filter(x, gain_fn, t_offset=0.0):
    """Time-varying zero-phase filter: sqrt-Hann STFT, 75 % overlap, gain_fn(frame_times, freqs) -> gains."""
    mono = x.ndim == 1
    X = x[:, None] if mono else x
    n = len(X)
    pad = STFT_N
    nfr = int(math.ceil((n + pad) / STFT_H)) + 1
    total = (nfr - 1) * STFT_H + STFT_N
    times = (np.arange(nfr) * STFT_H + STFT_N / 2 - pad) / SR + t_offset
    G = gain_fn(times, STFT_F)
    out = np.zeros((n, X.shape[1]))
    step = STFT_N // STFT_H
    for c in range(X.shape[1]):
        xp = np.zeros(total)
        xp[pad:pad + n] = X[:, c]
        fr = np.lib.stride_tricks.sliding_window_view(xp, STFT_N)[::STFT_H][:nfr] * _SQRT_HANN
        sp = np.fft.rfft(fr, axis=1)
        sp *= G
        fr = np.fft.irfft(sp, STFT_N, axis=1) * _SQRT_HANN
        y = np.zeros(total + STFT_N)
        for j in range(step):
            blk = fr[j::step].reshape(-1)
            s0 = j * STFT_H
            y[s0:s0 + len(blk)] += blk
        out[:, c] = y[pad:pad + n] / 2.0          # sum of Hann windows at 75 % overlap = 2
    return out[:, 0] if mono else out


def lp_gain(fc_fn, order=2, res_db=0.0, res_w=0.3, open_above=None):
    """Gain function for stft_filter: Butterworth-like low-pass with optional resonance bump."""
    def fn(times, freqs):
        fc = np.asarray(fc_fn(times), float)[:, None]
        r = freqs[None, :] / fc
        g = 1.0 / np.sqrt(1.0 + r ** (2 * order))
        if res_db:
            bump = np.exp(-0.5 * (np.log2(np.maximum(r, 1e-4)) / res_w) ** 2)
            g = g * (1.0 + (undb(res_db) - 1.0) * bump)
        if open_above is not None:
            g[fc[:, 0] >= open_above, :] = 1.0
        return g
    return fn


def note_env(n, att, sus_n, rel):
    """Raised-cosine attack (att s), hold until sample sus_n, raised-cosine release (rel s). Length n."""
    e = np.ones(n)
    a = min(n, max(1, S(att)))
    e[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    if sus_n < n:
        r = max(1, S(rel))
        k = np.arange(n - sus_n)
        e[sus_n:] *= np.where(k < r, 0.5 + 0.5 * np.cos(np.pi * np.minimum(k, r) / r), 0.0)
    return e


def box_smooth(x, k):
    """Moving average (length k) applied twice -> triangular smoothing, edge-padded."""
    if k < 2:
        return x
    for _ in range(2):
        xp = np.concatenate([np.full(k, x[0]), x, np.full(k, x[-1])])
        c = np.concatenate([[0.0], np.cumsum(xp)])
        y = (c[k:] - c[:-k]) / k
        off = k // 2
        x = y[k - off:k - off + len(x)]
    return x


def add(buf, sig, t, gain=1.0, pan=0.0):
    """Mix sig (mono or stereo) into buf starting at time t (sample exact). pan in [-1, 1], centre = unity."""
    i0 = S(t)
    s = sig
    if i0 < 0:
        s = s[-i0:]
        i0 = 0
    n = min(len(s), len(buf) - i0)
    if n <= 0:
        return
    s = s[:n]
    if buf.ndim == 2:
        if s.ndim == 1:
            a = (pan + 1.0) * math.pi / 4.0
            buf[i0:i0 + n, 0] += s * (gain * math.cos(a) * math.sqrt(2.0))
            buf[i0:i0 + n, 1] += s * (gain * math.sin(a) * math.sqrt(2.0))
        else:
            buf[i0:i0 + n] += s * gain
    else:
        buf[i0:i0 + n] += (s.mean(axis=1) if s.ndim == 2 else s) * gain


def stereo():
    return np.zeros((N, 2))


def gap_mask():
    """1 everywhere except the pre-drop stop [T_CUT, 14.0) (12 ms raised-cosine fade into it)."""
    g = np.ones(N)
    c = S(T_CUT)
    f = S(0.012)
    g[c - f:c] = 0.5 + 0.5 * np.cos(np.pi * np.arange(f) / f)
    g[c:S(T_DROP)] = 0.0
    return g


GAP = gap_mask()


def hit_breath():
    """A 28 ms intake of breath before the BIG HIT: everything dips to -20 dB from 43.972 to 44.000."""
    g = np.ones(N)
    a, b = S(43.966), S(43.972)
    g[a:b] = 0.1 + 0.9 * (0.5 + 0.5 * np.cos(np.pi * np.arange(b - a) / (b - a)))
    g[b:S(T_HIT)] = 0.1
    return g


BREATH = hit_breath()


def conv_cut(x, h):
    """Convolution where every tail of material before T_CUT is stopped at T_CUT (the pre-drop silence)."""
    c = S(T_CUT)
    post = x.copy()
    post[:c] = 0.0
    y = fftconv(post, h, len(x))
    pre = x[:c]
    if np.any(pre):
        yp = fftconv(pre, h, c)
        f = S(0.012)
        yp[c - f:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(f) / f)
        y[:c] += yp
    return y


def make_ir(length, rt_lo, rt_mid, rt_hi, seed, predelay=0.02, lo_cut=110.0, hi_cut=10000.0, n_er=12):
    """Synthetic stereo reverb impulse: decorrelated noise, frequency dependent exponential decay."""
    rng = np.random.default_rng(seed)
    n = S(length)
    t = np.arange(n) / SR
    nf = nextpow2(n)
    f = np.fft.rfftfreq(nf, 1.0 / SR)
    centres = np.array([60.0, 160.0, 400.0, 1000.0, 2500.0, 6000.0, 12000.0])
    rts = np.interp(np.log2(centres), np.log2([100.0, 1000.0, 10000.0]), [rt_lo, rt_mid, rt_hi])
    lf = np.log2(np.maximum(f, 1.0))
    lc = np.log2(centres)
    masks = []
    for i in range(len(centres)):                           # triangular partition of unity in log f
        w = np.zeros_like(f)
        if i == 0:
            w[lf <= lc[0]] = 1.0
        if i == len(centres) - 1:
            w[lf >= lc[-1]] = 1.0
        if i > 0:
            m = (lf > lc[i - 1]) & (lf <= lc[i])
            w[m] = (lf[m] - lc[i - 1]) / (lc[i] - lc[i - 1])
        if i < len(centres) - 1:
            m = (lf > lc[i]) & (lf < lc[i + 1])
            w[m] = (lc[i + 1] - lf[m]) / (lc[i + 1] - lc[i])
        masks.append(w)
    tone = hp_mag(np.maximum(f, 1e-3), lo_cut, 1) * lp_mag(f, hi_cut, 1)
    ir = np.zeros((n, 2))
    for ch in range(2):
        X = np.fft.rfft(rng.standard_normal(n), nf) * tone
        acc = np.zeros(n)
        for w, rt in zip(masks, rts):
            acc += np.fft.irfft(X * w, nf)[:n] * np.exp(-6.9078 * t / rt)
        acc *= 1.0 - np.exp(-t / 0.018)                      # diffuse build-up
        for _ in range(n_er):                                # sparse early reflections
            pos = S(rng.uniform(0.004, 0.075))
            acc[pos] += rng.choice([-1.0, 1.0]) * rng.uniform(0.6, 1.6) * np.std(acc[:S(0.1)]) * 6.0
        ir[:, ch] = acc
    ir = np.concatenate([np.zeros((S(predelay), 2)), ir])
    ir /= np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True))   # unit energy per channel
    return ir


def pingpong_ir(delay=0.375, fb=0.42, taps=7, lp=3800.0):
    """Ping-pong delay as a stereo impulse response (first repeat left), each repeat a little darker."""
    K = 4096
    n = S(delay * taps) + K
    h = np.zeros((n, 2))
    f = np.fft.rfftfreq(K, 1.0 / SR)
    H1 = 1.0 / (1.0 + 1j * f / lp)
    for k in range(1, taps + 1):
        ker = np.fft.irfft(H1 ** k, K)
        pos = S(k * delay)
        h[pos:pos + K, 0 if k % 2 == 1 else 1] += fb ** (k - 1) * ker
    return h


def reverb(send, ir):
    out = np.zeros_like(send)
    for c in range(2):
        out[:, c] = conv_cut(send[:, c], ir[:, c])
    return out


def duck_env(hits, depth, att=0.004, hold=0.025, rel=0.15):
    """Kick side-chain gain curve. hits = [(time, weight)]."""
    g = np.ones(N)
    n = S(att + hold + 5 * rel)
    t = np.arange(n) / SR
    shp = np.where(t < att, t / att, np.where(t < att + hold, 1.0, np.exp(-(t - att - hold) / rel)))
    for tk, w in hits:
        i0 = S(tk)
        seg = g[i0:i0 + n]
        seg *= 1.0 - depth * w * shp[:len(seg)]
    return g


# ----------------------------------------------------------------------------------------- harmony
CHORDS = {                       # pad voicings (MIDI), voice-led, common tone G4 throughout the loop
    "Fmaj7": [53, 57, 64, 67, 72],          # F3 A3 E4 G4 C5
    "G6":    [55, 59, 64, 67, 74],          # G3 B3 E4 G4 D5
    "Am9":   [57, 60, 64, 67, 71],          # A3 C4 E4 G4 B4
    "Em7":   [52, 59, 62, 67, 71],          # E3 B3 D4 G4 B4
    "A":     [45, 52, 57, 64, 69, 73],      # A2 E3 A3 E4 A4 C#5  (open A, picardy third on top)
}
ARP = {                          # arpeggio tone sets (ascending)
    "Am9":   [69, 72, 76, 79, 83, 84],
    "Em7":   [64, 67, 71, 74, 76, 79],
    "Fmaj7": [65, 69, 72, 76, 79, 81],
    "G6":    [67, 71, 74, 76, 79, 83],
    "A":     [69, 73, 76, 81, 85, 88],
}
ROOT1 = {"Am9": 33, "Em7": 28, "Fmaj7": 29, "G6": 31, "A": 33}   # octave-1 roots for the sub (41-55 Hz)
PROG = [
    (0.0, 10.0, "Am9"), (10.0, 14.0, "Em7"),
    (14.0, 18.0, "Fmaj7"), (18.0, 22.0, "G6"), (22.0, 26.0, "Am9"), (26.0, 30.0, "Em7"),
    (30.0, 34.0, "Fmaj7"), (34.0, 38.0, "G6"), (38.0, 40.0, "Am9"),
    (40.0, 42.0, "Fmaj7"), (42.0, 44.0, "G6"), (44.0, 46.0, "Am9"),
    (46.0, 50.0, "Fmaj7"), (50.0, 52.0, "G6"),
    (52.0, 54.0, "Am9"), (54.0, 56.0, "Fmaj7"), (56.0, 60.0, "A"),
]


def chord_at(t):
    for a, b, c in PROG:
        if a <= t < b:
            return c
    return PROG[-1][2]


# ----------------------------------------------------------------------------------------- instruments
_pluck_cache = {}


def pluck(midi, bright=1.0, decay=0.24, length=0.9):
    """Glassy pluck: additive saw-like partials whose upper harmonics die first (a filter envelope),
    a pure sine body and a short, slightly inharmonic glass 'tink'. Cached; peak-normalised."""
    key = (int(midi), round(bright, 2), round(decay, 3), round(length, 2))
    if key in _pluck_cache:
        return _pluck_cache[key]
    f = float(hz(midi))
    n = S(length)
    t = np.arange(n) / SR
    y = np.zeros(n)
    K = max(1, min(int(4 + 12 * bright), int(17000.0 / f)))
    for k in range(1, K + 1):
        dk = (1.0 + 0.55 * (k - 1) / max(bright, 0.2)) / decay
        y += (1.0 / k ** 1.2) * np.exp(-t * dk) * np.sin(2.0 * np.pi * k * f * t)
    y += 0.6 * np.sin(2.0 * np.pi * f * t) * np.exp(-t / (decay * 2.4))
    if 4.07 * f < 20000:
        y += 0.10 * bright * np.sin(2.0 * np.pi * 4.07 * f * t) * np.exp(-t / 0.045)
    a = S(0.0015)
    y[:a] *= np.linspace(0.0, 1.0, a)
    r = S(0.03)
    y[-r:] *= np.linspace(1.0, 0.0, r)
    y /= np.max(np.abs(y))
    _pluck_cache[key] = y
    return y


_bass_cache = {}


def bass_note(midi, length=0.40):
    """Plucked synth bass, additive with a decaying 12 dB/oct filter envelope (cached)."""
    if midi in _bass_cache:
        return _bass_cache[midi]
    f = float(hz(midi))
    n = S(length)
    t = np.arange(n) / SR
    fc = 240.0 + 1000.0 * np.exp(-t / 0.05)
    y = np.zeros(n)
    for k in range(1, int(min(40, 5000.0 / f)) + 1):
        y += (1.0 / k) * np.sin(2.0 * np.pi * k * f * t) / np.sqrt(1.0 + (k * f / fc) ** 4)
    env = (1.0 - np.exp(-t / 0.0025)) * np.exp(-t / 0.19)
    env[-S(0.03):] *= np.linspace(1.0, 0.0, S(0.03))
    y *= env
    y /= np.max(np.abs(y))
    _bass_cache[midi] = y
    return y


def supersaw_notes(notes, dur, rng, voices=7, spread=14.0, note_amps=None):
    """Detuned supersaw chord, each voice panned somewhere across the stereo field (decorrelated width)."""
    n = S(dur)
    out = np.zeros((n, 2))
    dets = np.linspace(-1.0, 1.0, voices)
    vamp = 1.0 - 0.35 * np.abs(dets)
    for j, m in enumerate(notes):
        f = float(hz(m))
        an = 1.0 if note_amps is None else note_amps[j]
        pans = rng.permutation(np.linspace(-0.9, 0.9, voices))
        for d, va, p in zip(dets, vamp, pans):
            cents = d * spread + rng.uniform(-1.2, 1.2)
            s = saw(f * 2.0 ** (cents / 1200.0), n, rng.random()) * (va * an)
            ang = (p + 1.0) * math.pi / 4.0
            out[:, 0] += s * math.cos(ang)
            out[:, 1] += s * math.sin(ang)
    return out / math.sqrt(voices * len(notes))


def kick_sound(soft=False):
    """Tight kick: pitch-swept sine body + short click. Starts at phase 0 on its first sample."""
    n = S(0.7)
    t = np.arange(n) / SR
    if soft:     # heartbeat: rounder, lower, no click
        f = 40.0 + 55.0 * np.exp(-t / 0.045)
        amp = (1.0 - np.exp(-t / 0.0025)) * np.exp(-t / 0.22)
        y = np.sin(phase_of(f)) * amp
        y = fft_filter(y, lambda fr: lp_mag(fr, 220.0, 2))
        return y / np.max(np.abs(y))
    f = 46.0 + 120.0 * np.exp(-t / 0.03) + 150.0 * np.exp(-t / 0.004)
    amp = (1.0 - np.exp(-t / 0.0006)) * np.exp(-t / 0.26)
    amp *= np.where(t > 0.26, np.exp(-(t - 0.26) / 0.05), 1.0)
    body = np.sin(phase_of(f)) * amp
    rng = np.random.default_rng(7)
    nz = fft_filter(rng.standard_normal(n), lambda fr: hp_mag(np.maximum(fr, 1e-3), 1800.0, 2) * lp_mag(fr, 9000.0, 2))
    click = nz * np.exp(-t / 0.0022)
    click[:S(0.0003)] *= np.linspace(0, 1, S(0.0003))
    y = body + 0.22 * click
    y = np.tanh(1.5 * y) / np.tanh(1.5)
    return y / np.max(np.abs(y))


def hat_sound(seed, decay=0.032, length=0.25, bright=1.0):
    rng = np.random.default_rng(seed)
    n = S(length)
    t = np.arange(n) / SR
    nz = fft_filter(rng.standard_normal(n), lambda f: hp_mag(np.maximum(f, 1e-3), 7200.0, 3) * lp_mag(f, 15500.0, 2))
    metal = np.zeros(n)
    for fr in (6123.0, 7041.0, 8388.0, 9450.0, 10673.0, 12110.0):
        metal += np.sin(2.0 * np.pi * fr * bright * t + rng.random() * 6.283)
    y = (0.85 * nz / np.std(nz) + 0.18 * metal) * np.exp(-t / decay)
    a = S(0.0004)
    y[:a] *= np.linspace(0.0, 1.0, a)
    y[-S(0.01):] *= np.linspace(1.0, 0.0, S(0.01))
    return y / np.max(np.abs(y))


def snare_sound(seed, tone=190.0, ndecay=0.09):
    rng = np.random.default_rng(seed)
    n = S(0.45)
    t = np.arange(n) / SR
    f1 = tone * (1.0 + 0.5 * np.exp(-t / 0.012))
    body = np.sin(phase_of(f1)) * np.exp(-t / 0.05) + 0.45 * np.sin(phase_of(1.62 * f1)) * np.exp(-t / 0.035)
    nz = fft_filter(rng.standard_normal(n), lambda f: hp_mag(np.maximum(f, 1e-3), 1300.0, 2) * lp_mag(f, 9500.0, 2))
    nz /= np.std(nz)
    y = 0.5 * body + 0.55 * nz * np.exp(-t / ndecay)
    y[:S(0.0004)] *= np.linspace(0.0, 1.0, S(0.0004))
    y[-S(0.02):] *= np.linspace(1.0, 0.0, S(0.02))
    return y / np.max(np.abs(y))


def crash_sound(seed, length=4.5, decay=1.5):
    """Stereo crash: shaped noise plus a cloud of inharmonic metallic partials."""
    rng = np.random.default_rng(seed)
    n = S(length)
    t = np.arange(n) / SR
    out = np.zeros((n, 2))
    for ch in range(2):
        nz = fft_filter(rng.standard_normal(n),
                        lambda f: hp_mag(np.maximum(f, 1e-3), 2600.0, 2) * lp_mag(f, 14000.0, 2)
                        * (1.0 + 0.8 * np.exp(-0.5 * (np.log2(np.maximum(f, 1.0) / 6500.0) / 0.5) ** 2)))
        nz /= np.std(nz)
        metal = np.zeros(n)
        for _ in range(36):
            fr = math.exp(rng.uniform(math.log(3000.0), math.log(12500.0)))
            metal += np.sin(2.0 * np.pi * fr * t + rng.random() * 6.283) * np.exp(-t / (decay * rng.uniform(0.3, 1.0)))
        metal /= 6.0
        y = (0.75 * nz + 0.5 * metal) * np.exp(-t / decay) + 0.5 * nz * np.exp(-t / 0.06)
        out[:, ch] = y
    out[:S(0.001)] *= np.linspace(0.0, 1.0, S(0.001))[:, None]
    out[-S(0.05):] *= np.linspace(1.0, 0.0, S(0.05))[:, None]
    return out / np.max(np.abs(out))


def impact_sound(seed, size=1.0):
    """Cinematic impact: deep pitch-dropping boom, low thud, short crack (mono, centred)."""
    rng = np.random.default_rng(seed)
    n = S(3.2 * size)
    t = np.arange(n) / SR
    f = 30.0 + 80.0 * np.exp(-t / (0.10 * size)) + 60.0 * np.exp(-t / 0.01)
    boom = np.sin(phase_of(f)) * (1.0 - np.exp(-t / 0.0008)) * np.exp(-t / (0.95 * size))
    thud = fft_filter(rng.standard_normal(n), lambda fr: lp_mag(fr, 260.0, 2))
    thud = thud / np.std(thud[:S(0.1)]) * np.exp(-t / 0.07)
    crack = fft_filter(rng.standard_normal(n), lambda fr: hp_mag(np.maximum(fr, 1e-3), 900.0, 2) * lp_mag(fr, 7000.0, 2))
    crack = crack / np.std(crack) * np.exp(-t / 0.014)
    y = boom + 0.12 * thud + 0.10 * crack
    y[:S(0.0004)] *= np.linspace(0.0, 1.0, S(0.0004))
    y[-S(0.1):] *= np.linspace(1.0, 0.0, S(0.1))
    y = np.tanh(1.3 * y) / np.tanh(1.3)
    return y / np.max(np.abs(y))


def bell(midi, length=1.4, decay=0.4, ratios=((1.0, 1.0, 1.0), (2.756, 0.45, 0.2), (5.404, 0.14, 0.08)),
         att=0.001, click=0.0, seed=0):
    """Sine bell: list of (ratio, amp, decay-multiplier) partials, optional high noise tick."""
    f = float(hz(midi))
    n = S(length)
    t = np.arange(n) / SR
    y = np.zeros(n)
    for r, a, dm in ratios:
        if r * f < 20000:
            y += a * np.sin(2.0 * np.pi * r * f * t) * np.exp(-t / (decay * dm))
    if click:
        rng = np.random.default_rng(seed)
        nz = fft_filter(rng.standard_normal(n), lambda fr: hp_mag(np.maximum(fr, 1e-3), 5000.0, 2) * lp_mag(fr, 13000.0, 2))
        y += click * nz / np.std(nz) * np.exp(-t / 0.0016)
    a = max(1, S(att))
    y[:a] *= np.linspace(0.0, 1.0, a)
    y[-S(0.03):] *= np.linspace(1.0, 0.0, S(0.03))
    return y / np.max(np.abs(y))


def noise_riser(dur, f0, f1, seed, shape=1.4, bw=0.9):
    """Band-passed noise whose centre sweeps exponentially f0 -> f1, amplitude rising."""
    n = S(dur)
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, 2))

    def g(times, freqs):
        u = np.clip(times / dur, 0.0, 1.0)[:, None]
        fc = np.exp(math.log(f0) + (math.log(f1) - math.log(f0)) * u ** shape)
        r = np.log2(np.maximum(freqs[None, :], 1.0) / fc)
        return np.exp(-0.5 * (r / bw) ** 2) + 0.12 * hp_mag(np.maximum(freqs[None, :], 1e-3), 4000.0, 2) * u
    y = stft_filter(x, g)
    u = np.arange(n) / n
    y *= (u ** 2.2)[:, None]
    return y / np.max(np.abs(y))


def pitch_riser(m0, m1, dur, seed):
    """Three detuned saws gliding m0 -> m1 (accelerating), opening low-pass, rising amplitude."""
    rng = np.random.default_rng(seed)
    n = S(dur)
    u = np.arange(n) / n
    m = m0 + (m1 - m0) * u ** 1.5
    f = hz(m)
    out = np.zeros((n, 2))
    for det, pan in ((-9.0, -0.7), (0.0, 0.0), (9.0, 0.7)):
        s = saw(f * 2.0 ** (det / 1200.0), n, rng.random())
        a = (pan + 1.0) * math.pi / 4.0
        out[:, 0] += s * math.cos(a)
        out[:, 1] += s * math.sin(a)
    out = stft_filter(out, lp_gain(lambda tt: 500.0 * (18.0 ** np.clip(tt / dur, 0, 1) ** 1.3), order=2, res_db=4.0))
    out *= (smoothstep(u * 4.0) * u ** 1.8)[:, None]
    return out / np.max(np.abs(out))


def reverse_swell(src, ir, length):
    """Reverse reverb: convolve src with ir, keep `length` seconds, reverse -> swell that ends on its peak."""
    n = S(length)
    if src.ndim == 1:
        src = np.stack([src, src], axis=1)
    out = np.zeros((n, 2))
    for c in range(2):
        out[:, c] = fftconv(src[:, c], ir[:, c], n)[:n]
    out += 0.6 * np.concatenate([src, np.zeros((max(0, n - len(src)), 2))])[:n]
    out = out[::-1].copy()
    u = np.arange(n) / n
    out *= (smoothstep(u * 2.5) * (0.25 + 0.75 * u ** 1.5))[:, None]
    f = S(0.008)
    out[-f:] *= np.linspace(1.0, 0.0, f)[:, None]
    return out / np.max(np.abs(out))


def shimmer_sound(notes, length, seed, att=0.015, decay=2.2, grains=50, grain_span=3.0):
    """Held cream-white shimmer: chorused sine pairs with gentle tremolo + a cloud of sparkle grains."""
    rng = np.random.default_rng(seed)
    n = S(length)
    t = np.arange(n) / SR
    out = np.zeros((n, 2))
    for m in notes:
        f = float(hz(m))
        for ch, det in ((0, -3.5), (1, 3.5)):
            trem = 1.0 + 0.22 * np.sin(2.0 * np.pi * rng.uniform(4.5, 7.0) * t + rng.random() * 6.283)
            out[:, ch] += np.sin(2.0 * np.pi * f * 2.0 ** (det / 1200.0) * t + rng.random() * 6.283) * trem
    env = (1.0 - np.exp(-t / att)) * np.exp(-t / decay)
    out *= env[:, None] / len(notes)
    for _ in range(grains):
        tg = grain_span * rng.random() ** 1.7
        m = rng.choice(notes) + 12 * int(rng.integers(0, 2))
        f = float(hz(m))
        gl = rng.uniform(0.04, 0.10)
        gn = S(gl)
        g = np.sin(2.0 * np.pi * f * np.arange(gn) / SR) * np.hanning(gn)
        amp = 0.35 * math.exp(-tg / (grain_span * 0.6)) * rng.uniform(0.5, 1.0)
        add(out, g, tg, amp, pan=rng.uniform(-0.8, 0.8))
    out[-S(0.05):] *= np.linspace(1.0, 0.0, S(0.05))[:, None]
    return out / np.max(np.abs(out))


# ----------------------------------------------------------------------------------------- composition
MIX = {               # stem gains (pre-master, linear)
    "drone": 0.40, "ticks": 0.14, "arp_boot": 0.20, "arp": 0.22, "pads": 0.60, "padlow": 0.08,
    "stab": 0.45, "lead": 0.30, "bass": 0.20, "sub": 0.12, "kick": 0.62, "hats": 0.24, "snare": 0.30,
    "impact": 0.55, "crash": 0.25, "risers": 0.20, "revswell": 0.22, "drops": 0.07, "pings": 0.36,
    "chimes": 0.24, "tone": 0.20, "shimmer": 0.20, "slams": 0.30, "tension": 0.02,
}

STEP16 = 0.125
P1 = [0, 2, 4, 2, 1, 3, 5, 3, 0, 2, 4, 5, 3, 4, 2, 1]
P2 = [0, 2, 4, 2, 1, 3, 5, 3, 2, 4, 5, 4, 3, 2, 1, 2]
SOFT = [0, 2, 4, 2, 1, 3, 5, 3]
BOOT_B1 = [57, 64, 71, 64, 60, 67, 72, 67]
BOOT_B2 = [57, 64, 69, 64, 60, 67, 71, 76]
EM_UP = [64, 67, 71, 74, 76, 79, 83, 86, 88, 91, 95, 98]

COUNTER = [(22.0, 76, 1.5), (23.5, 74, 0.5), (24.0, 72, 1.0), (25.0, 71, 0.5), (25.5, 72, 0.5),
           (26.0, 71, 2.0), (28.0, 74, 1.0), (29.0, 76, 0.5), (29.5, 79, 0.5),
           (30.0, 81, 1.5), (31.5, 79, 0.5), (32.0, 76, 1.0), (33.0, 72, 0.5), (33.5, 74, 0.5),
           (34.0, 76, 2.0), (36.0, 74, 1.0), (37.0, 71, 1.0),
           (38.0, 72, 1.0), (39.0, 71, 0.5), (39.5, 69, 1.3)]
FINALE_MELODY = [(52.0, 76, 1.5), (53.5, 79, 0.5), (54.0, 81, 1.5), (55.5, 79, 0.5), (56.0, 76, 3.6)]

PING_TIMES = [30.5, 31.5, 32.5, 33.5]
PING_NOTES = [93, 91, 88, 84]                       # A6 G6 E6 C6 - echoes the counter-melody's descent
CHIME_TIMES = [46.5, 47.0, 47.5, 48.0, 48.5, 49.0]
CHIME_NOTES = [84, 88, 91, 93, 96, 100]             # C6 E6 G6 A6 C7 E7 - rising checklist
TONE_TIME = 50.5

GROOVE_KICKS = [float(t) for t in np.arange(14.0, 40.0, BEAT)] + [44.0, 44.5, 45.0, 45.5]
BUILD_KICKS = [(42.0, 0.50), (42.5, 0.58), (43.0, 0.66), (43.5, 0.76)]
HEART = [(40.0, 1.0), (40.25, 0.45), (41.0, 1.0), (41.25, 0.45)]
SNARE_ROLL = ([42.0 + 0.25 * i for i in range(4)] + [43.0 + 0.125 * i for i in range(4)]
              + [43.5 + 0.0625 * i for i in range(8)])


def render_drone():
    n = S(T_CUT)
    t = TT[:n]
    rng = np.random.default_rng(101)
    out = np.zeros((N, 2))
    for ch, sg in ((0, -1.0), (1, 1.0)):
        y = 0.55 * np.sin(2.0 * np.pi * float(hz(33)) * t + rng.random() * 6.283)
        y += 0.30 * saw(float(hz(45)) * 2.0 ** (sg * 4.0 / 1200.0), n, rng.random())
        y += 0.18 * saw(float(hz(52)) * 2.0 ** (-sg * 3.0 / 1200.0), n, rng.random())
        y += 0.09 * saw(float(hz(57)) * 2.0 ** (sg * 6.0 / 1200.0), n, rng.random())
        out[:n, ch] = y

    def fc(times):
        base = curve([(0.0, 160.0), (3.0, 280.0), (6.0, 480.0), (10.0, 850.0), (14.0, 1500.0)], times, True)
        return base * (1.0 + 0.18 * np.sin(2.0 * np.pi * 0.125 * times))
    out[:n] = stft_filter(out[:n], lp_gain(fc, order=2, res_db=3.0))
    amp = smoothstep(t / 5.5) * curve([(0.0, 1.0), (6.0, 1.0), (10.0, 0.72), (13.875, 0.45)], t)
    out[:n] *= amp[:, None]
    return out


def render_ticks():
    out = stereo()
    hi = bell(100, length=0.05, decay=0.006, ratios=((1.0, 1.0, 1.0),), att=0.0003, click=0.5, seed=3)
    lo = bell(93, length=0.05, decay=0.005, ratios=((1.0, 1.0, 1.0),), att=0.0003, click=0.5, seed=4)
    for k in range(12):                                 # 0.0, 0.5, ... 5.5
        down = k % 4 == 0
        add(out, hi if down else lo, k * BEAT, 1.0 if down else 0.7)
    return out


def arp_events():
    """All pluck events: (time, midi, velocity, brightness, decay, pan, stem)."""
    rng = np.random.default_rng(202)
    ev = []
    # boot arpeggio: 8ths from 2.0, low and dark, slowly opening
    for k in range(int(2.0 / 0.25), int(10.0 / 0.25)):
        t = k * 0.25
        pat = BOOT_B1 if int(t // BAR) % 2 == 1 else BOOT_B2
        step = k % 8
        u = (t - 2.0) / 8.0
        vel = (0.45 + 0.2 * u) * (1.0 if step % 2 == 0 else 0.78)
        ev.append((t, pat[step], vel, 0.35 + 0.4 * u, 0.30, -0.25 if step % 2 else 0.25, "arp_boot"))
    # rising arpeggio 10.0 -> 13.75 over Em7: each beat a 4-note group one chord tone higher
    for b in range(8):
        for i in range(4):
            t = 10.0 + b * BEAT + i * STEP16
            if t >= T_CUT - 0.01:
                continue
            u = (t - 10.0) / 3.875
            ev.append((t, EM_UP[b + i], 0.55 + 0.45 * u, 0.7 + 0.7 * u, 0.22, -0.2 if i % 2 else 0.2, "arp_boot"))
    # lead arpeggio: 16ths, 14.0 -> 46.0
    for k in range(int(14.0 / STEP16), int(46.0 / STEP16)):
        t = k * STEP16
        ch = chord_at(t)
        bar = int(t // BAR)
        step = k % 16
        pat = P1 if bar % 2 == 0 else P2
        m = ARP[ch][pat[step]]
        acc = 1.0 if step % 4 == 0 else (0.82 if step % 2 == 0 else 0.64)
        if t < 22.0:
            vel, br = 0.85, 1.2
        elif t < 30.0:
            vel, br = 0.76, 1.3
        elif t < 34.0:
            vel, br = 0.55, 1.3
        elif t < 40.0:
            vel, br = 0.84, 1.3
        elif t < 42.0:
            vel, br = 0.80, 1.3
        elif t < 44.0:
            vel, br = 0.80 + 0.2 * (t - 42.0) / 2.0, 1.3
        else:
            vel, br = 1.0, 1.5
        vel *= acc * (1.0 + rng.uniform(-0.04, 0.04))
        ev.append((t, m, vel, br, 0.24, 0.16 if k % 2 else -0.16, "arp"))
    # soft arpeggio: 8ths, an octave lower, 46.0 -> 56.0
    for k in range(int(46.0 / 0.25), int(56.0 / 0.25)):
        t = k * 0.25
        ch = chord_at(t)
        step = k % 8
        m = ARP[ch][SOFT[step]] - 12
        fin = t >= T_FINALE
        vel = (0.58 if fin else 0.52) * (1.0 if step % 2 == 0 else 0.8)
        ev.append((t, m, vel, 0.75 if fin else 0.65, 0.30, -0.2 if step % 2 else 0.2, "arp"))
    # finale: rising quarter notes on A major into the dissolve
    for i, m in enumerate(ARP["A"]):
        t = 56.0 + i * BEAT
        ev.append((t, m, 0.62 - 0.06 * i, 0.7, 0.34, -0.3 + 0.12 * i, "arp"))
    # gentle A major strike on the resolution (56.0)
    for m in (57, 64, 69, 73):
        ev.append((56.0, m, 0.5, 0.8, 0.45, 0.0, "arp"))
    # chord strike at 52.0 (finale onset)
    for m in (45, 57, 64, 71, 72, 76, 79):
        ev.append((T_FINALE, m, 0.62, 0.9, 0.40, 0.0, "arp"))
    return ev


def render_arps():
    stems = {"arp_boot": stereo(), "arp": stereo()}
    for t, m, vel, br, dec, pan, stem in arp_events():
        add(stems[stem], pluck(m, round(br * 20) / 20.0, dec), t, vel, pan)
    # boot arpeggio low-pass: very soft at 2.0, opening through the rain
    fcb = lambda tt: curve([(0.0, 650.0), (2.0, 650.0), (6.0, 1000.0), (10.0, 2400.0), (13.9, 9000.0)], tt, True)
    nb = S(T_DROP) + S(0.5)
    stems["arp_boot"][:nb] = stft_filter(stems["arp_boot"][:nb], lp_gain(fcb, order=2, res_db=2.0))
    return stems


PAD_EVENTS = [   # start, end, chord, attack, release, level
    (6.0, 10.0, "Am9", 3.2, 1.2, 0.48),
    (10.0, T_CUT, "Em7", 1.0, 0.01, 0.58),
    (14.0, 18.0, "Fmaj7", 0.08, 1.0, 0.85),
    (18.0, 22.0, "G6", 0.35, 1.0, 0.80),
    (22.0, 26.0, "Am9", 0.35, 1.0, 0.78),
    (26.0, 30.0, "Em7", 0.35, 1.0, 0.78),
    (30.0, 34.0, "Fmaj7", 0.35, 1.0, 0.78),
    (34.0, 38.0, "G6", 0.35, 1.0, 0.80),
    (38.0, 40.0, "Am9", 0.35, 0.8, 0.80),
    (40.0, 42.0, "Fmaj7", 0.25, 0.8, 0.75),
    (42.0, 44.0, "G6", 0.25, 0.4, 0.85),
    (44.0, 46.0, "Am9", 0.02, 1.0, 1.10),
    (46.0, 50.0, "Fmaj7", 0.6, 1.2, 0.48),
    (50.0, 52.0, "G6", 0.5, 1.0, 0.46),
    (52.0, 54.0, "Am9", 0.15, 1.2, 0.80),
    (54.0, 56.0, "Fmaj7", 0.5, 1.4, 0.86),
    (56.0, 60.0, "A", 0.6, 0.0, 0.92),
]


def render_pads():
    rng = np.random.default_rng(303)
    pads = stereo()
    low = stereo()
    for (a, b, ch, att, rel, lvl) in PAD_EVENTS:
        dur = (b - a) + rel
        notes = CHORDS[ch]
        amps = [0.85] + [1.0] * (len(notes) - 1)
        if ch == "A":
            amps = [0.7, 0.85, 1.0, 1.0, 1.0, 1.1]
        sig = supersaw_notes(notes, dur, rng, note_amps=amps)
        n = len(sig)
        sig *= (note_env(n, att, S(b - a), rel) * lvl)[:, None]
        add(pads, sig, a)
        # low root layer (warm triangle) where the bass is not playing
        if a < T_DROP or a >= 46.0:
            r = ROOT1[ch] + 12
            f = float(hz(r))
            tt = np.arange(n) / SR
            tri = sum(((-1) ** j) / (2 * j + 1) ** 2 * np.sin(2 * np.pi * (2 * j + 1) * f * tt) for j in range(4))
            tri *= note_env(n, max(att, 0.4), S(b - a), max(rel, 0.01)) * lvl
            add(low, tri, a)
    fc = lambda tt: curve([(0.0, 500.0), (6.0, 600.0), (9.5, 2000.0), (13.8, 6500.0), (14.0, 6500.0),
                           (22.0, 7000.0), (40.0, 7000.0), (44.0, 9000.0), (46.0, 3400.0), (52.0, 3800.0),
                           (53.5, 6000.0), (56.0, 6000.0), (57.5, 7000.0), (60.0, 2000.0)], tt, True)
    pads = stft_filter(pads, lp_gain(fc, order=2, res_db=1.5))
    return pads, low


def render_stabs():
    rng = np.random.default_rng(404)
    out = stereo()
    for t, ch, lvl in ((T_DROP, "Fmaj7", 1.0), (T_HIT, "Am9", 1.6)):
        notes = [ROOT1[ch] + 12] + CHORDS[ch] + [m + 12 for m in CHORDS[ch][2:]]
        sig = supersaw_notes(notes, 3.0, rng, voices=5, spread=18.0)
        sig = fft_filter(sig, lambda f: lp_mag(f, 5200.0, 2))
        n = len(sig)
        tt = np.arange(n) / SR
        env = (1.0 - np.exp(-tt / 0.002)) * (0.65 * np.exp(-tt / 0.35) + 0.35 * np.exp(-tt / 1.4))
        env[-S(0.1):] *= np.linspace(1.0, 0.0, S(0.1))
        add(out, sig * env[:, None], t, lvl)
    return out


def render_lead(notes, seed):
    """Warm monophonic lead with portamento and delayed vibrato (counter-melody / finale line)."""
    t_start = notes[0][0]
    t_end = min(60.0, notes[-1][0] + notes[-1][2] + 0.4)
    i0, i1 = S(t_start), S(t_end)
    n = i1 - i0
    t = np.arange(n) / SR + t_start
    logf = np.zeros(n)
    amp = np.zeros(n)
    tsince = np.zeros(n)
    for (ts, m, d) in notes:
        a, b = S(ts) - i0, min(n, S(ts + d) - i0)
        logf[a:] = math.log2(float(hz(m)))
        tsince[a:] = np.arange(n - a) / SR
        amp[a:] = np.maximum(amp[a:], note_env(n - a, 0.06, b - a, 0.2) * (0.85 + 0.15 * smoothstep(np.arange(n - a) / S(0.6))))
    k = S(0.035)
    logf = np.concatenate([np.full(k, logf[0]), box_smooth(logf, k)[:-k]])    # causal glide from each note start
    vib = (11.0 / 1200.0) * smoothstep((tsince - 0.25) / 0.5) * np.sin(2.0 * np.pi * 5.1 * t)
    f = 2.0 ** (logf + vib)
    rng = np.random.default_rng(seed)
    out = np.zeros((n, 2))
    for ch, det in ((0, -6.0), (1, 6.0)):
        fd = f * 2.0 ** (det / 1200.0)
        out[:, ch] = 0.55 * saw(fd, n, rng.random()) + 0.5 * np.sin(phase_of(fd) + rng.random() * 6.283)
    out = stft_filter(out, lambda tt, fr: lp_mag(fr, 3000.0, 2)[None, :] * hp_mag(np.maximum(fr, 1e-3), 160.0, 1)[None, :]
                      * np.ones((len(tt), 1)))
    out *= amp[:, None]
    full = stereo()
    add(full, out, t_start)
    return full


def render_bass():
    """Sub sine on chord roots (mono, < 120 Hz) and an 8th-note plucked bass (mono)."""
    segs = [(14.0, 40.0), (40.0, 42.0), (44.0, 46.0), (52.0, FADE_END)]
    f = np.full(N, float(hz(33)))
    for a, b, ch in PROG:
        f[S(a):S(b)] = float(hz(ROOT1[ch]))
    f = box_smooth(f, S(0.012))
    lvl = curve([(0.0, 0.0), (13.99, 0.0), (14.0, 0.0), (14.003, 1.0), (40.0, 1.0), (40.1, 0.62), (42.0, 0.62),
                 (42.25, 0.0), (43.999, 0.0), (44.003, 1.05), (46.0, 1.05), (46.4, 0.0), (51.999, 0.0),
                 (52.01, 0.85), (56.0, 0.85), (60.0, 0.85)], TT)
    ph = phase_of(f)
    sub = np.sin(ph) + 0.16 * np.sin(2.0 * ph)
    sub = np.tanh(1.2 * sub) / np.tanh(1.2) * lvl
    bass = np.zeros(N)
    for k in range(int(14.0 / 0.25), int(46.0 / 0.25)):
        t = k * 0.25
        if 42.0 <= t < 44.0:
            continue
        r = ROOT1[chord_at(t)] + 12
        m = r if k % 2 == 0 else r + 12
        add(bass, bass_note(m), t, 1.0 if k % 2 == 0 else 0.7)
    return sub, bass


def render_drums():
    kick = np.zeros(N)
    k_main = kick_sound()
    k_soft = kick_sound(soft=True)
    hits = []
    for t in GROOVE_KICKS:
        w = 1.12 if t in (T_DROP, T_HIT) else 1.0
        add(kick, k_main, t, w)
        hits.append((t, 1.0))
    for t, w in BUILD_KICKS:
        add(kick, k_main, t, w)
        hits.append((t, w))
    for t, w in HEART:
        add(kick, k_soft, t, 0.9 * w)
        hits.append((t, 0.6 * w))
    # hats on the offbeats (x.25 / x.75), open hat into every chord change, soft 16th ghosts from 22.0
    hats = stereo()
    rng = np.random.default_rng(505)
    closed = [hat_sound(600 + i) for i in range(4)]
    opened = hat_sound(700, decay=0.16, length=0.6)
    ghost = hat_sound(800, decay=0.014, length=0.08, bright=1.1)
    offbeats = [t for t in np.arange(14.25, 40.0, BEAT)] + [44.25, 44.75, 45.25, 45.75]
    for i, t in enumerate(offbeats):
        t = float(t)
        is_open = abs(((t - 14.0) % 4.0) - 3.75) < 1e-6 and t < 40.0
        v = (1.2 if t > T_HIT else 1.0) * (1.0 + rng.uniform(-0.12, 0.12))
        if is_open:
            add(hats, opened, t, 0.8 * v, pan=0.25)
        else:
            add(hats, closed[i % 4], t, v, pan=0.2)
    for t in np.arange(22.0 + 0.125, 40.0, 0.25):
        add(hats, ghost, float(t), 0.22 * (1.0 + rng.uniform(-0.2, 0.2)), pan=-0.35)
    # accelerating snare roll 42.0 -> 43.94, crescendo and rising tone
    snare = stereo()
    for i, t in enumerate(SNARE_ROLL):
        u = i / (len(SNARE_ROLL) - 1)
        s = snare_sound(900 + i, tone=175.0 + 85.0 * u, ndecay=0.11 - 0.05 * u)
        add(snare, s, t, 0.36 + 0.64 * u ** 1.4, pan=0.15 if i % 2 else -0.15)
    # glyph slams on the HEVI letters (14.5, 15.0, 15.5)
    slams = stereo()
    tt = np.arange(S(0.5)) / SR
    tom = np.sin(phase_of(75.0 + 70.0 * np.exp(-tt / 0.03))) * np.exp(-tt / 0.13) * (1.0 - np.exp(-tt / 0.0008))
    rng2 = np.random.default_rng(32)
    clack = fft_filter(rng2.standard_normal(len(tt)),
                       lambda f: hp_mag(np.maximum(f, 1e-3), 1500.0, 2) * lp_mag(f, 6500.0, 2))
    clack = clack / np.std(clack) * np.exp(-tt / 0.006)
    clack[:S(0.0004)] *= np.linspace(0.0, 1.0, S(0.0004))
    for t, p, m in ((14.5, -0.15, 100), (15.0, 0.15, 103), (15.5, 0.0, 105)):     # glyph blips E7 G7 A7
        glyph = bell(m, length=0.5, decay=0.05, ratios=((1.0, 1.0, 1.0),), att=0.0004)
        slam = 0.7 * tom + 0.6 * clack + 0.45 * glyph
        add(slams, slam / np.max(np.abs(slam)), t, 1.0, pan=p)
    return kick, hats, snare, slams, hits


def render_fx(hall_ir):
    fx = {k: stereo() for k in ("impact", "crash", "risers", "revswell", "drops", "pings", "chimes", "tone",
                                 "shimmer", "tension")}
    # impacts: the drop, the big hit (biggest), the soft finale landing
    add(fx["impact"], impact_sound(11, 1.0), T_DROP, 1.0)
    add(fx["impact"], impact_sound(12, 1.35), T_HIT, 1.0)
    imp_soft = fft_filter(impact_sound(13, 0.8), lambda f: lp_mag(f, 400.0, 2))
    add(fx["impact"], imp_soft / np.max(np.abs(imp_soft)), T_FINALE, 0.7)
    # crashes
    add(fx["crash"], crash_sound(21, 4.5, 1.5), T_DROP, 0.85)
    add(fx["crash"], crash_sound(22, 6.0, 2.0), T_HIT, 1.8)
    # risers 10.0 -> 13.875 and 42.0 -> 43.97
    add(fx["risers"], noise_riser(T_CUT - 10.0, 250.0, 9000.0, 31), 10.0, 1.0)
    add(fx["risers"], pitch_riser(52, 76, T_CUT - 10.0, 32), 10.0, 0.55)
    r2 = noise_riser(1.97, 400.0, 11000.0, 33, shape=1.2)
    r2[-S(0.006):] *= np.linspace(1, 0, S(0.006))[:, None]
    add(fx["risers"], r2, 42.0, 1.0)
    p2 = pitch_riser(55, 79, 1.97, 34)
    p2[-S(0.006):] *= np.linspace(1, 0, S(0.006))[:, None]
    add(fx["risers"], p2, 42.0, 0.5)
    # reverse swells: into the drop (reverse reverb of the coming Fmaj7 + reversed crash),
    # into the big hit (reversed crash), into the finale (reverse reverb of Am9), soft ones into 22 and 34
    chord = sum(pluck(m, 1.0, 0.3) for m in (53, 57, 64, 67, 72, 76))
    src = np.stack([chord, chord], axis=1) / 6.0
    cr = crash_sound(41, 2.0, 0.6)
    src = np.concatenate([src, np.zeros((len(cr) - len(src), 2))]) + 0.7 * cr
    add(fx["revswell"], reverse_swell(src, hall_ir, T_CUT - 12.0), 12.0, 1.0)
    add(fx["revswell"], reverse_swell(crash_sound(42, 1.2, 0.5), hall_ir[:S(1.0)], 43.97 - 42.9), 42.9, 0.7)
    chord = sum(pluck(m, 0.8, 0.3) for m in (57, 60, 64, 67, 71))
    add(fx["revswell"], reverse_swell(chord / 5.0, hall_ir, 51.94 - 50.75), 50.75, 0.45)
    for t_end, sd in ((22.0, 43), (34.0, 44)):
        rs = reverse_swell(crash_sound(sd, 1.2, 0.5), hall_ir[:S(1.2)], 1.0)
        add(fx["revswell"], rs, t_end - 1.0 - 0.01, 0.25)
    # digital rain droplets 6.0 -> 13.5 (density falls as the rain slows from 10.0)
    rng = np.random.default_rng(606)
    pent = [81, 84, 86, 88, 91, 93, 96, 98, 100]
    drop_cache = {m: bell(m, length=0.5, decay=0.07, ratios=((1.0, 1.0, 1.0), (3.0, 0.25, 0.4)), att=0.0006)
                  for m in pent}
    for k in range(int(6.0 / 0.0625), int(13.5 / 0.0625)):
        t = k * 0.0625
        p = 0.26 if t < 10.0 else 0.26 * (1.0 - (t - 10.0) / 3.5)
        if rng.random() < p:
            add(fx["drops"], drop_cache[int(rng.choice(pent))], t, rng.uniform(0.35, 1.0), pan=rng.uniform(-0.75, 0.75))
    # filter-reject pings
    for i, (t, m) in enumerate(zip(PING_TIMES, PING_NOTES)):
        add(fx["pings"], bell(m, length=1.3, decay=0.36, att=0.0008), t, 1.0, pan=-0.3 if i % 2 == 0 else 0.3)
    # checklist chimes (tick + bell, rising) and the softer tone at 50.5
    for i, (t, m) in enumerate(zip(CHIME_TIMES, CHIME_NOTES)):
        c = bell(m, length=1.6, decay=0.55, ratios=((1.0, 1.0, 1.0), (2.0, 0.4, 0.45), (3.01, 0.15, 0.18)),
                 att=0.0006, click=0.55, seed=50 + i)
        add(fx["chimes"], c, t, 0.85 + 0.03 * i, pan=-0.2 + 0.08 * i)
    tone = (bell(76, length=3.0, decay=1.0, ratios=((1.0, 1.0, 1.0), (2.0, 0.12, 0.5)), att=0.0025)
            + 0.55 * bell(83, length=3.0, decay=0.9, ratios=((1.0, 1.0, 1.0),), att=0.0025))
    add(fx["tone"], tone / np.max(np.abs(tone)), TONE_TIME, 1.0)
    # 53.0 'HEVI' glint
    add(fx["chimes"], bell(93, length=2.0, decay=0.7, att=0.001), 53.0, 0.55, pan=0.1)
    # held cream-white shimmer at the big hit, a softer bloom on the final A
    add(fx["shimmer"], shimmer_sound([81, 88, 91, 95, 93], 6.0, 61, att=0.015, decay=2.2, grains=55), T_HIT, 1.3)
    sh2 = shimmer_sound([81, 85, 88, 93], 3.8, 62, att=0.7, decay=3.0, grains=45, grain_span=3.6)
    add(fx["shimmer"], sh2, 56.0, 0.75)
    # tension: a faint, trembling high E over the muffled bed (40 -> 42)
    n = S(2.3)
    tt = np.arange(n) / SR
    tens = np.sin(2.0 * np.pi * float(hz(88)) * tt) * (1.0 + 0.5 * np.sin(2.0 * np.pi * 4.0 * tt))
    tens *= smoothstep(tt / 1.5) * smoothstep((2.25 - tt) / 0.3)
    add(fx["tension"], tens, 40.0, 1.0, pan=0.1)
    return fx


def compose():
    log("rendering instruments")
    hall_ir = make_ir(4.0, 3.4, 2.9, 1.5, seed=1, predelay=0.024)
    room_ir = make_ir(1.3, 1.0, 0.85, 0.45, seed=2, predelay=0.008, n_er=16)
    stems = {}
    stems["drone"] = render_drone()
    stems["ticks"] = render_ticks()
    stems.update(render_arps())
    log("  arps done")
    stems["pads"], stems["padlow"] = render_pads()
    log("  pads done")
    stems["stab"] = render_stabs()
    stems["lead"] = render_lead(COUNTER, 71) + render_lead(FINALE_MELODY, 72)
    sub, bass = render_bass()
    kick, hats, snare, slams, hits = render_drums()
    stems["sub"], stems["bass"], stems["kick"] = sub, bass, kick
    stems["hats"], stems["snare"], stems["slams"] = hats, snare, slams
    stems.update(render_fx(hall_ir))
    log("  fx done")
    for k in stems:
        stems[k] = stems[k] * MIX[k]
        stems[k] *= GAP if stems[k].ndim == 1 else GAP[:, None]
    return stems, hits, hall_ir, room_ir


# ----------------------------------------------------------------------------------------- mix
GROUP = ["drone", "arp_boot", "arp", "pads", "padlow", "stab", "lead", "bass", "hats"]
DIRECT = ["ticks", "kick", "sub", "snare", "slams", "impact", "crash", "risers", "revswell", "drops", "pings",
          "chimes", "tone", "shimmer", "tension"]
HALL_MUSIC_SEND = {"drone": 0.25, "arp_boot": 0.35, "arp": 0.30, "pads": 0.32, "padlow": 0.10, "stab": 0.40,
                   "lead": 0.38, "hats": 0.05}
HALL_FX_SEND = {"ticks": 0.10, "snare": 0.22, "slams": 0.20, "impact": 0.18, "crash": 0.30, "risers": 0.25,
                "drops": 0.55, "pings": 0.45, "chimes": 0.45, "tone": 0.50, "shimmer": 0.60, "tension": 0.5}
ROOM_SEND = {"kick": 0.05, "hats": 0.12, "snare": 0.35, "slams": 0.30}
DELAY_SEND = {"arp_boot": 0.32, "arp": 0.30, "lead": 0.12, "pings": 0.30, "chimes": 0.18, "drops": 0.15}


def as_stereo(x):
    return np.stack([x, x], axis=1) if x.ndim == 1 else x


def group_fc(times):
    """Tension / build automation on the music group: everything dives to ~400 Hz at 40.0, opens 42 -> 44."""
    return curve([(0.0, 22000.0), (39.95, 22000.0), (40.0, 9000.0), (40.12, 1200.0), (40.3, 430.0),
                  (41.95, 380.0), (42.0, 380.0), (43.0, 900.0), (43.5, 2600.0), (43.8, 7500.0),
                  (43.93, 20000.0), (44.0, 22000.0), (60.0, 22000.0)], times, True)


def mixdown(stems, hits, hall_ir, room_ir):
    log("mixing: side-chain, delay, reverbs, tension filter")
    duck = duck_env(hits, 1.0)
    stems["sub"] = stems["sub"] * (1.0 - 0.55 * (1.0 - duck))
    stems["bass"] = stems["bass"] * (1.0 - 0.65 * (1.0 - duck))
    stems["pads"] = stems["pads"] * (1.0 - 0.22 * (1.0 - duck))[:, None]
    stems["arp"] = stems["arp"] * (1.0 - 0.10 * (1.0 - duck))[:, None]
    # ping-pong delay (dotted 8th)
    dsend = np.zeros(N)
    for k, g in DELAY_SEND.items():
        s = stems[k]
        dsend += g * (s.mean(axis=1) if s.ndim == 2 else s)
    pp = pingpong_ir(0.375, 0.42, 7, 3800.0)
    delay_out = np.stack([conv_cut(dsend, pp[:, 0]), conv_cut(dsend, pp[:, 1])], axis=1)
    # reverbs
    hm, hf, rm = stereo(), stereo(), stereo()
    for k, g in HALL_MUSIC_SEND.items():
        hm += g * as_stereo(stems[k])
    hm += 0.30 * delay_out
    for k, g in HALL_FX_SEND.items():
        hf += g * as_stereo(stems[k])
    for k, g in ROOM_SEND.items():
        rm += g * as_stereo(stems[k])
    hall_m = reverb(hm, hall_ir)
    hall_f = reverb(hf, hall_ir)
    room = reverb(rm, room_ir)
    log("  reverbs done")
    group = stereo()
    for k in GROUP:
        group += as_stereo(stems[k])
    group += delay_out + 0.9 * hall_m
    group = stft_filter(group, lp_gain(group_fc, order=3, res_db=4.0, res_w=0.35, open_above=19000.0))
    direct = stereo()
    for k in DIRECT:
        direct += as_stereo(stems[k])
    direct += 0.9 * hall_f + 0.8 * room
    mix = group + direct
    buses = {"group": group, "direct": direct, "delay": delay_out, "hall_music": hall_m, "hall_fx": hall_f,
             "room": room}
    return mix, buses


# ----------------------------------------------------------------------------------------- master
def fade_env():
    g = np.ones(N)
    u = (TT - FADE_START) / (FADE_END - FADE_START)
    g *= 1.0 - smoothstep(u)
    g[S(FADE_END):] = 0.0
    return g


def ms_cleanup(x):
    """Side high-pass at 120 Hz (mono-compatible low end), DC / sub-sonic high-pass at 18 Hz, +3 dB air shelf."""
    nf = nextpow2(N)
    f = np.fft.rfftfreq(nf, 1.0 / SR)
    mid = 0.5 * (x[:, 0] + x[:, 1])
    side = 0.5 * (x[:, 0] - x[:, 1])
    dc = hp_mag(np.maximum(f, 1e-6), 18.0, 2) * (1.0 + (undb(3.0) - 1.0) * hp_mag(np.maximum(f, 1e-6), 7000.0, 1) ** 2)
    mid = np.fft.irfft(np.fft.rfft(mid, nf) * dc, nf)[:N]
    side = np.fft.irfft(np.fft.rfft(side, nf) * dc * hp_mag(np.maximum(f, 1e-6), 120.0, 4), nf)[:N]
    return np.stack([mid + side, mid - side], axis=1)


def compressor(x, thr_db=-20.0, ratio=1.7, knee=6.0, att=0.015, rel=0.25, block=64):
    """Gentle RMS bus compressor (block-rate detector, attack/release smoothing)."""
    nb = N // block
    p = (x[:nb * block] ** 2).mean(axis=1).reshape(nb, block).mean(axis=1)
    lev = 10.0 * np.log10(p + 1e-12)
    over = lev - thr_db
    slope = 1.0 - 1.0 / ratio
    gr = np.where(over <= -knee / 2, 0.0, np.where(over >= knee / 2, over * slope,
                                                    slope * (over + knee / 2) ** 2 / (2 * knee)))
    ca = math.exp(-block / (att * SR))
    cr = math.exp(-block / (rel * SR))
    sm = np.empty(nb)
    s = 0.0
    for i in range(nb):
        g = gr[i]
        s = ca * s + (1 - ca) * g if g > s else cr * s + (1 - cr) * g
        sm[i] = s
    centres = (np.arange(nb) + 0.5) * block
    gdb = np.interp(np.arange(N), centres, sm)
    return x * undb(-gdb)[:, None], float(np.max(sm))


def limiter(x, ceiling_db=-1.3, look=0.0015, rel=0.08, block=16):
    """Look-ahead brick-wall limiter (gain never exceeds what each sample needs; smooth release)."""
    ceil = undb(ceiling_db)
    pk = np.max(np.abs(x), axis=1)
    req = np.minimum(1.0, ceil / np.maximum(pk, 1e-12))
    W = S(look)
    rp = np.concatenate([req, np.ones(W)])
    g1 = np.lib.stride_tricks.sliding_window_view(rp, W).min(axis=1)[:N]         # min over [n, n+W)
    c = np.concatenate([[0.0], np.cumsum(np.concatenate([np.ones(W - 1), g1]))])
    g2 = (c[W:] - c[:-W]) / W                                                   # mean of g1[n-W+1 .. n]
    g2 = np.minimum(g2[:N], req)
    nb = N // block
    bmin = g2[:nb * block].reshape(nb, block).min(axis=1)
    cr = math.exp(-block / (rel * SR))
    r = np.empty(nb)
    s = 1.0
    for i in range(nb):
        s = min(bmin[i], 1.0 - (1.0 - s) * cr)
        r[i] = s
    g = np.repeat(r, block)
    y = x * g[:, None]
    return np.clip(y, -ceil, ceil), float(np.min(g))


K_B1 = [1.53512485958697, -2.69169618940638, 1.19839281085285]
K_A1 = [1.0, -1.69065929318241, 0.73248077421585]
K_B2 = [1.0, -2.0, 1.0]
K_A2 = [1.0, -1.99004745483398, 0.99007225036621]


def k_weight(x):
    n = len(x)
    nf = nextpow2(n)
    w = 2.0 * np.pi * np.fft.rfftfreq(nf, 1.0 / SR) / SR
    z1 = np.exp(-1j * w)
    z2 = z1 * z1
    H = ((K_B1[0] + K_B1[1] * z1 + K_B1[2] * z2) / (K_A1[0] + K_A1[1] * z1 + K_A1[2] * z2)
         * (K_B2[0] + K_B2[1] * z1 + K_B2[2] * z2) / (K_A2[0] + K_A2[1] * z1 + K_A2[2] * z2))
    return np.fft.irfft(np.fft.rfft(x, nf, axis=0) * np.abs(H)[:, None], nf, axis=0)[:n]


def block_loudness(xk, block=0.4, hop=0.1):
    p = (xk ** 2).sum(axis=1)
    c = np.concatenate([[0.0], np.cumsum(p)])
    bl, hp_ = S(block), S(hop)
    starts = np.arange(0, len(p) - bl + 1, hp_)
    ms = (c[starts + bl] - c[starts]) / bl
    return starts, ms


def integrated_lufs(x):
    _, ms = block_loudness(k_weight(x))
    L = -0.691 + 10.0 * np.log10(ms + 1e-20)
    g1 = ms[L > -70.0]
    lr = -0.691 + 10.0 * np.log10(g1.mean()) - 10.0
    g2 = ms[(L > -70.0) & (L > lr)]
    return -0.691 + 10.0 * np.log10(g2.mean())


def master(mix):
    log("mastering")
    x = ms_cleanup(mix)
    x *= (GAP * BREATH)[:, None]
    x *= fade_env()[:, None]
    g_rms = math.sqrt(np.mean(x[S(14.0):S(40.0)] ** 2))
    x *= undb(-17.0) / g_rms                                   # groove at -17 dBFS RMS into the compressor
    x, max_gr = compressor(x)
    target = -14.0
    gain = 0.0
    for _ in range(4):
        y, min_g = limiter(x * undb(gain))
        lufs = integrated_lufs(y)
        gain += target - lufs
        if abs(target - lufs) < 0.05:
            break
    y, min_g = limiter(x * undb(gain))
    y *= (GAP * BREATH)[:, None]
    y[S(FADE_END):] = 0.0
    live = (GAP > 0) & (TT < FADE_END)
    y[live] -= y[live].mean(axis=0)                            # residual DC trim (sub-LSB)
    return y, {"comp_max_gr_db": max_gr, "limiter_min_gain_db": db(min_g), "makeup_db": gain}


def to_pcm16(y):
    rng = np.random.default_rng(999)
    d = (rng.random((N, 2)) - rng.random((N, 2))) / 32768.0          # TPDF dither, +-1 LSB
    mask = (GAP > 0) & (TT < FADE_END)
    d *= mask[:, None]
    q = np.round((y + d) * 32767.0)
    return np.clip(q, -32768, 32767).astype("<i2")


def write_wav(path, pcm):
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.ascontiguousarray(pcm).tobytes())


def read_wav(path):
    with wave.open(path, "rb") as w:
        info = (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes())
        raw = w.readframes(w.getnframes())
    pcm = np.frombuffer(raw, dtype="<i2").reshape(-1, info[0])
    return pcm, info


# ----------------------------------------------------------------------------------------- analysis
def hilbert_env(x, smooth=24):
    """Instantaneous amplitude (analytic signal via FFT), lightly smoothed (0.5 ms)."""
    n = len(x)
    nf = nextpow2(n)
    X = np.fft.fft(x, nf)
    h = np.zeros(nf)
    h[0] = 1.0
    h[1:nf // 2] = 2.0
    h[nf // 2] = 1.0
    return box_smooth(np.abs(np.fft.ifft(X * h)[:n]), smooth)


def detect_onset(x, t0, band=None, search=(-0.02, 0.025), frac=0.25):
    """Onset near t0 = start of the attack: first time the (optionally band-limited, zero-phase) Hilbert
    envelope has risen `frac` of the way (linear) from the pre-onset level (median of the 50 ms before) to the
    peak of the next 25 ms, and stays above that for 1 ms. Returns (time or None, rise in dB); a rise under
    6 dB is reported as masked (None)."""
    a = max(0, S(t0 - 0.15))
    b = min(len(x), S(t0 + 0.15))
    seg = x[a:b].copy()
    if band is not None:
        lo, hi = band
        seg = fft_filter(seg, lambda f: hp_mag(np.maximum(f, 1e-3), lo, 4) * lp_mag(f, hi, 4), pad=8192)
    e = hilbert_env(seg)
    tc = (np.arange(len(e)) + a) / SR
    pre_m = (tc >= t0 - 0.05) & (tc <= t0 - 0.005)
    pre = float(np.median(e[pre_m])) if np.any(pre_m) else 0.0
    pk_m = (tc >= t0 - 0.002) & (tc <= t0 + 0.025)
    peak = float(np.max(e[pk_m]))
    rise = db(peak / max(pre, 1e-9))
    if rise < 6.0:
        return None, rise
    thr = pre + frac * (peak - pre)
    hold = S(0.001)
    above = e >= thr
    held = np.lib.stride_tricks.sliding_window_view(np.concatenate([above, np.zeros(hold, bool)]), hold).all(axis=1)
    win = np.where((tc >= t0 + search[0]) & (tc <= t0 + search[1]) & held[:len(tc)])[0]
    if len(win) == 0:
        return None, rise
    return float(tc[win[0]]), rise


def mix_lag(stem, mix, t0, pre=0.005, post=0.08, maxlag=0.010):
    """Where does this element sit inside the final mix? Normalised cross-correlation of the isolated
    element against the final WAV around t0, lags +-10 ms. Returns (lag seconds, correlation)."""
    a, b = S(t0 - pre), S(t0 + post)
    L = S(maxlag)
    s = stem[a:b]
    m = mix[max(0, a - L):b + L]
    if a - L < 0:
        m = np.concatenate([np.zeros(L - a), m])
    W = np.lib.stride_tricks.sliding_window_view(m, len(s))
    c = W @ s / (np.linalg.norm(s) * np.sqrt(np.sum(W * W, axis=1)) + 1e-20)
    k = int(np.argmax(c))
    return (k - L) / SR, float(c[k])


BROAD_BAND = (150.0, 16000.0)
SECTIONS = [("1 BOOT", 0.0, 6.0), ("2 RAIN>REVEAL", 6.0, 14.0), ("3 HERO (drop)", 14.0, 22.0),
            ("4 READS", 22.0, 34.0), ("5a TRADE groove", 34.0, 40.0), ("5b TENSION", 40.0, 42.0),
            ("5c BUILD", 42.0, 44.0), ("5d BIG HIT", 44.0, 46.0), ("6 DISCIPLINE", 46.0, 52.0),
            ("7 FINALE", 52.0, 60.0)]


def analyse(pcm, info, stems, mstats, runtime):
    x = pcm.astype(np.float64) / 32768.0
    mono = x.mean(axis=1)
    lines = []
    P = lines.append
    P("HEVI - 'She Waits' - score report")
    P("=" * 78)
    P(f"file            {os.path.relpath(WAV_PATH, os.path.dirname(HERE))}")
    P(f"format          {info[2]} Hz, {8 * info[1]}-bit PCM, {info[0]} channels")
    P(f"frames          {info[3]:,}  (expected 2,880,000)  -> duration {info[3] / info[2]:.6f} s")
    peak = np.max(np.abs(x))
    nf = nextpow2(N)
    tp = 0.0
    for c in range(2):
        up = np.fft.irfft(np.fft.rfft(x[:, c], nf), nf * 4) * 4.0
        tp = max(tp, float(np.max(np.abs(up[:N * 4]))))
        del up
    clip_n = int(np.sum(np.abs(pcm.astype(np.int32)) >= 32767))
    P(f"sample peak     {db(peak):+.2f} dBFS     true peak (4x) {db(tp):+.2f} dBTP     clipped samples {clip_n}")
    P(f"DC offset       L {np.mean(x[:, 0]):+.2e}   R {np.mean(x[:, 1]):+.2e}")
    lufs = integrated_lufs(x)
    xk = k_weight(x)
    starts, ms = block_loudness(xk, 3.0, 0.1)
    st = -0.691 + 10.0 * np.log10(ms + 1e-20)
    P(f"loudness        integrated {lufs:.1f} LUFS (BS.1770 K-weighting, gated)   max short-term {st.max():.1f} LUFS"
      f" at {(starts[np.argmax(st)] / SR) + 1.5:.1f} s")
    rms_groove = math.sqrt(np.mean(x[S(14.0):S(40.0)] ** 2))
    P(f"groove RMS      {db(rms_groove):.1f} dBFS (14-40 s, both channels)")
    corr = float(np.sum(x[:, 0] * x[:, 1]) / math.sqrt(np.sum(x[:, 0] ** 2) * np.sum(x[:, 1] ** 2)))
    side = 0.5 * (x[:, 0] - x[:, 1])
    Sx = np.abs(np.fft.rfft(side, nf)) ** 2
    Mx = np.abs(np.fft.rfft(mono, nf)) ** 2
    fr = np.fft.rfftfreq(nf, 1.0 / SR)
    lowmask = (fr > 20) & (fr < 120)
    P(f"stereo          L/R correlation {corr:+.2f}; side/mid energy below 120 Hz "
      f"{10 * np.log10(Sx[lowmask].sum() / Mx[lowmask].sum()):.1f} dB (mono-compatible sub)")
    P(f"mono fold-down  mono RMS vs stereo RMS (groove): "
      f"{db(math.sqrt(np.mean(mono[S(14):S(40)] ** 2)) / rms_groove):+.1f} dB")
    sil = x[S(FADE_END):]
    P(f"tail            59.8-60.0 s max |sample| = {int(np.max(np.abs(pcm[S(FADE_END):])))} LSB "
      f"(digital silence: {'yes' if np.max(np.abs(sil)) == 0 else 'no'});  "
      f"pre-drop gap {T_CUT:.3f}-14.000 max = {int(np.max(np.abs(pcm[S(T_CUT):S(T_DROP)])))} LSB")
    P(f"start           sample 0 = {int(pcm[0, 0])}/{int(pcm[0, 1])} LSB; drone fades in from silence "
      f"(drone stem at 0.5 s: {db(np.max(np.abs(stems['drone'][:S(0.5)])) + 1e-12):.0f} dBFS pre-master); "
      f"first beat tick at 0.000")
    P(f"master          bus comp max GR {mstats['comp_max_gr_db']:.1f} dB, limiter max GR "
      f"{-mstats['limiter_min_gain_db']:.1f} dB, makeup {mstats['makeup_db']:+.1f} dB")
    P(f"render time     {runtime:.1f} s")
    P("")
    P("Sections (RMS of both channels, approx. loudness = ungated K-weighted mean, peak)")
    P("-" * 78)
    P(f"{'section':<18}{'time':>12}{'RMS dBFS':>11}{'~LUFS':>9}{'peak dBFS':>11}")
    for name, a, b in SECTIONS:
        seg = x[S(a):S(b)]
        segk = xk[S(a):S(b)]
        r = math.sqrt(np.mean(seg ** 2))
        lk = -0.691 + 10.0 * np.log10(np.mean((segk ** 2).sum(axis=1)) + 1e-20)
        P(f"{name:<18}{a:>5.1f}-{b:<5.1f}s{db(r):>11.1f}{lk:>9.1f}{db(np.max(np.abs(seg))):>11.1f}")
    P("")
    P("Groove spectrum balance (14-40 s, energy per band relative to total)")
    P("-" * 78)
    gs = np.abs(np.fft.rfft(mono[S(14):S(40)])) ** 2
    gf = np.fft.rfftfreq(S(40) - S(14), 1.0 / SR)
    tot = gs.sum()
    bands = [(20, 60, "sub"), (60, 250, "low"), (250, 2000, "mid"), (2000, 6000, "presence"),
             (6000, 12000, "brilliance"), (12000, 24000, "air")]
    P("  " + "   ".join(f"{nm} {lo}-{hi}: {10 * np.log10(gs[(gf >= lo) & (gf < hi)].sum() / tot):.1f} dB"
                         for lo, hi, nm in bands[:3]))
    P("  " + "   ".join(f"{nm} {lo}-{hi}: {10 * np.log10(gs[(gf >= lo) & (gf < hi)].sum() / tot + 1e-20):.1f} dB"
                         for lo, hi, nm in bands[3:]))
    P("")
    P("Onsets / sync points")
    P("-" * 78)
    P("element = start-of-attack onset of the isolated element (Hilbert envelope, 25 % rise) as placed in the mix;")
    P("mix     = the same detector run blind on the final WAV (band where the element speaks: ticks 2.5-12 kHz,")
    P("          slams 1.5-6.5 kHz, pings/chimes/tone fundamental +-4 %, heartbeat 20-200 Hz, snare 1-9 kHz,")
    P("          others 150 Hz-16 kHz; '-' = masked by other parts at that instant);")
    P("lag     = offset of the element inside the final WAV by cross-correlation (0.00 = sample aligned), corr.")
    P(f"{'cue':<40}{'target':>8}{'element':>10}{'err ms':>7}{'mix':>10}{'err ms':>7}{'lag ms':>8}{'corr':>6}")
    nb = lambda m, r=1.0, w=0.04: (float(hz(m)) * r * (1 - w), float(hz(m)) * r * (1 + w))
    cues = [("boot tick (first beat)", 0.0, None, "ticks")]
    cues += [(f"boot tick (beat {k + 1})", k * BEAT, (2500, 12000), "ticks") for k in range(1, 12)]
    cues += [("boot arpeggio enters", 2.0, None, "arp_boot"),
             ("THE DROP: kick", 14.0, None, "kick"),
             ("THE DROP: impact", 14.0, None, "impact"),
             ("THE DROP: crash", 14.0, None, "crash"),
             ("THE DROP: Fmaj7 stab", 14.0, None, "stab"),
             ("HEVI letter slam 2", 14.5, (1500, 6500), "slams"),
             ("HEVI letter slam 3", 15.0, (1500, 6500), "slams"),
             ("HEVI letter slam 4", 15.5, (1500, 6500), "slams")]
    cues += [(f"filter-reject ping {i + 1}", t, nb(m), "pings")
             for i, (t, m) in enumerate(zip(PING_TIMES, PING_NOTES))]
    cues += [("tension: heartbeat 1", 40.0, (20, 200), "kick"),
             ("tension: heartbeat 2", 41.0, (20, 200), "kick"),
             ("build: snare roll start", 42.0, (1000, 9000), "snare"),
             ("BIG HIT: impact", 44.0, None, "impact"),
             ("BIG HIT: crash", 44.0, None, "crash"),
             ("BIG HIT: Am9 stab", 44.0, None, "stab"),
             ("BIG HIT: shimmer", 44.0, None, "shimmer")]
    cues += [(f"checklist chime {i + 1}", t, nb(m), "chimes") for i, (t, m) in enumerate(zip(CHIME_TIMES, CHIME_NOTES))]
    cues += [("softer tone (50.5)", 50.5, nb(76), "tone"),
             ("FINALE: soft impact", 52.0, None, "impact"),
             ("FINALE: chord strike", 52.0, None, "arp"),
             ("final A major resolution (arp)", 56.0, None, "arp")]
    results = []
    fm = lambda v: f"{v:10.4f}" if v is not None else "         -"
    for name, t0, band, stem in cues:
        tm, _ = detect_onset(mono, t0, band if band is not None else BROAD_BAND)
        sm = stems[stem].mean(axis=1) if stems[stem].ndim == 2 else stems[stem]
        ts, _ = detect_onset(sm, t0)
        lag, corr = mix_lag(sm, mono, t0)
        results.append((name, t0, tm, ts, lag))
        fe = lambda v: f"{(v - t0) * 1000:+7.1f}" if v is not None else "      -"
        P(f"{name:<40}{t0:>8.3f}{fm(ts)}{fe(ts)}{fm(tm)}{fe(tm)}{lag * 1000:+8.2f}{corr:6.2f}")
    el = [abs(r[3] - r[1]) for r in results if r[3] is not None]
    mx = [abs(r[2] - r[1]) for r in results if r[2] is not None]
    lg = [abs(r[4]) for r in results]
    P(f"summary: {len(results)} cues; element onsets worst {1000 * max(el):.2f} ms; blind mix onsets "
      f"{len(mx)}/{len(results)} detected, worst {1000 * max(mx):.2f} ms; worst mix lag {1000 * max(lg):.2f} ms")
    kick = stems["kick"]
    kt = GROOVE_KICKS + [b[0] for b in BUILD_KICKS]
    stem_err = [abs(detect_onset(kick, t)[0] - t) for t in kt]
    mix_err = []
    for t in kt:
        tm, _ = detect_onset(mono, t, BROAD_BAND)
        if tm is not None:
            mix_err.append((abs(tm - t), t))
    lags = [abs(mix_lag(kick, mono, t)[0]) for t in kt]
    w = max(mix_err)
    P(f"kick grid: {len(kt)} kicks on the beat grid (14.0-39.5, 42.0-45.5): element worst {1000 * max(stem_err):.2f} ms;"
      f" blind mix {len(mix_err)}/{len(kt)} detected, worst {1000 * w[0]:.2f} ms ({w[1]:.1f} s),"
      f" mean {1000 * np.mean([m[0] for m in mix_err]):.2f} ms; worst mix lag {1000 * max(lags):.2f} ms")
    P("")
    P("Arrangement / harmony map")
    P("-" * 78)
    for a, b, c in PROG:
        P(f"  {a:5.1f}-{b:5.1f}s  {c}")
    return lines, results, xk


def render_png(x, xk, path, results):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False
    W, H = 1800, 1080
    wav_h, spec_h, loud_h = 300, 520, 160
    top = 40
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (4, 8, 10)
    # waveform (L above, R below)
    cols = np.linspace(0, N, W + 1).astype(int)
    for ch, (y0, hh) in enumerate(((top, wav_h // 2), (top + wav_h // 2, wav_h // 2))):
        sig = x[:, ch]
        mid = y0 + hh // 2
        for i in range(W):
            seg = sig[cols[i]:cols[i + 1]]
            lo, hi = seg.min(), seg.max()
            r = math.sqrt(np.mean(seg ** 2))
            a, b = int(mid - hi * hh / 2), int(mid - lo * hh / 2)
            img[max(y0, a):min(y0 + hh, b + 1), i] = (14, 94, 85)
            a, b = int(mid - r * hh / 2), int(mid + r * hh / 2)
            img[max(y0, a):min(y0 + hh, b + 1), i] = (47, 211, 176)
    # spectrogram (log frequency 30 Hz - 20 kHz)
    mono = x.mean(axis=1)
    nfft, hop = 4096, 1024
    win = np.hanning(nfft)
    xp = np.concatenate([np.zeros(nfft // 2), mono, np.zeros(nfft)])
    frames = np.lib.stride_tricks.sliding_window_view(xp, nfft)[::hop] * win
    spec = 20 * np.log10(np.abs(np.fft.rfft(frames, axis=1)) + 1e-9)
    fbin = np.fft.rfftfreq(nfft, 1.0 / SR)
    rows_f = np.exp(np.linspace(math.log(20000.0), math.log(30.0), spec_h))
    ridx = np.clip(np.searchsorted(fbin, rows_f), 1, len(fbin) - 1)
    fidx = np.clip((np.arange(W) + 0.5) / W * N / hop, 0, len(spec) - 1).astype(int)
    S2 = spec[fidx][:, ridx].T
    vmax = np.percentile(S2, 99.7)
    v = np.clip((S2 - (vmax - 80.0)) / 80.0, 0, 1)
    stops = np.array([[4, 8, 10], [14, 94, 85], [47, 211, 176], [125, 255, 217], [255, 246, 224]], float)
    pos = np.linspace(0, 1, len(stops))
    rgb = np.stack([np.interp(v, pos, stops[:, c]) for c in range(3)], axis=-1).astype(np.uint8)
    y0s = top + wav_h + 10
    img[y0s:y0s + spec_h] = rgb
    # short-term loudness (400 ms momentary)
    starts, ms = block_loudness(xk, 0.4, 0.05)
    lm = -0.691 + 10 * np.log10(ms + 1e-20)
    y0l = y0s + spec_h + 10
    pil = Image.fromarray(img)
    d = ImageDraw.Draw(pil)
    try:
        font = ImageFont.load_default(size=14)
    except Exception:
        font = ImageFont.load_default()
    tx = lambda t: int(t / 60.0 * W)
    for lv in (-10, -20, -30, -40):
        yy = y0l + int((-lv - 5) / 40.0 * loud_h)
        d.line([(0, yy), (W, yy)], fill=(30, 50, 48))
        d.text((W - 60, yy - 15), f"{lv} LU", fill=(62, 110, 104), font=font)
    pts = [(tx((s0 + S(0.2)) / SR), y0l + int(np.clip((-l - 5) / 40.0, 0, 1) * loud_h)) for s0, l in zip(starts, lm)]
    d.line(pts, fill=(125, 255, 217), width=2)
    for name, a, b in SECTIONS:
        d.line([(tx(a), top), (tx(a), H - 20)], fill=(62, 110, 104))
        d.text((tx(a) + 4, 8), name, fill=(255, 246, 224), font=font)
    for name, t0, tm, ts, lag in results:
        col = (255, 79, 109) if t0 in (14.0, 44.0, 52.0) else (110, 168, 255)
        d.line([(tx(t0), y0s), (tx(t0), y0s + 18)], fill=col, width=2)
    for t0 in (14.0, 44.0, 52.0):
        d.line([(tx(t0), top), (tx(t0), y0l + loud_h)], fill=(255, 79, 109), width=1)
    for s in range(0, 61, 2):
        d.text((tx(s) + 2, H - 18), f"{s}", fill=(62, 110, 104), font=font)
    d.text((6, top + 2), "L", fill=(125, 255, 217), font=font)
    d.text((6, top + wav_h // 2 + 2), "R", fill=(125, 255, 217), font=font)
    d.text((6, y0s + 4), "spectrogram 30 Hz - 20 kHz (log)", fill=(255, 246, 224), font=font)
    d.text((6, y0l + 2), "momentary loudness (LUFS)", fill=(255, 246, 224), font=font)
    pil.save(path)
    return True


# ----------------------------------------------------------------------------------------- main
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    stems, hits, hall_ir, room_ir = compose()
    mix, buses = mixdown(stems, hits, hall_ir, room_ir)
    y, mstats = master(mix)
    pcm = to_pcm16(y)
    write_wav(WAV_PATH, pcm)
    log(f"wrote {WAV_PATH}")
    runtime = time.time() - T0_WALL
    pcm2, info = read_wav(WAV_PATH)
    assert info == (2, 2, SR, N), info
    lines, results, xk = analyse(pcm2, info, stems, mstats, runtime)
    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    log(f"wrote {REPORT_PATH}")
    if render_png(pcm2.astype(np.float64) / 32768.0, xk, PNG_PATH, results):
        log(f"wrote {PNG_PATH}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
