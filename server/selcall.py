"""Selcall: sequential tone calls (1-20 digits), encode for TX and decode from RX audio.

Each digit is a burst of one audio tone; the tones follow each other with no gap. A digit equal
to the one before it is sent as the repeat tone ("E") instead, so every tone differs from its
neighbour and the receiver can tell "11" from one long "1".

Standards (default tone length, frequencies in Hz for 0-9 and the repeat tone):
  ccir   100 ms  the common analogue selcall in Australia and Europe
  zvei1   70 ms
  eea     40 ms  CCIR frequencies, shorter tones
Many systems run a standard's frequencies at a different tone length (CCIR at 50 or 70 ms, say);
pass tone_ms to override the default.
"""

import math

import numpy as np

MAX_DIGITS = 20
TONE_MS_RANGE = (30, 200)  # shortest the decoder can follow, longest any system uses

_CCIR = [1981, 1124, 1197, 1275, 1358, 1446, 1540, 1640, 1747, 1860]
STANDARDS = {
    "ccir": {"tone_ms": 100, "digits": _CCIR, "repeat": 2110},
    "zvei1": {"tone_ms": 70, "digits": [2400, 1060, 1160, 1270, 1400, 1530, 1670, 1830, 2000, 2200], "repeat": 2600},
    "eea": {"tone_ms": 40, "digits": _CCIR, "repeat": 2110},
}
REPEAT = "E"


def check_digits(digits, what="selcall"):
    """Return digits as a string of 1-20 decimal digits, or raise ValueError."""
    d = str(digits).strip()
    if not d.isdigit() or not d.isascii() or not 1 <= len(d) <= MAX_DIGITS:
        raise ValueError(f"{what} must be 1-{MAX_DIGITS} digits (0-9)")
    return d


def symbols(digits):
    """Digits as tone symbols: a repeated digit becomes the repeat tone."""
    out = []
    for i, ch in enumerate(digits):
        out.append(REPEAT if i and ch == digits[i - 1] and out[-1] != REPEAT else ch)
    return out


def _freq(std, sym):
    return std["repeat"] if sym == REPEAT else std["digits"][int(sym)]


def duration_ms(digits, standard, tone_ms=None):
    return len(digits) * (tone_ms or STANDARDS[standard]["tone_ms"])


def encode(digits, standard, rate, level, tone_ms=None):
    """Tone sequence as float32 samples at `rate`, peak amplitude `level` (0-1). tone_ms
    overrides the standard's tone length.

    The phase runs on from tone to tone (no clicks between digits), with 3 ms fades at the ends.
    """
    std = STANDARDS[standard]
    n = int(rate * (tone_ms or std["tone_ms"]) / 1000)
    freqs = np.repeat([_freq(std, s) for s in symbols(check_digits(digits))], n).astype(np.float64)
    phase = 2 * np.pi * np.cumsum(freqs) / rate
    y = np.sin(phase) * level
    ramp = min(len(y) // 2, int(rate * 0.003))
    if ramp:
        fade = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, ramp))
        y[:ramp] *= fade
        y[-ramp:] *= fade[::-1]
    return y.astype(np.float32)


