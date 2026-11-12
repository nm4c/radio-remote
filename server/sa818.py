"""SA818S built-in transceiver (the RadPi SA818 HAT).

The module is set over its serial port (9600 8N1, NiceRF SA818 programming manual):
  AT+DMOCONNECT                       handshake           -> +DMOCONNECT:0
  AT+DMOSETGROUP=bw,tx,rx,txt,sq,rxt  frequency/tones     -> +DMOSETGROUP:0   (0 = ok)
  AT+DMOSETVOLUME=1..8                audio output level  -> +DMOSETVOLUME:0
  AT+SETFILTER=emph,hpf,lpf           1 = bypass          -> +DMOSETFILTER:0
  AT+SETTAIL=0/1                      squelch tail tone   -> +DMOSETTAIL:0
  RSSI?                               signal strength     -> RSSI=0..255 (1 dB steps)
  AT+VERSION                                              -> +VERSION:...

GPIO on the HAT: PTT (Q1, high = transmit), PD (high = on), H/L (Q2, high = low power),
AudioON as the busy input (low = receiving audio).
"""

import asyncio
import logging
import random
import re
import time

from .radio import Radio, RadioError, gpio_output

log = logging.getLogger("sa818")

# The module's CTCSS codes 0001-0038, in order.
CTCSS = [67.0, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2,
         110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 162.2, 167.9,
         173.8, 179.9, 186.2, 192.8, 203.5, 210.7, 218.1, 225.7, 233.6, 241.8, 250.3]


def tone_code(tone):
    """Config tone -> module code. None/0/'' = off, 91.5 = CTCSS, 'D023N' / 'D023I' = DCS."""
    if tone in (None, 0, "", "off", "none"):
        return "0000"
    if isinstance(tone, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(tone)):
        hz = float(tone)
        for i, t in enumerate(CTCSS, start=1):
            if abs(t - hz) < 0.05:
                return f"{i:04d}"
        raise ValueError(f"CTCSS tone {hz} Hz isn't one the SA818 supports")
    m = re.fullmatch(r"D?(\d{3})([NI])", str(tone).strip().upper())
    if m:
        return m.group(1) + m.group(2)
    raise ValueError(f"tone {tone!r}: use Hz (e.g. 91.5), D023N / D023I, or leave empty for none")


def tone_label(tone):
    code = tone_code(tone)
    if code == "0000":
        return None
    if code[-1] in "NI":
        return "D" + code
    return f"{CTCSS[int(code) - 1]:.1f}"


class _MockLink:
    """Answers like a module, for development without the hardware."""

    def __init__(self):
        self._rssi = 40

    def command(self, cmd, expect, timeout=1.0):
        time.sleep(0.05)
        log.debug("[mock sa818] > %s", cmd)
        if cmd == "RSSI?":
            self._rssi = max(0, min(255, self._rssi + random.randint(-6, 6)))
            return f"RSSI={self._rssi}"
        if cmd == "AT+VERSION":
            return "+VERSION:SA818_MOCK"
        return f"{expect}0"

    def close(self):
        pass


class _SerialLink:
    def __init__(self, port, baud):
        import serial  # pyserial

        self._s = serial.Serial(port, baud, timeout=0.1)

    def command(self, cmd, expect, timeout=1.0):
        """Send one command and return the reply line starting with `expect` (None on timeout)."""
        self._s.reset_input_buffer()
        self._s.write((cmd + "\r\n").encode("ascii"))
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            buf += self._s.read(64)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("ascii", "replace").strip()
                if text.startswith(expect):
                    return text
                if text:
                    log.debug("ignored %r while waiting for %s", text, expect)
        return None

    def close(self):
        self._s.close()


