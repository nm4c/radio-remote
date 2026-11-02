"""Configuration loading with defaults."""

import copy
from pathlib import Path

import yaml

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

    # Resolve the state file relative to the config file.
    state = Path(cfg["server"]["state_file"])
    if not state.is_absolute():
        state = path.resolve().parent / state
    cfg["server"]["state_file"] = str(state)
    return cfg
