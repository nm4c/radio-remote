"""Voice effects for transmitted voice: Vader, Dalek, Demon and friends.

Every preset starts with Resynth, which replaces the speaker's voice outright and keeps only what
is said (see its notes). The rest colour the new voice:
  RingMod     multiplies the voice by a sine: the classic Dalek sound at 30 Hz.
  Comb        feedback or feedforward comb: metal masks, rough echo, reverb tails.
  Reverb      a few combs in parallel: a rough room.
  Shape       tanh saturation, for grit.
  Tone        the bass/treble shelves from eq.py.

Audio arrives in blocks of any length (20 ms from the WebSocket, whatever the decoder hands over
from WebRTC), so every effect keeps its own history and works on whole numpy blocks.

Each preset's output is matched in loudness to its input at start-up (see _calibrate), then
soft-limited, so switching effects doesn't change the deviation much.
"""

import math

import numpy as np

from .eq import TxEq


# ---- building blocks ---------------------------------------------------------

class Resynth:
    """Source-filter resynthesis: keeps what is said, replaces who says it.

    Every hop (8 ms) it takes the last 32 ms of voice and splits it into
      - the spectral envelope (cepstral smoothing): the shape of the mouth and throat, which
        carries the words, and
      - the pitch and how voiced the sound is (autocorrelation).
    The envelope is stretched by `warp` along the frequency axis, as if the vocal tract were
    shorter (> 1: smaller, higher, female, child) or longer (< 1: bigger, deeper), and played
    through a brand-new synthetic voice source:
      - a band-limited pulse train at `f0` Hz, following the speaker's intonation only to the
        power `keep` (0 = monotone, 1 = all of it, relative to the speaker's own average pitch),
      - an extra pulse train an octave below (`sub`) for growl,
      - noise for unvoiced sounds (s, f, sh) and, with `breath`, mixed into voiced ones too.
        f0=None gives noise only: a whisper.
    Nothing of the original voice source reaches the output, so the speaker's pitch, roughness
    and timbre are gone; only timing and articulation remain.

    Each frame's output energy is matched to the input frame's. Delay: one frame (32 ms).
    """

    N = 512          # analysis frame: 32 ms at 16 kHz
    HOP = 128        # 8 ms
    LIFTER = 36      # cepstral coefficients kept for the envelope (fewer than the pitch period)
    F0_RANGE = (65, 400)

    def __init__(self, rate, warp=1.0, f0=None, keep=0.5, sub=0.0, breath=0.0):
        self.rate, self.f0, self.keep, self.sub, self.breath = rate, f0, keep, sub, breath
        n = self.N
        self.win = np.hanning(n).astype(np.float32)
        self.norm = float(np.sum(self.win ** 2) / self.HOP)
        # Autocorrelation of the window, to undo its taper in the pitch detector.
        rw = np.fft.irfft(np.abs(np.fft.rfft(self.win, 2 * n)) ** 2)[:n]
        self.rw = np.maximum(rw / rw[0], 1e-3)
        self.lo = int(rate / self.F0_RANGE[1])
        self.hi = int(rate / self.F0_RANGE[0])
        self.bins = np.arange(n // 2 + 1)
        self.warp_at = self.bins / warp  # the new envelope at bin b is the old one at b / warp
        self.inbuf = np.zeros(n, np.float32)
        self.exc = np.zeros(n, np.float32)
        self.acc = np.zeros(n, np.float32)
        self.ready = np.zeros(0, np.float32)
        self.pending = 0
        self.phase = 0.0
        self.mean_f0 = 130.0           # running average of the speaker's pitch
        self.f_out = f0 or 100.0
        self.v = 0.0                   # voicing, 0..1
        self.rng = np.random.default_rng()

    def _pitch(self, xw):
        """(f0 in Hz or None, voicing 0..1) of a windowed frame."""
        if float(np.dot(xw, xw)) < (0.003 ** 2) * self.N * 0.375:  # quieter than about -50 dBFS
            return None, 0.0
        r = np.fft.irfft(np.abs(np.fft.rfft(xw, 2 * self.N)) ** 2)[:self.N]
        r = r / (r[0] * self.rw)
        k = self.lo + int(np.argmax(r[self.lo:self.hi]))
        strength = float(r[k])
        half = k // 2
        if half >= self.lo and r[half] > 0.85 * strength:  # octave error: take the shorter period
            k, strength = half, float(r[half])
        a, b, c = r[k - 1], r[k], r[k + 1]
        den = a - 2 * b + c
        period = k + (0.5 * (a - c) / den if den else 0.0)
        return self.rate / period, float(np.clip((strength - 0.35) / 0.3, 0, 1))

    def _source(self, f_from, f_to, v_from, v_to):
        """The next HOP samples of the synthetic voice source."""
        h = self.HOP
        f = np.linspace(f_from, f_to, h, endpoint=False)
        phi = self.phase + np.cumsum(2 * np.pi * f / self.rate)
        self.phase = float(phi[-1] % (4 * np.pi))  # 4 pi keeps the sub-octave continuous too

        def pulses(ph, top):
            # Sum of cos(k ph) for k = 1..K (a Dirichlet kernel): K equal harmonics up to 7.6 kHz.
            k = max(1, int(7600 / top))
            s = np.sin(ph / 2)
            safe = np.abs(s) > 1e-6
            d = np.where(safe, np.sin((k + 0.5) * ph) / (2 * np.where(safe, s, 1.0)) - 0.5, float(k))
            return d / math.sqrt(k / 2)

        top = max(f_from, f_to)
        voiced = pulses(phi, top)
        if self.sub:
            voiced = voiced + self.sub * pulses(phi / 2, top / 2)
        v = np.linspace(v_from, v_to, h, endpoint=False)
        noise = self.rng.standard_normal(h)
        return (v * voiced + (1 - v + self.breath) * noise).astype(np.float32)

    def _frame(self):
        xw = self.inbuf * self.win
        f0_in, v = self._pitch(xw) if self.f0 else (None, 0.0)
        if f0_in:
            self.mean_f0 += 0.03 * v * (f0_in - self.mean_f0)
            f_new = self.f0 * (f0_in / self.mean_f0) ** self.keep
        else:
            f_new = self.f_out
        self.exc = np.concatenate([self.exc[self.HOP:], self._source(self.f_out, f_new, self.v, v)])
        self.f_out, self.v = f_new, v

        # Envelope by cepstral smoothing, then stretched along the frequency axis.
        X = np.fft.rfft(xw)
        cep = np.fft.irfft(np.log(np.abs(X) + 1e-6))
        cep[self.LIFTER:len(cep) - self.LIFTER + 1] = 0
        env = np.exp(np.fft.rfft(cep).real)
        env = np.interp(self.warp_at, self.bins, env, right=env[-1] * 0.1)

        Y = np.fft.rfft(self.exc * self.win) * env
        ey = float(np.sum(np.abs(Y) ** 2))
        if ey > 0:
            Y *= math.sqrt(float(np.sum(np.abs(X) ** 2)) / ey)
        y = np.fft.irfft(Y, self.N).astype(np.float32) * self.win / self.norm
        self.acc += y
        self.ready = np.concatenate([self.ready, self.acc[:self.HOP]])
        self.acc = np.concatenate([self.acc[self.HOP:], np.zeros(self.HOP, np.float32)])

    def __call__(self, x):
        for s in range(0, len(x), self.HOP):
            c = x[s:s + self.HOP]
            self.inbuf = np.concatenate([self.inbuf[len(c):], c])
            self.pending += len(c)
            if self.pending >= self.HOP:
                self.pending -= self.HOP
                self._frame()
        need = len(x)
        if len(self.ready) < need:  # still filling the first frame
            self.ready = np.concatenate([np.zeros(need - len(self.ready), np.float32), self.ready])
        out, self.ready = self.ready[:need], self.ready[need:]
        return out


class RingMod:
    def __init__(self, rate, freq, depth=1.0):
        self.w = 2 * np.pi * freq / rate
        self.depth = depth
        self.n = 0

    def __call__(self, x):
        carrier = np.sin(self.w * (self.n + np.arange(len(x)))).astype(np.float32)
        self.n += len(x)
        return x * (1 - self.depth + self.depth * carrier)


class Comb:
    """y[n] = x[n] + ff * x[n-D] + fb * y[n-D]. Worked in chunks of D samples, so each chunk only
    needs earlier output and stays vectorised."""

    def __init__(self, rate, delay_ms, fb=0.0, ff=0.0, mix=1.0):
        self.d = max(1, int(rate * delay_ms / 1000))
        self.fb, self.ff, self.mix = fb, ff, mix
        self.xh = np.zeros(self.d, np.float32)
        self.yh = np.zeros(self.d, np.float32)

    def __call__(self, x):
        out = np.empty_like(x)
        for s in range(0, len(x), self.d):
            c = x[s:s + self.d]
            n = len(c)
            y = c + self.ff * self.xh[:n] + self.fb * self.yh[:n]
            # Shift the histories along by n.
            self.xh = np.concatenate([self.xh[n:], c])
            self.yh = np.concatenate([self.yh[n:], y])
            out[s:s + n] = (1 - self.mix) * c + self.mix * y
        return out


class Reverb:
    """A few parallel feedback combs: a rough room."""

    def __init__(self, rate, size=1.0, decay=0.6, mix=0.3):
        self.combs = [Comb(rate, ms * size, fb=decay) for ms in (29.7, 37.1, 41.1, 43.7)]
        self.mix = mix

    def __call__(self, x):
        wet = sum(c(x) for c in self.combs) / len(self.combs)
        return (1 - self.mix) * x + self.mix * (wet - x)


class Shape:
    def __init__(self, drive):
        self.drive = drive

    def __call__(self, x):
        return np.tanh(x * self.drive) / math.tanh(self.drive)


class Tone:
    def __init__(self, rate, bass_db=0, bass_hz=400, treble_db=0, treble_hz=2500):
        self.eq = TxEq(rate, bass_db, bass_hz, treble_db, treble_hz)
        self.eq.reset()

    def __call__(self, x):
        return self.eq(x)


# ---- presets -------------------------------------------------------------------
# warp: vocal tract size (< 1 bigger, > 1 smaller); f0: the new voice's pitch in Hz;
# keep: how much of the speaker's intonation survives (0 = monotone).

def _vader(r):
    return [Resynth(r, warp=0.86, f0=78, keep=0.5, breath=0.04),
            Tone(r, bass_db=6, bass_hz=500, treble_db=-3, treble_hz=3000),
            Comb(r, 2.3, ff=0.6, mix=0.7), Comb(r, 3.7, fb=0.35, mix=0.5), Shape(1.8)]


def _dalek(r):
    # A flat, slightly small voice through a 30 Hz ring modulator, as on the original.
    return [Resynth(r, warp=1.05, f0=95, keep=0.25), Tone(r, bass_db=-6, bass_hz=400),
            RingMod(r, 30), Shape(2.5), Comb(r, 4.0, ff=0.4, mix=0.5)]


def _demon(r):
    # A huge tract, a very low voice with an octave-down growl, breath, distortion and a dark room.
    return [Resynth(r, warp=0.72, f0=58, keep=0.4, sub=0.8, breath=0.15), RingMod(r, 23, depth=0.35),
            Shape(2.8), Tone(r, bass_db=8, bass_hz=450, treble_db=-6, treble_hz=2200),
            Reverb(r, size=1.4, decay=0.6, mix=0.3)]


def _robot(r):
    return [Resynth(r, warp=1.0, f0=100, keep=0.0), Comb(r, 6.25, fb=0.3, mix=0.4), Shape(1.5)]


def _giant(r):
    return [Resynth(r, warp=0.78, f0=62, keep=0.5),
            Tone(r, bass_db=6, bass_hz=500, treble_db=-4, treble_hz=2500),
            Reverb(r, size=1.6, decay=0.55, mix=0.25)]


def _female(r):
    # A shorter vocal tract and a higher, breathy source, keeping most of the intonation.
    return [Resynth(r, warp=1.17, f0=205, keep=0.8, breath=0.2),
            Tone(r, bass_db=-4, bass_hz=350, treble_db=2, treble_hz=3000)]


def _chipmunk(r):
    return [Resynth(r, warp=1.4, f0=330, keep=0.9)]


def _ghost(r):
    # Whisper only: no pitch at all, a slightly smaller tract, and a long tail.
    return [Resynth(r, warp=1.08, f0=None, breath=1.0), Reverb(r, size=1.3, decay=0.7, mix=0.45),
            Tone(r, bass_db=-6, bass_hz=500, treble_db=3, treble_hz=2000)]


PRESETS = {
    "vader": ("Vader", _vader),
    "dalek": ("Dalek", _dalek),
    "demon": ("Demon", _demon),
    "robot": ("Robot", _robot),
    "giant": ("Giant", _giant),
    "female": ("Female", _female),
    "chipmunk": ("Chipmunk", _chipmunk),
    "ghost": ("Ghost", _ghost),
}

LIMIT = 0.9  # soft ceiling on the output peak


def _test_voice(rate, seconds=2.0):
    """Speech-like test signal for level matching: a 120 Hz buzz with vibrato, shaped by two
    formants and chopped into syllables."""
    t = np.arange(int(rate * seconds)) / rate
    f0 = 120 * (1 + 0.05 * np.sin(2 * np.pi * 5 * t))
    phase = 2 * np.pi * np.cumsum(f0) / rate
    x = sum(np.sin(k * phase) / k for k in range(1, 25))
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / rate)
    spec *= np.exp(-((f - 700) / 300) ** 2) + 0.6 * np.exp(-((f - 1800) / 400) ** 2) + 0.05
    x = np.fft.irfft(spec, len(x)) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t) ** 2)
    return (0.3 * x / np.max(np.abs(x))).astype(np.float32)


