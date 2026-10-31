"""
Procedural ambient music engine.

Generates long-form, never-repeating ambient/meditation audio entirely from code:
pads, drones, singing bowls, sparse bell melodies, binaural beats and synthesized
nature beds (rain, ocean, wind, fire, birds, brown noise). Everything is driven
by a theme JSON so each video gets its own key, mode, timbre and texture.

Audio is rendered in chunks and streamed to ffmpeg, so a 3-hour render uses
roughly the same memory as a 1-minute one.

Usage:
    python -m engine.music --theme themes/example.json --out out/audio.m4a
    python -m engine.music --theme themes/example.json --minutes 2 --out preview.m4a
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
import sys
import time

import numpy as np
from scipy import signal

SR = 44100
CHUNK_SECONDS = 20
TWO_PI = 2 * np.pi

NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
        "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}

MODES = {
    "ionian": [0, 2, 4, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "pentatonic_minor": [0, 3, 5, 7, 10],
}

DEFAULT_THEME = {
    "key": "D",
    "mode": "dorian",
    "duration_min": 60,
    "seed": None,                 # None -> derived from title
    "title": "untitled",
    "chord_seconds": [22, 40],    # min/max length of each chord
    "brightness": 0.4,            # 0 = dark/warm, 1 = bright/airy
    "lowpass_hz": 9000,           # master tone; lower = softer for sleep
    "reverb": {"seconds": 7.0, "mix": 0.45},
    "layers": {                   # 0..1 levels; 0 disables a layer
        "pad": 0.8, "drone": 0.45, "bowls": 0.0, "bells": 0.25,
        "brown": 0.0, "rain": 0.0, "ocean": 0.0, "wind": 0.0,
        "fire": 0.0, "birds": 0.0, "binaural": 0.0,
    },
    "density": {"bowls_every": 35, "bells_every": 9, "birds_every": 18},
    "binaural": {"carrier_hz": 180, "beat_hz": 4.0},
    "evolve_minutes": [4, 12],    # period range of slow mix evolution
}


# ---------------------------------------------------------------- helpers

def midi_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def pan_gains(p: float):
    """Equal-power pan, p in [0,1] (0 = left)."""
    p = min(1.0, max(0.0, p))
    return math.cos(p * math.pi / 2), math.sin(p * math.pi / 2)


def raised_cos_env(t, dur, fade_in, fade_out):
    env = np.ones_like(t)
    if fade_in > 0:
        m = t < fade_in
        env[m] = 0.5 - 0.5 * np.cos(np.pi * t[m] / fade_in)
    if fade_out > 0:
        m = t > dur - fade_out
        env[m] *= 0.5 - 0.5 * np.cos(np.pi * (dur - t[m]) / fade_out)
    return env


class StatefulSOS:
    """IIR filter that keeps its state across chunks (stereo, axis 0)."""

    def __init__(self, sos):
        self.sos = sos
        self.zi = np.zeros((sos.shape[0], 2, 2))

    def __call__(self, x):
        y, self.zi = signal.sosfilt(self.sos, x, axis=0, zi=self.zi)
        return y


def bp(lo, hi, order=2):
    return StatefulSOS(signal.butter(order, [lo, hi], btype="band", fs=SR, output="sos"))


def lp(fc, order=2):
    return StatefulSOS(signal.butter(order, fc, btype="low", fs=SR, output="sos"))


def hp(fc, order=2):
    return StatefulSOS(signal.butter(order, fc, btype="high", fs=SR, output="sos"))


# ---------------------------------------------------------------- harmony

class Harmony:
    """Slow random-walk chord progression inside a key/mode."""

    # Preferred moves between scale degrees (ambient-friendly, avoids tension).
    MOVES = {0: [3, 5, 4, 1], 1: [4, 0, 5], 2: [5, 3, 0], 3: [0, 5, 1, 4],
             4: [0, 5, 3], 5: [3, 0, 1, 2], 6: [0, 5]}

    def __init__(self, theme, rng, total_s):
        self.scale = MODES[theme["mode"]]
        self.root = 48 + NOTE[theme["key"]]  # around C3..B3
        lo, hi = theme["chord_seconds"]
        self.chords = []  # (start, dur, degree)
        t, deg = 0.0, 0
        n = len(self.scale)
        while t < total_s:
            d = float(rng.uniform(lo, hi))
            self.chords.append((t, d, deg))
            t += d
            opts = [m % n for m in self.MOVES.get(deg % 7, [0])]
            deg = int(rng.choice(opts))

    def note(self, idx):
        o, i = divmod(idx, len(self.scale))
        return self.root + 12 * o + self.scale[i]

    def voicing(self, deg, rng):
        bass = self.note(deg) - 12
        uppers = [self.note(deg + k) for k in (0, 2, 4)]
        color = self.note(deg + int(rng.choice([1, 6]))) + 12  # add9 or 7th, up an octave
        notes = [bass] + [u + 12 * int(rng.integers(0, 2)) for u in uppers] + [color]
        return sorted(set(notes))

    def chord_tones_at(self, t):
        for s, d, deg in self.chords:
            if s <= t < s + d:
                return [self.note(deg + k) for k in (0, 2, 4)]
        return [self.note(0)]


# ---------------------------------------------------------------- events

class Event:
    """Something with a start and a duration that can render any slice of itself."""
    start: float
    dur: float

    def render_into(self, buf, i0):
        n = buf.shape[0]
        s_abs = int(round(self.start * SR))
        e_abs = int(round((self.start + self.dur) * SR))
        s = max(0, s_abs - i0)
        e = min(n, e_abs - i0)
        if e <= s:
            return
        t = (np.arange(s, e) + (i0 - s_abs)) / SR  # local time since event start
        buf[s:e] += self.synth(t)

    def synth(self, t):
        raise NotImplementedError


class PadChord(Event):
    def __init__(self, start, dur, fade, notes, brightness, lowpass, rng, gain):
        self.start, self.dur, self.fade, self.gain = start, dur, fade, gain
        p = 3.0 - 2.0 * brightness           # harmonic roll-off exponent
        self.voices = []
        for m in notes:
            f0 = midi_hz(m)
            for d in (-1, 0, 1):
                f = f0 * 2 ** (d * rng.uniform(4, 9) / 1200)
                gl, gr = pan_gains(0.5 + d * 0.33 + rng.uniform(-0.08, 0.08))
                harms = [(h, 1.0 / h ** p) for h in range(1, 7) if f * h < min(lowpass, 6000)]
                self.voices.append(dict(
                    f=f, gl=gl, gr=gr, harms=harms,
                    ph=rng.uniform(0, TWO_PI, size=len(harms)),
                    vr=rng.uniform(0.08, 0.25), vd=rng.uniform(0.2, 0.6),
                    vp=rng.uniform(0, TWO_PI),
                    sweep=rng.uniform(0.02, 0.07), sp=rng.uniform(0, TWO_PI),
                    lvl=1.0 if m > 45 else 0.7,
                ))
        self.norm = 1.0 / math.sqrt(len(self.voices))

    def synth(self, t):
        env = raised_cos_env(t, self.dur, self.fade, self.fade) * self.gain * self.norm
        L = np.zeros_like(t)
        R = np.zeros_like(t)
        for v in self.voices:
            base = TWO_PI * v["f"] * t + v["vd"] * np.sin(TWO_PI * v["vr"] * t + v["vp"])
            sweep = 0.5 + 0.5 * np.sin(TWO_PI * v["sweep"] * t + v["sp"])  # slow "filter" motion
            s = np.zeros_like(t)
            for (h, w), ph in zip(v["harms"], v["ph"]):
                amp = w if h == 1 else w * (0.4 + 0.6 * sweep)
                s += amp * np.sin(h * base + ph)
            s *= v["lvl"]
            L += v["gl"] * s
            R += v["gr"] * s
        return np.stack([L * env, R * env], axis=1)


class Drone(Event):
    def __init__(self, dur, root_midi, rng, gain):
        self.start, self.dur, self.gain = 0.0, dur, gain
        base = root_midi - 12
        while midi_hz(base) > 80:
            base -= 12
        self.parts = []
        for m, a in ((base, 1.0), (base + 7, 0.45), (base + 12, 0.35), (base + 19, 0.12)):
            f = midi_hz(m)
            for det in (-0.12, 0.12):  # slow beating pair
                self.parts.append((f + det, a * 0.5, rng.uniform(0, TWO_PI),
                                   rng.uniform(1 / 60, 1 / 20), rng.uniform(0, TWO_PI),
                                   0.5 + (0.25 if det > 0 else -0.25)))

    def synth(self, t):
        L = np.zeros_like(t)
        R = np.zeros_like(t)
        for f, a, ph, lr, lp_, pan in self.parts:
            swell = 0.65 + 0.35 * np.sin(TWO_PI * lr * t + lp_)
            s = a * swell * np.sin(TWO_PI * f * t + ph)
            gl, gr = pan_gains(pan)
            L += gl * s
            R += gr * s
        env = raised_cos_env(t, self.dur, 12, 15) * self.gain
        return np.stack([L * env, R * env], axis=1)


class Bowl(Event):
    RATIOS = (1.0, 2.71, 5.15, 8.35)
    AMPS = (1.0, 0.45, 0.22, 0.10)
    DECAYS = (14.0, 8.0, 4.5, 2.5)

    def __init__(self, start, f, rng, gain):
        self.start, self.gain = start, gain
        k = rng.uniform(0.8, 1.3)
        self.decays = [d * k for d in self.DECAYS]
        self.dur = self.decays[0] * 4.5
        self.f = f
        self.beats = [rng.uniform(0.4, 1.6) * (i + 1) * 0.5 for i in range(4)]
        self.ph = rng.uniform(0, TWO_PI, size=8)
        self.gl, self.gr = pan_gains(rng.uniform(0.25, 0.75))

    def synth(self, t):
        s = np.zeros_like(t)
        for i, (r, a, d) in enumerate(zip(self.RATIOS, self.AMPS, self.decays)):
            fr = self.f * r
            if fr > 9000:
                continue
            e = a * np.exp(-t / d)
            s += e * 0.5 * (np.sin(TWO_PI * fr * t + self.ph[2 * i]) +
                            np.sin(TWO_PI * (fr + self.beats[i]) * t + self.ph[2 * i + 1]))
        s *= (1 - np.exp(-t / 0.006)) * self.gain
        tail = raised_cos_env(t, self.dur, 0, 3.0)
        s *= tail
        return np.stack([s * self.gl, s * self.gr], axis=1)


class BellNote(Event):
    PARTS = ((1.0, 1.0, 1.0), (2.0, 0.28, 0.5), (3.0, 0.10, 0.33), (4.2, 0.04, 0.2))

    def __init__(self, start, f, rng, gain):
        self.start, self.f, self.gain = start, f, gain
        self.decay = rng.uniform(2.0, 4.5)
        self.dur = self.decay * 5
        self.ph = rng.uniform(0, TWO_PI, size=4)
        self.gl, self.gr = pan_gains(rng.uniform(0.15, 0.85))
        self.attack = rng.uniform(0.01, 0.05)

    def synth(self, t):
        s = np.zeros_like(t)
        for (r, a, dk), ph in zip(self.PARTS, self.ph):
            s += a * np.exp(-t / (self.decay * dk)) * np.sin(TWO_PI * self.f * r * t + ph)
        s *= (1 - np.exp(-t / self.attack)) * self.gain
        return np.stack([s * self.gl, s * self.gr], axis=1)


class BirdCall(Event):
    def __init__(self, start, rng, gain):
        self.start, self.gain = start, gain
        n = int(rng.integers(2, 7))
        self.chirps = []
        t = 0.0
        f0 = rng.uniform(2200, 4800)
        for _ in range(n):
            d = rng.uniform(0.05, 0.16)
            self.chirps.append((t, d, f0 * rng.uniform(0.9, 1.1), rng.uniform(-0.5, 0.6)))
            t += d + rng.uniform(0.04, 0.25)
        self.dur = t + 0.3
        self.gl, self.gr = pan_gains(rng.uniform(0.1, 0.9))

    def synth(self, t):
        s = np.zeros_like(t)
        for c0, d, f, sweep in self.chirps:
            m = (t >= c0) & (t < c0 + d)
            if not m.any():
                continue
            u = (t[m] - c0) / d
            inst = f * (1 + sweep * u)                       # linear sweep
            phase = TWO_PI * np.cumsum(inst) / SR
            s[m] += np.sin(np.pi * u) ** 2 * np.sin(phase)
        s *= self.gain
        return np.stack([s * self.gl, s * self.gr], axis=1)


# ---------------------------------------------------------------- continuous beds

class Bed:
    """Continuous stateful layer rendered chunk by chunk."""

    def __init__(self, rng):
        self.rng = rng
        self.norm = 1.0

    def calibrate(self, target_rms=0.1):
        probe = copy.deepcopy(self)
        x = probe.process(np.arange(SR * 6) / SR)
        rms = float(np.sqrt(np.mean(x[SR:] ** 2))) or 1.0
        self.norm = target_rms / rms
        return self

    def __call__(self, t):
        return self.process(t) * self.norm


class BrownNoise(Bed):
    def __init__(self, rng):
        super().__init__(rng)
        self.leak = StatefulSOS(signal.tf2sos([1.0], [1.0, -0.995]))
        self.hp = hp(25)

    def process(self, t):
        w = self.rng.standard_normal((len(t), 2))
        return self.hp(self.leak(w))


class Rain(Bed):
    def __init__(self, rng):
        super().__init__(rng)
        self.hiss = bp(900, 7000)
        self.flut = lp(12, order=1)
        self.drop = bp(2200, 5200)
        self.rumble = lp(250)
        self.rate = rng.uniform(25, 60)

    def process(self, t):
        n = len(t)
        r = self.rng
        hiss = self.hiss(r.standard_normal((n, 2)))
        fl = self.flut(r.standard_normal((n, 2)))
        hiss *= 0.75 + 0.25 * np.tanh(fl * 8)
        imp = (r.random((n, 2)) < self.rate / SR) * r.uniform(0.2, 1.0, (n, 2))
        drops = self.drop(imp) * 6.0
        rum = self.rumble(r.standard_normal((n, 2))) * 0.6
        return hiss + drops + rum


class Ocean(Bed):
    def __init__(self, rng):
        super().__init__(rng)
        self.body = lp(900)
        self.wash = bp(1500, 6000)
        self.p1, self.p2 = rng.uniform(7, 11), rng.uniform(13, 19)
        self.ph = rng.uniform(0, TWO_PI, 2)

    def _wave(self, t):
        w1 = 0.5 + 0.5 * np.sin(TWO_PI * t / self.p1 + self.ph[0])
        w2 = 0.5 + 0.5 * np.sin(TWO_PI * t / self.p2 + self.ph[1])
        return (0.6 * w1 + 0.4 * w2) ** 2.5

    def process(self, t):
        n = len(t)
        wave = self._wave(t)
        wash_env = self._wave(t - 0.7)  # the wash trails the swell
        body = self.body(self.rng.standard_normal((n, 2))) * (0.25 + wave[:, None])
        wash = self.wash(self.rng.standard_normal((n, 2))) * (0.05 + 0.6 * wash_env[:, None])
        return body + wash


class Wind(Bed):
    def __init__(self, rng):
        super().__init__(rng)
        self.bands = [bp(180, 420), bp(500, 1100), bp(1200, 2600)]
        self.rates = rng.uniform(0.03, 0.12, 3)
        self.ph = rng.uniform(0, TWO_PI, 3)

    def process(self, t):
        n = len(t)
        out = np.zeros((n, 2))
        for f, rate, ph, a in zip(self.bands, self.rates, self.ph, (1.0, 0.6, 0.25)):
            g = (0.5 + 0.5 * np.sin(TWO_PI * rate * t + ph)) ** 2
            out += a * f(self.rng.standard_normal((n, 2))) * g[:, None]
        return out


class Fire(Bed):
    def __init__(self, rng):
        super().__init__(rng)
        self.roar = lp(400)
        self.crack = bp(1200, 5500)
        self.ring = StatefulSOS(signal.tf2sos([1.0], [1.0, -0.93]))  # short decay per crackle
        self.burst = lp(3, order=1)

    def process(self, t):
        n = len(t)
        r = self.rng
        roar = self.roar(r.standard_normal((n, 2))) * 0.5
        activity = np.clip(self.burst(r.standard_normal((n, 2))) * 40 + 0.5, 0.05, 2.0)
        imp = (r.random((n, 2)) < 4 * activity / SR) * r.uniform(0.3, 1.0, (n, 2)) ** 2
        crack = np.tanh(self.crack(self.ring(imp)) * 3) * 0.5
        return roar + crack


class Binaural(Bed):
    def __init__(self, rng, carrier, beat):
        super().__init__(rng)
        self.c, self.b = carrier, beat

    def process(self, t):
        L = np.sin(TWO_PI * self.c * t)
        R = np.sin(TWO_PI * (self.c + self.b) * t)
        return np.stack([L, R], axis=1)


# ---------------------------------------------------------------- reverb

class Reverb:
    """Chunked FFT convolution with a synthetic stereo impulse response."""

    def __init__(self, seconds, rng):
        n = int(seconds * SR)
        t = np.arange(n) / SR
        env = np.exp(-6.9 * t / seconds)
        ir = rng.standard_normal((n, 2)) * env[:, None]
        # darker tail: blend in a low-passed copy as the IR decays
        dark = signal.sosfilt(signal.butter(2, 2500, fs=SR, output="sos"), ir, axis=0)
        mix = np.clip(t / seconds * 2, 0, 1)[:, None]
        ir = (1 - mix) * ir + mix * dark * 1.8
        pre = int(0.025 * SR)
        ir = np.concatenate([np.zeros((pre, 2)), ir])
        ir /= np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True))
        self.ir = ir
        self.tail = np.zeros((len(ir) - 1, 2))

    def __call__(self, x):
        n = len(x)
        y = np.stack([signal.fftconvolve(x[:, c], self.ir[:, c]) for c in range(2)], axis=1)
        y[: len(self.tail)] += self.tail[: len(y)]
        self.tail = y[n:].copy()  # already includes any un-consumed older tail
        return y[:n]


# ---------------------------------------------------------------- engine

class AmbientEngine:
    MUSIC = ("pad", "drone", "bowls", "bells")
    BEDS = ("brown", "rain", "ocean", "wind", "fire", "binaural")

    def __init__(self, theme: dict, total_seconds: float):
        th = deep_merge(DEFAULT_THEME, theme)
        if th["mode"] not in MODES:
            raise ValueError(f"unknown mode {th['mode']}, choose from {list(MODES)}")
        if th["key"] not in NOTE:
            raise ValueError(f"unknown key {th['key']}")
        seed = th["seed"]
        if seed is None:
            seed = int(hashlib.sha256(th["title"].encode()).hexdigest()[:8], 16)
        self.th, self.total = th, total_seconds
        rng = np.random.default_rng(seed)
        self.rng = rng
        lv = th["layers"]
        self.harmony = Harmony(th, rng, total_seconds)

        # musical events
        ev = []
        if lv.get("pad", 0) > 0:
            for s, d, deg in self.harmony.chords:
                fade = min(12.0, d * 0.45)
                ev.append(PadChord(max(0.0, s - fade / 2), d + fade, fade,
                                   self.harmony.voicing(deg, rng), th["brightness"],
                                   th["lowpass_hz"], rng, 0.22 * lv["pad"]))
        if lv.get("drone", 0) > 0:
            ev.append(Drone(total_seconds, self.harmony.root, rng, 0.25 * lv["drone"]))
        dens = th["density"]
        if lv.get("bowls", 0) > 0:
            t = rng.uniform(3, 12)
            while t < total_seconds - 20:
                tones = self.harmony.chord_tones_at(t)
                m = int(rng.choice(tones)) + 12 * int(rng.integers(0, 2))
                ev.append(Bowl(t, midi_hz(m), rng, 0.7 * lv["bowls"]))
                t += rng.exponential(dens["bowls_every"]) + 6
        if lv.get("bells", 0) > 0:
            t = rng.uniform(5, 15)
            while t < total_seconds - 10:
                tones = self.harmony.chord_tones_at(t)
                scale_extra = [self.harmony.note(i) for i in range(7)]
                m = int(rng.choice(tones + scale_extra[:3])) + 24
                ev.append(BellNote(t, midi_hz(m), rng, 0.36 * lv["bells"]))
                # occasionally a short 2-3 note phrase
                if rng.random() < 0.3:
                    for k in range(int(rng.integers(1, 3))):
                        m2 = self.harmony.note(int(rng.integers(0, 7))) + 24
                        ev.append(BellNote(t + 0.6 * (k + 1) * rng.uniform(0.8, 1.5),
                                           midi_hz(m2), rng, 0.27 * lv["bells"]))
                t += rng.exponential(dens["bells_every"]) + 2
        self.events = ev
        self.nature_events = []
        if lv.get("birds", 0) > 0:
            t = rng.uniform(2, 10)
            while t < total_seconds - 5:
                self.nature_events.append(BirdCall(t, rng, 0.35 * lv["birds"]))
                t += rng.exponential(dens["birds_every"]) + 1.5

        # continuous beds
        self.beds = []
        bed_cls = {"brown": BrownNoise, "rain": Rain, "ocean": Ocean, "wind": Wind, "fire": Fire}
        for name, cls in bed_cls.items():
            if lv.get(name, 0) > 0:
                self.beds.append((name, cls(np.random.default_rng(rng.integers(1 << 31))).calibrate(0.1),
                                  lv[name]))
        if lv.get("binaural", 0) > 0:
            b = th["binaural"]
            self.beds.append(("binaural", Binaural(rng, b["carrier_hz"], b["beat_hz"]).calibrate(0.05),
                              lv["binaural"]))

        # slow macro evolution of each layer's level (keeps an hour from feeling static)
        lo, hi = th["evolve_minutes"]
        self.evolve = {}
        for name in list(self.MUSIC) + list(self.BEDS) + ["birds"]:
            self.evolve[name] = (60 * rng.uniform(lo, hi), rng.uniform(0, TWO_PI))

        self.reverb = Reverb(th["reverb"]["seconds"], rng)
        self.wet = th["reverb"]["mix"]
        self.master_hp = hp(28)
        self.master_lp = lp(th["lowpass_hz"])
        self.gain = 1.0

    def _evo(self, name, t, depth=0.35):
        p, ph = self.evolve[name]
        return (1 - depth) + depth * np.sin(TWO_PI * t / p + ph)

    def render_chunk(self, i0, n):
        t_abs = (np.arange(n) + i0) / SR
        music = np.zeros((n, 2))
        for e in self.events:
            if e.start * SR < i0 + n and (e.start + e.dur) * SR > i0:
                e.render_into(music, i0)
        wet = self.reverb(music)
        music = (1 - self.wet) * music + self.wet * wet * 1.6

        nature = np.zeros((n, 2))
        for e in self.nature_events:
            if e.start * SR < i0 + n and (e.start + e.dur) * SR > i0:
                e.render_into(nature, i0)
        if self.nature_events:
            nature *= self._evo("birds", t_abs)[:, None]
        for name, bed, level in self.beds:
            nature += bed(t_abs) * level * self._evo(name, t_abs, 0.25)[:, None]

        x = music + nature
        x = self.master_lp(self.master_hp(x))
        # master fades
        fi = np.clip(t_abs / 8.0, 0, 1)
        fo = np.clip((self.total - t_abs) / 15.0, 0, 1)
        x *= (fi * fo)[:, None] ** 2
        return x

    @staticmethod
    def soft_clip(x, ceiling=0.89):
        return np.tanh(x / ceiling) * ceiling


def calibrate_gain(theme, target_dbfs=-20.0, probe_s=90):
    """Render a short probe to find a master gain hitting the target RMS."""
    probe = AmbientEngine(theme, max(probe_s + 20, 120))
    n = probe_s * SR
    acc, i = [], 10 * SR  # skip the fade-in
    while i < n:
        c = probe.render_chunk(i, CHUNK_SECONDS * SR)
        acc.append(np.mean(c ** 2))
        i += CHUNK_SECONDS * SR
    rms = math.sqrt(float(np.mean(acc))) or 1e-6
    return 10 ** (target_dbfs / 20) / rms


def render(theme: dict, out_path: str, minutes: float | None = None, bitrate="256k", verbose=True):
    th = deep_merge(DEFAULT_THEME, theme)
    total = 60.0 * (minutes if minutes is not None else th["duration_min"])
    gain = calibrate_gain(th)
    eng = AmbientEngine(th, total)
    n_total = int(total * SR)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", "2",
           "-i", "pipe:0"]
    if out_path.endswith(".wav"):
        cmd += [out_path]
    elif out_path.endswith(".flac"):
        cmd += ["-c:a", "flac", out_path]
    else:
        cmd += ["-c:a", "aac", "-b:a", bitrate, out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    i, step = 0, CHUNK_SECONDS * SR
    peak = 0.0
    while i < n_total:
        n = min(step, n_total - i)
        x = eng.render_chunk(i, n) * gain
        peak = max(peak, float(np.max(np.abs(x))))
        x = AmbientEngine.soft_clip(x)
        proc.stdin.write((x * 32767).astype("<i2").tobytes())
        i += n
        if verbose:
            done = i / n_total
            el = time.time() - t0
            sys.stderr.write(f"\r[music] {done*100:5.1f}%  {el:6.0f}s elapsed, ~{el/done - el:5.0f}s left")
    proc.stdin.close()
    proc.wait()
    if verbose:
        sys.stderr.write(f"\n[music] done in {time.time()-t0:.0f}s, pre-clip peak {peak:.2f}\n")
    if proc.returncode:
        raise RuntimeError("ffmpeg failed")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", required=True, help="theme JSON (the 'music' block is used if present)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=None, help="override duration (for previews)")
    a = ap.parse_args()
    with open(a.theme) as f:
        theme = json.load(f)
    music = theme.get("music", theme)
    music.setdefault("title", theme.get("title", "untitled"))
    render(music, a.out, a.minutes)


if __name__ == "__main__":
    main()
