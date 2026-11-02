"""Radio control.

Two kinds of radio, chosen with `radio.type` in config.yaml:

  buttons  An external radio (IC-F621 etc.) through the Radio Remote HAT or loose optocouplers:
           PTT and channel UP/DOWN are "pressed" on the mic lines. There's no way to read the
           channel back, so it's tracked here in software; if it drifts (someone pressed a button
           on the radio), "sync" tells the server the true channel.
  sa818    The SA818S HAT: a built-in transceiver set over its serial port (see sa818.py).

Both share PTT handling, the busy input, the channel list and remembering the last channel.
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
        if not self.is_active:
            log.info("[mock gpio] %s ON", self.name)
        self.is_active = True

    def off(self):
        if self.is_active:
            log.info("[mock gpio] %s OFF", self.name)
        self.is_active = False

    def close(self):
        self.off()


def gpio_output(backend, pin, active_high, name):
    if backend == "mock":
        return _MockOutput(name)
    from gpiozero import DigitalOutputDevice

    return DigitalOutputDevice(pin, active_high=active_high, initial_value=False)


def gpio_input(backend, pin, pull_up):
    if backend == "mock" or pin is None:
        return None
    from gpiozero import DigitalInputDevice

    return DigitalInputDevice(pin, pull_up=pull_up)


class Radio:
    """Shared behaviour. Subclasses implement _tune(target) and may override run()/close()."""

    kind = "base"
    can_sync = False   # channel can be corrected without touching the radio
    can_tune = False   # arbitrary frequencies (VFO)

    def __init__(self, cfg, on_change):
        g, c = cfg["gpio"], cfg["channel"]
        self.backend = g["backend"]
        self._ptt = gpio_output(self.backend, g["ptt_pin"], g["active_high"], "PTT")
        self._busy = gpio_input(self.backend, g["busy_pin"], g["busy_pull_up"])
        self._busy_invert = bool(g.get("busy_invert", False))

        self.wrap = bool(c["wrap"])
        self._state_file = Path(cfg["server"]["state_file"])
        self.ptt_owner = None  # client id currently transmitting
        self.ptt_since = None  # time.monotonic() when keyed
        self.stepping = False  # channel change in progress
        self.error = None      # shown to operators while the radio isn't usable
        self._lock = asyncio.Lock()
        self._on_change = on_change
        self.count = 1
        self.names = {}
        self.channel = 1

    def _init_channels(self, count, names, start):
        self.count = count
        self.names = names
        self.channel = self._load_state().get("channel", start)
        if not 1 <= self.channel <= self.count:
            self.channel = start

    # ---- info -------------------------------------------------------------

    def channel_name(self, n):
        return self.names.get(n, f"CH {n}")

    def channel_list(self):
        return [{"n": n, "name": self.channel_name(n)} for n in range(1, self.count + 1)]

    def channel_info(self):
        """Extra state for the current channel (frequency etc.), or None."""
        return None

    @property
    def busy_input(self):
        """True/False from the hardware busy line, or None if not wired."""
        return None if self._busy is None else bool(self._busy.is_active) != self._busy_invert

    @property
    def rssi(self):
        """Latest signal strength reading, or None if the radio can't report one."""
        return None

    # ---- PTT --------------------------------------------------------------

    def key(self, owner):
        if self.stepping:
            raise RadioError("Channel change in progress")
        if self.error:
            raise RadioError(self.error)
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
        await self.goto(self._next(direction), _step=direction)

    async def goto(self, target, _step=None):
        if not 1 <= target <= self.count:
            raise RadioError(f"No channel {target}")
        if self.ptt_owner is not None:
            raise RadioError("Can't change channel while transmitting")
        async with self._lock:
            if target == self.channel and _step is None and not self._needs_retune():
                return
            self.stepping = True
            self._changed()
            try:
                await self._tune(target)
            finally:
                self.stepping = False
                self._save()
                self._changed()

    def _needs_retune(self):
        return False

    async def _tune(self, target):
        raise NotImplementedError

    def sync(self, channel):
        raise RadioError("This radio's channel can't be corrected by hand")

    def _next(self, direction):
        n = self.channel + direction
        if self.wrap:
            return (n - 1) % self.count + 1
        return max(1, min(self.count, n))

    # ---- lifecycle --------------------------------------------------------

    async def run(self):
        """Long-running background work (polling etc.). Default: nothing."""

    def close(self):
        self.unkey("shutdown")
        for dev in (self._ptt, self._busy):
            if dev is not None:
                dev.close()

    # ---- persistence ------------------------------------------------------

    def _load_state(self):
        try:
            data = json.loads(self._state_file.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _state(self):
        return {"channel": self.channel}

    def _save(self):
        try:
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state()))
            tmp.replace(self._state_file)
        except OSError as e:
            log.warning("could not save state: %s", e)

    def _changed(self):
        if self._on_change:
            self._on_change()


class ButtonRadio(Radio):
    """External radio: channel UP/DOWN button presses on the mic lines (Radio Remote HAT)."""

    kind = "buttons"
    can_sync = True

    def __init__(self, cfg, on_change):
        super().__init__(cfg, on_change)
        g, c = cfg["gpio"], cfg["channel"]
        self._up = gpio_output(self.backend, g["up_pin"], g["active_high"], "UP")
        self._down = gpio_output(self.backend, g["down_pin"], g["active_high"], "DOWN")
        self.press_s = c["press_ms"] / 1000
        self.gap_s = c["gap_ms"] / 1000
        names = {int(k): str(v) for k, v in (c.get("names") or {}).items()}
        self._init_channels(int(c["count"]), names, int(c["start"]))

    async def _tune(self, target):
        # The shortest way round (or the direction asked for, for a single step).
        diff = target - self.channel
        if diff == 0:
            return
        if self.wrap:
            up = diff % self.count
            down = self.count - up
            direction, times = (1, up) if up <= down else (-1, down)
        else:
            direction, times = (1 if diff > 0 else -1), abs(diff)
        line = self._up if direction > 0 else self._down
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

    def sync(self, channel):
        """Tell the server which channel the radio is really on (no button presses)."""
        if not 1 <= channel <= self.count:
            raise RadioError(f"No channel {channel}")
        self.channel = channel
        self._save()
        self._changed()

    def close(self):
        super().close()
        self._up.close()
        self._down.close()


def create_radio(cfg, on_change):
    kind = cfg["radio"]["type"]
    if kind == "sa818":
        from .sa818 import SA818Radio

        return SA818Radio(cfg, on_change)
    return ButtonRadio(cfg, on_change)
