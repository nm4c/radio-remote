"""Cutting received audio into overs (one station's transmission each), and audio helpers.

The server feeds every received 20 ms frame here with whether the channel is busy (the radio's
busy line, or the RX level without one) and whether we are transmitting. An over starts when the
channel goes busy, keeps `preroll_ms` from just before so the first syllable isn't lost, and ends
when the channel has been quiet for `hang_ms`. While we transmit the receiver is muted, so
anything in progress is dropped rather than mixed with our own silence.
"""

import io
import time
import wave

import numpy as np

from ..audio import _lowpass


class OverCapture:
    def __init__(self, rate, preroll_ms, hang_ms, min_s, max_s, on_over):
        """on_over(pcm_bytes, started_at, seconds) is called for each finished over."""
        self.rate = rate
        self.preroll_max = int(rate * preroll_ms / 1000) * 2
        self.hang_max = int(rate * hang_ms / 1000) * 2
        self.min_bytes = int(rate * min_s) * 2
        self.max_bytes = int(rate * max_s) * 2
        self.on_over = on_over
        self.preroll = bytearray()
        self.buf = None          # the over being captured
        self.started = None
        self.hang = 0            # quiet bytes since the channel last went quiet
        self.last_end = 0.0      # time.time() the last over ended

    @property
    def active(self):
        return self.buf is not None

    def feed(self, pcm, busy, keyed):
        if keyed:
            self.buf = None      # our own transmission: the receiver is muted
            self.preroll.clear()
            return
        if self.buf is None:
            if busy:
                self.buf = bytearray(self.preroll)
                self.started = time.time() - len(self.preroll) / 2 / self.rate
                self.preroll.clear()
                self.hang = 0
            else:
                self.preroll += pcm
                if len(self.preroll) > self.preroll_max:
                    del self.preroll[:len(self.preroll) - self.preroll_max]
                return
        self.buf += pcm
        self.hang = 0 if busy else self.hang + len(pcm)
        if self.hang >= self.hang_max:
            self._finish(trim=self.hang)
        elif len(self.buf) >= self.max_bytes:
            self._finish(trim=0, more=busy)

    def _finish(self, trim, more=False):
        pcm = bytes(self.buf[:len(self.buf) - max(0, trim - self.hang_max // 4)])
        started = self.started
        self.buf = None
        if more:  # a long over: carry on in a new part with no gap
            self.buf = bytearray()
            self.started = time.time()
        self.last_end = time.time()
        if len(pcm) >= self.min_bytes:
            self.on_over(pcm, started, len(pcm) / 2 / self.rate)


# ---- helpers ------------------------------------------------------------------

def to_float(pcm):
    return np.frombuffer(pcm, "<i2").astype(np.float32) / 32768


def to_pcm(x):
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def resample(x, src, dst):
    """Resample float32 mono audio. Good enough for speech: a windowed-sinc low-pass below the
    lower Nyquist frequency, then linear interpolation onto the new sample times."""
    if src == dst or len(x) == 0:
        return x.astype(np.float32)
    if dst < src:
        taps = _lowpass(0.45 * dst / src, 127)
        x = np.convolve(x, taps, mode="same")
    n = int(round(len(x) * dst / src))
    t = np.arange(n) * (src / dst)
    return np.interp(t, np.arange(len(x)), x).astype(np.float32)


def wav_bytes(pcm, rate):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def read_wav(path_or_bytes, rate):
    """Any PCM WAV (mono or stereo, 8/16/32-bit) as float32 mono at `rate`."""
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else str(path_or_bytes)
    with wave.open(src) as w:
        ch, width, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
    elif width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return resample(x, fr, rate)
