"""Audio between the radio (USB sound card) and the network.

Wire format (both directions): signed 16-bit little-endian mono PCM at
`wire_rate` (16 kHz), one WebSocket binary message per `frame_ms` (20 ms).
The sound card runs at `device_rate` (48 kHz) and we resample by an integer
factor with a windowed-sinc FIR filter.
"""

import logging
import math
import threading
import time

import numpy as np

log = logging.getLogger("audio")


# ---- resampling -----------------------------------------------------------

def _lowpass(cutoff, numtaps=95):
    """Windowed-sinc low-pass. cutoff is in cycles/sample (0..0.5)."""
    n = np.arange(numtaps) - (numtaps - 1) / 2
    h = np.sinc(2 * cutoff * n) * np.hamming(numtaps)
    return (h / h.sum()).astype(np.float32)


class _FIR:
    def __init__(self, taps):
        self.taps = taps
        self.reset()

    def reset(self):
        self.hist = np.zeros(len(self.taps) - 1, np.float32)

    def __call__(self, x):
        buf = np.concatenate([self.hist, x])
        self.hist = buf[len(buf) - len(self.hist):]
        return np.convolve(buf, self.taps, mode="valid").astype(np.float32)


class Decimator:
    def __init__(self, m):
        self.m = m
        self.phase = 0
        self.fir = _FIR(_lowpass(0.45 / m)) if m > 1 else None

    def __call__(self, x):
        if self.m == 1:
            return x
        y = self.fir(x)[self.phase::self.m]
        self.phase = (self.phase - len(x)) % self.m
        return y


class Interpolator:
    def __init__(self, m):
        self.m = m
        self.fir = _FIR(_lowpass(0.45 / m)) if m > 1 else None

    def reset(self):
        if self.fir:
            self.fir.reset()

    def __call__(self, x):
        if self.m == 1:
            return x
        up = np.zeros(len(x) * self.m, np.float32)
        up[:: self.m] = x
        return self.fir(up) * self.m


def _dbfs(x):
    if len(x) == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return max(-120.0, 20 * math.log10(max(rms, 1e-6)))


# ---- TX buffer (shared by both backends) ------------------------------------

class _TxBuffer:
    """Jitter buffer feeding the radio. Written from asyncio, read from the audio thread."""

    def __init__(self, rate, prebuffer_ms, max_ms=600):
        self.rate = rate
        self.prebuffer = int(rate * prebuffer_ms / 1000)
        self.max = int(rate * max_ms / 1000)
        self.lock = threading.Lock()
        self.buf = np.zeros(0, np.float32)
        self.started = False
        self.draining = False

    def set_size(self, prebuffer_ms, max_ms):
        with self.lock:
            self.prebuffer = int(self.rate * prebuffer_ms / 1000)
            self.max = int(self.rate * max_ms / 1000)

    def push(self, x):
        with self.lock:
            self.buf = np.concatenate([self.buf, x])
            if len(self.buf) > self.max:  # too far behind: drop oldest
                self.buf = self.buf[len(self.buf) - self.max:]

    def pull(self, frames):
        out = np.zeros(frames, np.float32)
        with self.lock:
            if not self.started and (len(self.buf) >= self.prebuffer or (self.draining and len(self.buf))):
                self.started = True
            if self.started:
                n = min(frames, len(self.buf))
                out[:n] = self.buf[:n]
                self.buf = self.buf[n:]
                if n < frames:
                    self.started = False  # underrun -> rebuffer
        return out

    def buffered_ms(self):
        with self.lock:
            return len(self.buf) * 1000 / self.rate

    def clear(self):
        with self.lock:
            self.buf = np.zeros(0, np.float32)
            self.started = False
            self.draining = False


# ---- real sound card ----------------------------------------------------------