_GAIN = {}  # preset -> make-up gain, measured once per rate


def _calibrate(name, rate):
    key = (name, rate)
    if key not in _GAIN:
        x = _test_voice(rate)
        chain = PRESETS[name][1](rate)
        y = np.concatenate([_run(chain, x[i:i + 320]) for i in range(0, len(x), 320)])
        skip = rate // 4  # past the effects' start-up delay
        rms_in = np.sqrt(np.mean(x[skip:] ** 2))
        rms_out = np.sqrt(np.mean(y[skip:] ** 2)) or rms_in
        _GAIN[key] = float(rms_in / rms_out)
    return _GAIN[key]


def _run(chain, x):
    for fx in chain:
        x = fx(x)
    return x


class VoiceFx:
    """One operator's effect for one transmission. Make a new one at each PTT press."""

    def __init__(self, name, rate):
        self.name = name
        self.chain = PRESETS[name][1](rate)
        self.gain = _calibrate(name, rate)

    def __call__(self, x):
        y = _run(self.chain, x.astype(np.float32)) * self.gain
        return (np.tanh(y / LIMIT) * LIMIT).astype(np.float32)

    def pcm(self, data):
        if len(data) % 2:
            return data
        y = self(np.frombuffer(data, "<i2").astype(np.float32) / 32768)
        return (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()


def preset_list():
    return [{"id": k, "name": v[0]} for k, v in PRESETS.items()]
