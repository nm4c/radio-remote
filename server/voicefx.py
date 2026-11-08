"""Voice effects for transmitted voice: Vader, Dalek, Demon and friends.

Each preset is a chain of small streaming effects. Audio arrives in blocks of any length (20 ms
from the WebSocket, whatever the decoder hands over from WebRTC), so every effect keeps its own
history and works on whole numpy blocks rather than sample by sample.

Building blocks:
  PitchShift  delay-line pitch shifter: two read taps sweep through a short buffer at the new
              speed, each faded out while it jumps back. Cheap, about 25 ms of delay. It shifts
              pitch and formants together, which is what makes Chipmunk cartoonish.
  RingMod     multiplies the voice by a sine: the classic Dalek sound at 30 Hz.
  Comb        feedback or feedforward comb: metal masks, rough echo, reverb tails.
  Stft        short-time Fourier processing: zero phase gives Robot's monotone buzz, random
              phase turns voice into a whisper (Ghost, and Female's breathiness).
  Shape       tanh saturation, for grit.
  Tone        the bass/treble shelves from eq.py.

Each preset's output is matched in loudness to its input at start-up (see _calibrate), then
soft-limited, so switching effects doesn't change the deviation much.
"""

import math

import numpy as np

from .eq import TxEq


# ---- building blocks ---------------------------------------------------------

class PitchShift:
    def __init__(self, rate, semitones, window_ms=40):
        self.ratio = 2 ** (semitones / 12)
        self.win = int(rate * window_ms / 1000)
        self.min_delay = 2
        self.hist = np.zeros(self.win + self.min_delay + 2, np.float32)
        self.n = 0  # samples processed so far: the sweep's clock

    def __call__(self, x):
        buf = np.concatenate([self.hist, x])
        h = len(self.hist)
        i = np.arange(len(x))
        # Phase of the delay sweep, 0..1. The delay changes by (1 - ratio) samples per sample.
        phase = ((self.n + i) * (1 - self.ratio) / self.win) % 1.0
        out = np.zeros(len(x), np.float32)
        for p in (phase, (phase + 0.5) % 1.0):
            pos = h + i - (self.min_delay + p * self.win)
            k = np.floor(pos).astype(int)
            frac = (pos - k).astype(np.float32)
            tap = buf[k] * (1 - frac) + buf[k + 1] * frac
            out += tap * np.sin(np.pi * p).astype(np.float32) ** 2  # silent at the jump
        self.hist = buf[len(buf) - h:]
        self.n += len(x)
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


class Stft:
    """Overlap-add STFT. mode "robot" sets every phase to zero (a buzz at rate/hop Hz);
    "whisper" randomises the phases (noise shaped like the voice)."""

    def __init__(self, rate, mode, n=512, hop=None, mix=1.0):
        self.mode, self.n, self.mix = mode, n, mix
        self.hop = hop or n // 4
        self.win = np.hanning(n).astype(np.float32)
        self.inbuf = np.zeros(n, np.float32)    # last n input samples
        self.pending = 0                        # input samples since the last frame
        self.acc = np.zeros(n, np.float32)      # overlap-add accumulator
        self.ready = np.zeros(0, np.float32)    # finished output waiting to go
        self.rng = np.random.default_rng()
        # Hann^2 overlap-add gain for this hop, so the output level matches the input.
        self.norm = np.sum(self.win ** 2) / self.hop

    def _frame(self):
        spec = np.fft.rfft(self.inbuf * self.win)
        mag = np.abs(spec)
        if self.mode == "robot":
            spec = mag
        else:
            spec = mag * np.exp(1j * self.rng.uniform(0, 2 * np.pi, len(mag)))
        y = np.fft.irfft(spec, self.n).astype(np.float32) * self.win / self.norm
        self.acc += y
        self.ready = np.concatenate([self.ready, self.acc[:self.hop]])
        self.acc = np.concatenate([self.acc[self.hop:], np.zeros(self.hop, np.float32)])

    def __call__(self, x):
        dry = x
        for s in range(0, len(x), self.hop):
            c = x[s:s + self.hop]
            self.inbuf = np.concatenate([self.inbuf[len(c):], c])
            self.pending += len(c)
            if self.pending >= self.hop:
                self.pending -= self.hop
                self._frame()
        # Output runs n samples behind; pad with silence until the first frames are through.
        need = len(x)
        if len(self.ready) < need:
            self.ready = np.concatenate([np.zeros(need - len(self.ready), np.float32), self.ready])
        wet, self.ready = self.ready[:need], self.ready[need:]
        if self.mix >= 1:
            return wet
        # The dry part is delayed to line up with the wet part.
        return self.mix * wet + (1 - self.mix) * self._delay(dry)

    def _delay(self, x):
        if not hasattr(self, "dh"):
            self.dh = np.zeros(self.n, np.float32)
        buf = np.concatenate([self.dh, x])
        self.dh = buf[len(x):]
        return buf[:len(x)]


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


class Mix:
    """Several chains in parallel, summed with weights: [(weight, [effects...]), ...]."""

    def __init__(self, branches):
        self.branches = branches

    def __call__(self, x):
        out = np.zeros(len(x), np.float32)
        for weight, chain in self.branches:
            y = x
            for fx in chain:
                y = fx(y)
            out += weight * y
        return out


# ---- presets -------------------------------------------------------------------

def _vader(r):
    return [PitchShift(r, -4), Tone(r, bass_db=8, bass_hz=500, treble_db=-4, treble_hz=3000),
            Comb(r, 2.3, ff=0.6, mix=0.7), Comb(r, 3.7, fb=0.35, mix=0.5), Shape(1.8)]


def _robot(r):
    return [Stft(r, "robot", n=512, hop=160), Comb(r, 6.25, fb=0.3, mix=0.4), Shape(1.5)]


def _chipmunk(r):
    return [PitchShift(r, 7, window_ms=25), Tone(r, bass_db=-4, bass_hz=400)]


def _giant(r):
    return [PitchShift(r, -9, window_ms=50), Tone(r, bass_db=9, bass_hz=500, treble_db=-5, treble_hz=2500),
            Reverb(r, size=1.6, decay=0.55, mix=0.25)]


def _ghost(r):
    return [Stft(r, "whisper", n=512), PitchShift(r, 2), Reverb(r, size=1.3, decay=0.7, mix=0.45),
            Tone(r, bass_db=-6, bass_hz=500, treble_db=3, treble_hz=2000)]


def _female(r):
    # Higher, softer and breathy: up 4 semitones, a little whispered air mixed in, warm top end.
    return [PitchShift(r, 4, window_ms=30), Stft(r, "whisper", n=512, mix=0.18),
            Tone(r, bass_db=-5, bass_hz=350, treble_db=2, treble_hz=3000)]


def _dalek(r):
    # A 30 Hz ring modulator, as on the original, with some grit and a boxy top end.
    return [Tone(r, bass_db=-6, bass_hz=400), RingMod(r, 30), Shape(2.5), Comb(r, 4.0, ff=0.4, mix=0.5)]


def _demon(r):
    # An octave and a fifth down layered together, growl from a slow ring modulator, distortion.
    return [Mix([(0.8, [PitchShift(r, -12, window_ms=55)]), (0.5, [PitchShift(r, -7, window_ms=50)]),
                 (0.15, [])]),
            RingMod(r, 23, depth=0.35), Shape(2.8),
            Tone(r, bass_db=10, bass_hz=450, treble_db=-6, treble_hz=2200),
            Reverb(r, size=1.4, decay=0.6, mix=0.3)]


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