class SoundDeviceAudio:
    def __init__(self, cfg, on_rx):
        a = cfg["audio"]
        self.a = a
        self.dev_rate = int(a["device_rate"])
        self.wire_rate = int(a["wire_rate"])
        self.m = self.dev_rate // self.wire_rate
        self.frame = self.wire_rate * int(a["frame_ms"]) // 1000
        self.dev_block = self.frame * self.m
        self.rx_gain = float(a["rx_gain"])
        self.tx_gain = float(a["tx_gain"])
        self.decim = Decimator(self.m)
        self.interp = Interpolator(self.m)
        self.tx = _TxBuffer(self.dev_rate, a["tx_prebuffer_ms"])
        self.on_rx = on_rx
        self.rx_dbfs = -120.0
        self._streams = []

    @staticmethod
    def _device(spec):
        if spec is None or isinstance(spec, int):
            return spec
        return str(spec)  # sounddevice matches name substrings

    def start(self):
        import sounddevice as sd

        common = dict(samplerate=self.dev_rate, channels=1, dtype="float32",
                      blocksize=self.dev_block, latency="low")
        self._streams = [
            sd.InputStream(device=self._device(self.a["input_device"]), callback=self._in_cb, **common),
            sd.OutputStream(device=self._device(self.a["output_device"]), callback=self._out_cb, **common),
        ]
        for s in self._streams:
            s.start()
        log.info("audio started: in=%s out=%s @ %d Hz", self.a["input_device"] or "default",
                 self.a["output_device"] or "default", self.dev_rate)

    def stop(self):
        for s in self._streams:
            s.stop()
            s.close()
        self._streams = []

    def _in_cb(self, indata, frames, t, status):
        if status:
            log.debug("input status: %s", status)
        y = self.decim(indata[:, 0] * self.rx_gain)
        self.rx_dbfs = _dbfs(y)
        self.on_rx((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())

    def _out_cb(self, outdata, frames, t, status):
        if status:
            log.debug("output status: %s", status)
        outdata[:, 0] = self.tx.pull(frames)

    # called from the asyncio thread
    def push_tx(self, pcm):
        if len(pcm) % 2:
            return
        x = np.frombuffer(pcm, "<i2").astype(np.float32) * (self.tx_gain / 32768)
        self.tx.push(np.clip(self.interp(x), -1, 1))

    def drain(self, on=True):
        self.tx.draining = on

    def set_tx_buffer(self, prebuffer_ms, max_ms):
        self.tx.set_size(prebuffer_ms, max_ms)

    def tx_buffered_ms(self):
        return self.tx.buffered_ms()

    def clear_tx(self):
        self.tx.clear()
        self.interp.reset()


# ---- mock (no sound card) ------------------------------------------------------

class MockAudio:
    """Generates fake RX traffic (a tone burst every ~10 s) and discards TX audio."""

    def __init__(self, cfg, on_rx):
        a = cfg["audio"]
        self.rate = int(a["wire_rate"])
        self.frame = self.rate * int(a["frame_ms"]) // 1000
        self.frame_s = int(a["frame_ms"]) / 1000
        self.tx = _TxBuffer(self.rate, a["tx_prebuffer_ms"])
        self.on_rx = on_rx
        self.rx_dbfs = -120.0
        self._stop = threading.Event()
        self._thread = None
        self._rng = np.random.default_rng()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("mock audio started (tone burst every 10 s)")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(1)

    def _run(self):
        t0 = time.monotonic()
        n = 0
        while not self._stop.is_set():
            n += 1
            delay = t0 + n * self.frame_s - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            idx = np.arange((n - 1) * self.frame, n * self.frame)
            t = idx / self.rate
            y = self._rng.normal(0, 0.003, self.frame).astype(np.float32)
            if (t[0] % 10) < 2.5:  # "someone talking": warbling tone
                y += 0.15 * np.sin(2 * np.pi * (600 + 150 * np.sin(2 * np.pi * 3 * t)) * t).astype(np.float32)
            self.rx_dbfs = _dbfs(y)
            self.on_rx((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
            self.tx.pull(self.frame)  # consume TX at real-time rate

    def push_tx(self, pcm):
        if len(pcm) % 2 == 0:
            self.tx.push(np.frombuffer(pcm, "<i2").astype(np.float32) / 32768)

    def drain(self, on=True):
        self.tx.draining = on

    def set_tx_buffer(self, prebuffer_ms, max_ms):
        self.tx.set_size(prebuffer_ms, max_ms)

    def tx_buffered_ms(self):
        return self.tx.buffered_ms()

    def clear_tx(self):
        self.tx.clear()


def create_audio(cfg, on_rx):
    backend = cfg["audio"]["backend"]
    if backend == "mock":
        return MockAudio(cfg, on_rx)
    if backend == "sounddevice":
        return SoundDeviceAudio(cfg, on_rx)
    raise SystemExit(f"config: unknown audio.backend {backend!r}")
