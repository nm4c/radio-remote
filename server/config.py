"""Configuration loading with defaults."""

import copy
from pathlib import Path

import yaml

from . import selcall

DEFAULTS = {
    "radio": {
        "name": "Radio",
        "type": "buttons",   # buttons = external radio via the Radio Remote HAT, sa818 = SA818S HAT
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "password": "",
        "tls_cert": None,
        "tls_key": None,
        "state_file": "state.json",
    },
    "gpio": {
        "backend": "gpiozero",
        "ptt_pin": 17,
        "up_pin": 27,
        "down_pin": 22,
        "active_high": True,
        "busy_pin": None,
        "busy_pull_up": True,
        "busy_invert": False,
    },
    "sa818": {
        "port": "/dev/serial0",
        "baud": 9600,
        "pd_pin": 27,
        "low_power_pin": 22,
        "band": [400.0, 480.0],
        "bandwidth": "narrow",
        "squelch": 4,
        "volume": 6,
        "low_power": False,
        "tail_tone": False,
        "filters": {"emphasis": True, "highpass": True, "lowpass": True},
        "rssi_ms": 500,
        "rssi_offset_db": None,
    },
    "channel": {
        "count": 16,
        "wrap": True,
        "start": 1,
        "press_ms": 120,
        "gap_ms": 250,
        "names": {},
    },
    "ptt": {
        "max_tx_seconds": 120,
        "hold_timeout_ms": 1500,
        "tail_ms": 400,
    },
    "audio": {
        "backend": "sounddevice",
        "input_device": None,
        "output_device": None,
        "device_rate": 48000,
        "wire_rate": 16000,
        "frame_ms": 20,
        "rx_gain": 1.0,
        "tx_gain": 1.0,
        "tx_prebuffer_ms": 80,
        "busy_threshold_dbfs": -45,
    },
    "selcall": {
        "enabled": True,
        "standard": "ccir",       # ccir, zvei1 or eea (see server/selcall.py)
        "my_id": None,            # this station's selcall; incoming calls to it are flagged
        "tx_delay_ms": 300,       # keyed silence before the tones while the transmitter comes up
        "level": 0.3,             # tone amplitude 0-1, before audio.tx_gain
        "decode": True,           # watch received audio for selcalls
        "decode_min_digits": 3,   # shorter sequences are ignored (guards against chance matches)
        "presets": [],            # quick-dial list: [{name: Base, digits: "12345"}]
    },
    "webrtc": {
        "enabled": True,
        "ice_servers": [],
        "rx_jitter_packets": 4,
        "release_ms": 120,
        "tx_prebuffer_ms": 40,
        "tx_max_ms": 200,
    },
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key not in ("names",):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path):
    path = Path(path)
    with path.open() as f:
        cfg = _merge(DEFAULTS, yaml.safe_load(f) or {})

    if not cfg["server"]["password"]:
        raise SystemExit("config: server.password must be set")
    a = cfg["audio"]
    if int(a["device_rate"]) % int(a["wire_rate"]):
        raise SystemExit("config: audio.device_rate must be a multiple of audio.wire_rate")
    kind = cfg["radio"]["type"]
    if kind not in ("buttons", "sa818"):
        raise SystemExit("config: radio.type must be buttons or sa818")
    if kind == "buttons" and int(cfg["channel"]["count"]) < 1:
        raise SystemExit("config: channel.count must be >= 1")
    if kind == "sa818":
        g, s = cfg["gpio"], cfg["sa818"]
        clash = {g["ptt_pin"], g["busy_pin"]} & {s["pd_pin"], s["low_power_pin"]}
        if clash:
            raise SystemExit(f"config: GPIO {sorted(clash)} used twice (gpio vs sa818 pins)")

    _check_selcall(cfg["selcall"])

    # Resolve the state file relative to the config file.
    state = Path(cfg["server"]["state_file"])
    if not state.is_absolute():
        state = path.resolve().parent / state
    cfg["server"]["state_file"] = str(state)
    return cfg


def _quoted(value, what):
    # Unquoted YAML numbers lose leading zeros, and PyYAML reads 0-prefixed ones as octal
    # (01234 -> 668), so selcall numbers must be written as strings.
    if not isinstance(value, str):
        raise ValueError(f'{what} must be in quotes, e.g. "01234", so leading zeros are kept')
    return value


def _check_selcall(sc):
    """Validate the selcall section in place."""
    if sc["standard"] not in selcall.STANDARDS:
        raise SystemExit(f"config: selcall.standard must be one of {', '.join(selcall.STANDARDS)}")
    try:
        if sc["my_id"] is not None:
            sc["my_id"] = selcall.check_digits(_quoted(sc["my_id"], "selcall.my_id"), "selcall.my_id")
        presets = []
        for i, p in enumerate(sc["presets"] or [], start=1):
            if not isinstance(p, dict):
                raise ValueError(f"selcall.presets entry {i} must have name: and digits:")
            what = f"selcall.presets entry {i} digits"
            digits = selcall.check_digits(_quoted(p.get("digits", ""), what), what)
            presets.append({"name": str(p.get("name") or digits)[:24], "digits": digits})
        sc["presets"] = presets
    except ValueError as e:
        raise SystemExit(f"config: {e}")
    if not 0 < float(sc["level"]) <= 1:
        raise SystemExit("config: selcall.level must be between 0 and 1")
    if not 0 <= int(sc["tx_delay_ms"]) <= 3000:
        raise SystemExit("config: selcall.tx_delay_ms must be 0-3000")
    if not 1 <= int(sc["decode_min_digits"]) <= selcall.MAX_DIGITS:
        raise SystemExit(f"config: selcall.decode_min_digits must be 1-{selcall.MAX_DIGITS}")