class SA818Radio(Radio):
    kind = "sa818"
    can_tune = True

    def __init__(self, cfg, on_change):
        super().__init__(cfg, on_change)
        s, c = cfg["sa818"], cfg["channel"]
        self.cfg = s
        self.band = (float(s["band"][0]), float(s["band"][1]))
        self._pd = gpio_output(self.backend, s["pd_pin"], True, "SA818 PD")
        self._low = gpio_output(self.backend, s["low_power_pin"], True, "SA818 LOW POWER")
        self._link = None
        self._io = asyncio.Lock()   # one serial command at a time
        self._rssi = None
        self._ready = False
        self.version = None
        self.error = "Radio module starting"

        self.channels = [self._channel(i, ch) for i, ch in enumerate(c.get("list") or [], start=1)]
        if not self.channels:
            raise SystemExit("config: radio.type sa818 needs a channel.list with at least one frequency")
        names = {n: ch["name"] for n, ch in enumerate(self.channels, start=1)}
        self._init_channels(len(self.channels), names, min(int(c["start"]), len(self.channels)))
        vfo = self._load_state().get("vfo")
        self.vfo = None
        if vfo:
            try:
                self.vfo = self._channel(0, vfo)
            except SystemExit:
                pass
        if self._load_state().get("channel") == 0 and self.vfo:
            self.channel = 0
        self.active = None  # settings the module was last given

    # ---- configuration ----------------------------------------------------

    def _channel(self, n, ch):
        where = f"channel.list[{n}]" if n else "VFO"
        try:
            rx = float(ch["freq"])
            tx = float(ch.get("tx_freq") or rx)
            rx_tone = ch.get("rx_tone", ch.get("tone"))
            tx_tone = ch.get("tx_tone", ch.get("tone"))
            tone_code(rx_tone), tone_code(tx_tone)
        except (KeyError, TypeError, ValueError) as e:
            raise SystemExit(f"config: {where}: {e}")
        for f in (rx, tx):
            if not self.band[0] <= f <= self.band[1]:
                raise SystemExit(f"config: {where}: {f} MHz is outside sa818.band {self.band}")
        bw = str(ch.get("bandwidth", self.cfg["bandwidth"])).lower()
        if bw not in ("narrow", "wide"):
            raise SystemExit(f"config: {where}: bandwidth must be narrow or wide")
        sq = int(ch.get("squelch", self.cfg["squelch"]))
        if not 0 <= sq <= 8:
            raise SystemExit(f"config: {where}: squelch must be 0-8")
        return {
            "name": str(ch.get("name") or f"{rx:.4f}"),
            "freq": rx, "tx_freq": tx, "rx_tone": rx_tone, "tx_tone": tx_tone,
            "bandwidth": bw, "squelch": sq,
            "low_power": bool(ch.get("low_power", self.cfg["low_power"])),
        }

    def _current(self):
        return self.vfo if self.channel == 0 else self.channels[self.channel - 1]

    # ---- info -------------------------------------------------------------

    def channel_name(self, n):
        if n == 0:
            return "VFO"
        return super().channel_name(n)

    def channel_list(self):
        return [{"n": n, "name": ch["name"], "freq": f"{ch['freq']:.4f}"}
                for n, ch in enumerate(self.channels, start=1)]

    def channel_info(self):
        ch = self._current()
        return {
            "rx": f"{ch['freq']:.4f}",
            "tx": f"{ch['tx_freq']:.4f}",
            "rx_tone": tone_label(ch["rx_tone"]),
            "tx_tone": tone_label(ch["tx_tone"]),
            "bandwidth": ch["bandwidth"],
            "low_power": ch["low_power"],
        }

    @property
    def rssi(self):
        return self._rssi

    # ---- channels / VFO ---------------------------------------------------

    def _next(self, direction):
        if self.channel == 0:  # leaving the VFO: back to the channel list
            return 1 if direction > 0 else self.count
        return super()._next(direction)

    def _needs_retune(self):
        return self.active != self._current()

    async def tune(self, freq, tx_freq=None, tone=None):
        """Go to an arbitrary simplex/duplex frequency (the VFO)."""
        try:
            vfo = self._channel(0, {"freq": freq, "tx_freq": tx_freq, "tone": tone})
        except SystemExit as e:
            raise RadioError(str(e).removeprefix("config: VFO: "))
        if self.ptt_owner is not None:
            raise RadioError("Can't change frequency while transmitting")
        async with self._lock:
            self.stepping = True
            self._changed()
            old = (self.vfo, self.channel)
            self.vfo, self.channel = vfo, 0
            try:
                await self._apply()
            except RadioError:
                self.vfo, self.channel = old
                raise
            finally:
                self.stepping = False
                self._save()
                self._changed()

    async def _tune(self, target):
        old = self.channel
        self.channel = target
        try:
            await self._apply()
        except RadioError:
            self.channel = old
            raise

    async def _apply(self):
        ch = self._current()
        if not self._ready:
            raise RadioError(self.error or "Radio module not ready")
        cmd = "AT+DMOSETGROUP={},{:.4f},{:.4f},{},{},{}".format(
            1 if ch["bandwidth"] == "wide" else 0, ch["tx_freq"], ch["freq"],
            tone_code(ch["tx_tone"]), ch["squelch"], tone_code(ch["rx_tone"]))
        reply = await self._cmd(cmd, "+DMOSETGROUP:")
        if reply != "+DMOSETGROUP:0":
            raise RadioError(f"Module refused the settings ({reply or 'no reply'})")
        (self._low.on if ch["low_power"] else self._low.off)()
        self.active = ch
        log.info("tuned: %s rx %.4f tx %.4f", ch["name"], ch["freq"], ch["tx_freq"])

    # ---- serial -----------------------------------------------------------

    async def _cmd(self, cmd, expect, timeout=1.0):
        async with self._io:
            return await asyncio.to_thread(self._link.command, cmd, expect, timeout)

    async def _connect(self):
        s = self.cfg
        if self._link is None:
            self._link = _MockLink() if self.backend == "mock" else _SerialLink(s["port"], int(s["baud"]))
        self._pd.on()
        await asyncio.sleep(0.5)  # module start-up
        for _ in range(3):
            if await self._cmd("AT+DMOCONNECT", "+DMOCONNECT:") == "+DMOCONNECT:0":
                break
        else:
            raise RadioError(f"No answer from the SA818 on {s['port']}")
        reply = await self._cmd("AT+VERSION", "+VERSION:")
        self.version = reply.split(":", 1)[1].strip() if reply else None
        f = s["filters"]
        setup = [
            (f"AT+DMOSETVOLUME={int(s['volume'])}", "+DMOSETVOLUME:"),
            ("AT+SETFILTER={},{},{}".format(*(0 if f[k] else 1 for k in ("emphasis", "highpass", "lowpass"))),
             "+DMOSETFILTER:"),
            (f"AT+SETTAIL={1 if s['tail_tone'] else 0}", "+DMOSETTAIL:"),
        ]
        for cmd, expect in setup:
            reply = await self._cmd(cmd, expect)
            if not (reply and reply.endswith("0")):
                log.warning("%s -> %s", cmd, reply or "no reply")
        self._ready = True
        self.active = None
        await self._apply()
        log.info("SA818 ready (%s)", self.version or "version unknown")

    async def run(self):
        """Bring the module up, keep it set, and poll RSSI while receiving."""
        period = max(0.2, self.cfg["rssi_ms"] / 1000)
        while True:
            try:
                if not self._ready:
                    async with self._lock:
                        await self._connect()
                    self.error = None
                    self._changed()
                if self.ptt_owner is None and not self.stepping:
                    reply = await self._cmd("RSSI?", "RSSI=", timeout=0.5)
                    if reply:
                        self._rssi = int(reply.split("=", 1)[1])
                await asyncio.sleep(period)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # serial port missing, module silent, bad reply...
                msg = str(e) if isinstance(e, RadioError) else f"Radio module error: {e}"
                if msg != self.error:
                    log.error("%s", msg)
                self._ready = False
                self._rssi = None
                self.error = msg
                self._changed()
                await asyncio.sleep(5)

    # ---- persistence / lifecycle -----------------------------------------

    def _state(self):
        st = {"channel": self.channel}
        if self.vfo:
            st["vfo"] = {"freq": self.vfo["freq"], "tx_freq": self.vfo["tx_freq"], "tone": self.vfo["tx_tone"]}
        return st

    def close(self):
        super().close()
        self._low.close()
        self._pd.close()  # module back to sleep
        if self._link:
            self._link.close()
