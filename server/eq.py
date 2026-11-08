"""Tone control for transmitted voice and clips: a bass shelf and a treble shelf.

Phone and laptop mics, and the browser's voice processing, often sound thin on FM. Lifting the
bass and easing the treble gives a fuller sound on air. Selcall tones are not passed through
this: their levels and frequencies have to stay exact for decoders.

The two shelves are the usual RBJ "audio EQ cookbook" shelving filters (slope 1). Rather than
run them as IIR filters sample by sample in Python, their combined response is turned into a
linear-phase FIR, which numpy applies in one call per 20 ms frame (and to a whole clip at once).
TAPS = 255 at 16 kHz resolves the shelves down to about 100 Hz and delays audio by 8 ms.

Note that FM land-mobile radios filter their own audio to roughly 300-3000 Hz, so no setting here
adds bass below about 300 Hz on air.
"""

import math

import numpy as np

TAPS = 255


def _shelf(f, rate, f0, gain_db, high):
    """Magnitude response at frequencies f (Hz) of an RBJ shelving filter with slope 1."""
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / rate
    cw, alpha = math.cos(w0), math.sin(w0) / 2 * math.sqrt(2)
    sa = 2 * math.sqrt(a) * alpha
    if high:
        b = [a * ((a + 1) + (a - 1) * cw + sa), -2 * a * ((a - 1) + (a + 1) * cw), a * ((a + 1) + (a - 1) * cw - sa)]
        d = [(a + 1) - (a - 1) * cw + sa, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - sa]
    else:
        b = [a * ((a + 1) - (a - 1) * cw + sa), 2 * a * ((a - 1) - (a + 1) * cw), a * ((a + 1) - (a - 1) * cw - sa)]
        d = [(a + 1) + (a - 1) * cw + sa, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - sa]
    z = np.exp(-1j * 2 * np.pi * np.asarray(f) / rate)
    return np.abs((b[0] + b[1] * z + b[2] * z * z) / (d[0] + d[1] * z + d[2] * z * z))


class TxEq:
    def __init__(self, rate, bass_db=0.0, bass_hz=400, treble_db=0.0, treble_hz=2500):
        self.enabled = bool(bass_db or treble_db)
        self.taps = None
        if not self.enabled:
            return
        # Frequency sampling: the wanted magnitude on a fine grid, zero phase, then windowed to TAPS.
        n = 8192
        f = np.fft.rfftfreq(n, 1 / rate)
        mag = _shelf(f, rate, bass_hz, bass_db, high=False) * _shelf(f, rate, treble_hz, treble_db, high=True)
        h = np.roll(np.fft.irfft(mag, n), TAPS // 2)[:TAPS] * np.hanning(TAPS)
        self.taps = h.astype(np.float32)
        self.reset()

    def reset(self):
        """Start of a new transmission: forget the previous one's last few milliseconds."""
        if self.enabled:
            self.hist = np.zeros(TAPS - 1, np.float32)

    def __call__(self, x):
        """Filter the next block of a stream (float32). Output is delayed by TAPS // 2 samples."""
        if not self.enabled:
            return x
        buf = np.concatenate([self.hist, x])
        self.hist = buf[len(buf) - TAPS + 1:]
        return np.convolve(buf, self.taps, mode="valid").astype(np.float32)

    def pcm(self, data):
        """The same, for int16 LE PCM bytes (voice frames from the browser)."""
        if not self.enabled or len(data) % 2:
            return data
        y = self(np.frombuffer(data, "<i2").astype(np.float32) / 32768)
        return (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()

    def whole(self, x):
        """Filter a complete clip in one go, with no delay and no state."""
        if not self.enabled:
            return x
        return np.convolve(x, self.taps, mode="full")[TAPS // 2: TAPS // 2 + len(x)].astype(np.float32)

    def response_db(self, freqs, rate):
        """For checking: the FIR's gain at the given frequencies, in dB."""
        if not self.enabled:
            return np.zeros(len(freqs))
        z = np.exp(-1j * 2 * np.pi * np.outer(freqs, np.arange(TAPS)) / rate)
        return 20 * np.log10(np.abs(z @ self.taps.astype(np.float64)))
