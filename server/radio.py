"""Radio control: PTT and channel UP/DOWN via GPIO-driven optocouplers.

With mic-line button emulation there's no way to read the channel back, so the current
channel is tracked here in software. If it ever drifts (someone presses a
button on the real radio), use "sync" to tell the server the true channel.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("radio")


class RadioError(Exception):
    """An operator request that can't be carried out right now."""


class _MockOutput:
    def __init__(self, name):
        self.name = name
        self.is_active = False

    def on(self):
        self.is_active = True
        log.info("[mock gpio] %s ON", self.name)

    def off(self):
        if self.is_active:
            log.info("[mock gpio] %s OFF", self.name)
        self.is_active = False

    def close(self):
        self.off()


def _output(backend, pin, active_high, name):
    if backend == "mock":
        return _MockOutput(name)
    from gpiozero import DigitalOutputDevice

    return DigitalOutputDevice(pin, active_high=active_high, initial_value=False)


def _input(backend, pin, pull_up):
    if backend == "mock" or pin is None:
        return None
    from gpiozero import DigitalInputDevice

    return DigitalInputDevice(pin, pull_up=pull_up)


class Radio:
    def __init__(self, cfg, on_change):
        g, c = cfg["gpio"], cfg["channel"]
        backend = g["backend"]
        self._ptt = _output(backend, g["ptt_pin"], g["active_high"], "PTT")
        self._up = _output(backend, g["up_pin"], g["active_high"], "UP")
        self._down = _output(backend, g["down_pin"], g["active_high"], "DOWN")
        self._busy = _input(backend, g["busy_pin"], g["busy_pull_up"])

        self.count = int(c["count"])
        self.wrap = bool(c["wrap"])
        self.press_s = c["press_ms"] / 1000
        self.gap_s = c["gap_ms"] / 1000
        self.names = {int(k): str(v) for k, v in (c.get("names") or {}).items()}

        self._state_file = Path(cfg["server"]["state_file"])
        self.channel = self._load_channel(int(c["start"]))

        self.ptt_owner = None  # client id currently transmitting
        self.ptt_since = None  # time.monotonic() when keyed
        self.stepping = False
        self._lock = asyncio.Lock()
        self._on_change = on_change

    # ---- info -------------------------------------------------------------

    def channel_name(self, n):
        return self.names.get(n, f"CH {n}")

    def channel_list(self):
        return [{"n": n, "name": self.channel_name(n)} for n in range(1, self.count + 1)]

    @property
    def busy_input(self):
        """True/False from the hardware busy line, or None if not wired."""
        return None if self._busy is None else bool(self._busy.is_active)

    # ---- PTT --------------------------------------------------------------

    def key(self, owner):
        if self.stepping:
            raise RadioError("Channel change in progress")
        if self.ptt_owner is not None and self.ptt_owner != owner:
            raise RadioError("Radio is in use by another operator")
        if self.ptt_owner is None:
            self._ptt.on()
            self.ptt_owner = owner
            self.ptt_since = time.monotonic()
            log.info("PTT ON (client %s)", owner)
            self._changed()

    def unkey(self, reason=""):
        if self.ptt_owner is None:
            return
        self._ptt.off()
        log.info("PTT OFF (client %s)%s", self.ptt_owner, f" - {reason}" if reason else "")
        self.ptt_owner = None
        self.ptt_since = None
        self._changed()

    # ---- channels ---------------------------------------------------------

    async def step(self, direction):
        async with self._lock:
            await self._press(direction, 1)

    async def goto(self, target):
        if not 1 <= target <= self.count:
            raise RadioError(f"No channel {target}")
        async with self._lock:
            diff = target - self.channel
            if diff == 0:
                return
            if self.wrap:
                up = diff % self.count
                down = self.count - up
                await (self._press(1, up) if up <= down else self._press(-1, down))
            else:
                await self._press(1 if diff > 0 else -1, abs(diff))

    def sync(self, channel):
        """Tell the server which channel the radio is really on (no button presses)."""
        if not 1 <= channel <= self.count:
            raise RadioError(f"No channel {channel}")
        self.channel = channel
        self._save()
        self._changed()

    async def _press(self, direction, times):
        if self.ptt_owner is not None:
            raise RadioError("Can't change channel while transmitting")
        line = self._up if direction > 0 else self._down
        self.stepping = True
        self._changed()
        try:
            for _ in range(times):
                nxt = self._next(direction)
                if nxt == self.channel:
                    break  # at the end and no wrap
                line.on()
                try:
                    await asyncio.sleep(self.press_s)
                finally:
                    line.off()
                self.channel = nxt
                self._changed()
                await asyncio.sleep(self.gap_s)
        finally:
            self.stepping = False
            self._save()
            self._changed()

    def _next(self, direction):
        n = self.channel + direction
        if self.wrap:
            return (n - 1) % self.count + 1
        return max(1, min(self.count, n))

    # ---- persistence / lifecycle -----------------------------------------

    def _load_channel(self, default):
        try:
            ch = int(json.loads(self._state_file.read_text())["channel"])
            if 1 <= ch <= self.count:
                return ch
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return default

    def _save(self):
        try:
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"channel": self.channel}))
            tmp.replace(self._state_file)
        except OSError as e:
            log.warning("could not save state: %s", e)

    def _changed(self):
        if self._on_change:
            self._on_change()

    def close(self):
        self.unkey("shutdown")
        for dev in (self._ptt, self._up, self._down, self._busy):
            if dev is not None:
                dev.close()