class Decoder:
    """Finds selcall sequences in a stream of received audio.

    Every HOP_MS it takes the last WINDOW_MS of audio and finds its strongest frequency (a
    windowed FFT, interpolated between bins). The frame counts as a tone when that peak holds
    most of the frame's power and lies close to one of the standard's tones: within 3%, and
    within 45% of the way to the next tone, so neighbours never overlap. That copes with radios
    whose tones are a few per cent off.

    A digit is accepted once its tone has lasted about half its nominal length, and a sequence
    ends after a silence of 1.5 tone lengths. Voice rarely holds one clean tone at these exact
    frequencies for that long, and min_digits filters out the odd lone match.
    """

    WINDOW_MS = 20
    HOP_MS = 5
    FFT_SIZE = 4096
    PURITY = 0.6      # share of the frame's power within the peak's main lobe
    TOLERANCE = 0.03  # largest frequency error accepted, as a fraction of the tone
    MIN_RMS = 0.003   # about -50 dBFS: quieter frames are treated as silence

    def __init__(self, standard, rate, min_digits=3, on_call=None, tone_ms=None):
        std = STANDARDS[standard]
        self.rate = rate
        self.n = int(rate * self.WINDOW_MS / 1000)
        self.hop = int(rate * self.HOP_MS / 1000)
        self.window = np.hanning(self.n).astype(np.float32)
        self.bin_hz = rate / self.FFT_SIZE
        # Hann main lobe: two bins of the unpadded window either side of the peak.
        self.lobe = int(round(2 * rate / self.n / self.bin_hz))
        tones = sorted([(f, str(d)) for d, f in enumerate(std["digits"])] + [(std["repeat"], REPEAT)])
        self.tones = []  # (frequency, symbol, tolerance in Hz)
        for k, (f, sym) in enumerate(tones):
            gaps = [abs(f - tones[m][0]) for m in (k - 1, k + 1) if 0 <= m < len(tones)]
            self.tones.append((f, sym, min(self.TOLERANCE * f, 0.45 * min(gaps))))
        self.lo = int((tones[0][0] * (1 - self.TOLERANCE) - 50) / self.bin_hz)
        self.hi = int((tones[-1][0] * (1 + self.TOLERANCE) + 50) / self.bin_hz) + 1
        # Timing follows the expected tone length. Longer tones still decode (a digit only has to
        # last long enough), so a decoder set for 50 ms also hears 100 ms calls.
        tone = tone_ms or std["tone_ms"]
        tone_frames = tone / self.HOP_MS
        self.min_run = max(2, math.ceil((tone - self.WINDOW_MS) / self.HOP_MS * 0.5))
        self.short_gap = 3                              # frames between tones while they cross over
        self.end_gap = math.ceil(1.5 * tone_frames)     # silence that ends a sequence
        self.min_digits = min_digits
        self.on_call = on_call
        self.reset()

    def reset(self):
        self.skip = False    # ignoring tones until a gap (see blank)
        self.cur = None      # symbol in the current run
        self.run = 0         # frames of it so far
        self.gap = 0         # frames since a tone was last seen
        self.seq = []        # accepted symbols
        self.buf = np.zeros(0, np.float32)

    def blank(self):
        """Call while the radio transmits. Afterwards, tones are ignored until the channel has
        been quiet for a sequence-ending gap, so the tail of a call that was half over when we
        unkeyed isn't reported as a shorter call."""
        self.reset()
        self.skip = True

    def feed(self, pcm):
        """Feed int16 little-endian PCM bytes. Returns any calls completed, as digit strings."""
        x = np.frombuffer(pcm, "<i2").astype(np.float32) / 32768
        self.buf = np.concatenate([self.buf, x])
        found = []
        while len(self.buf) >= self.n:
            call = self._frame(self.buf[: self.n])
            self.buf = self.buf[self.hop:]
            if call:
                found.append(call)
                if self.on_call:
                    self.on_call(call)
        return found

    def _classify(self, frame):
        if float(np.dot(frame, frame)) < self.MIN_RMS ** 2 * self.n:
            return None
        spec = np.abs(np.fft.rfft(frame * self.window, self.FFT_SIZE)) ** 2
        k = self.lo + int(np.argmax(spec[self.lo:self.hi]))
        # Share of the frame's power in the peak's main lobe. rfft holds only positive frequencies,
        # so every bin except DC and Nyquist stands for two (Parseval).
        total = 2 * spec.sum() - spec[0] - spec[-1]
        lobe = 2 * spec[max(0, k - self.lobe): k + self.lobe + 1].sum()
        if lobe < self.PURITY * total:
            return None
        # Interpolate between FFT bins for the exact peak frequency.
        a, b, c = spec[k - 1], spec[k], spec[k + 1]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c else 0.0
        f = (k + shift) * self.bin_hz
        for tone, sym, tol in self.tones:
            if abs(f - tone) <= tol:
                return sym
        return None

    def _frame(self, frame):
        sym = self._classify(frame)
        if self.skip:
            self.gap = self.gap + 1 if sym is None else 0
            if self.gap >= self.end_gap:
                self.skip, self.gap = False, 0
            return None
        if sym is None:
            self.gap += 1
            if self.gap > self.short_gap:
                self.cur, self.run = None, 0
            if self.gap >= self.end_gap and self.seq:
                return self._finish()
            return None
        self.gap = 0
        if sym == self.cur:
            self.run += 1
        else:
            self.cur, self.run = sym, 1
        if self.run == self.min_run:
            self.seq.append(sym)
        return None

    def _finish(self):
        seq, self.seq = self.seq, []
        digits = []
        for s in seq:
            if s == REPEAT:
                if not digits:
                    return None  # a sequence can't start with the repeat tone
                digits.append(digits[-1])
            else:
                digits.append(s)
        if not self.min_digits <= len(digits) <= MAX_DIGITS:
            return None
        return "".join(digits)
