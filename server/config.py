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
        "tx_bass_db": 6,          # tone control for transmitted voice and clips (not selcall tones):
        "tx_bass_hz": 400,        #   bass shelf: boost below this frequency, in dB
        "tx_treble_db": -3,       #   treble shelf: cut (negative) or boost above this frequency
        "tx_treble_hz": 2500,
        "busy_threshold_dbfs": -45,
    },
    "selcall": {
        "enabled": True,
        "standard": "ccir",       # ccir, zvei1 or eea (see server/selcall.py)
        "tone_ms": None,          # tone length; null = the standard's (ccir 100, zvei1 70, eea 40)
        "my_id": None,            # this station's selcall; incoming calls to it are flagged
        "tx_delay_ms": 300,       # keyed silence before the tones while the transmitter comes up
        "level": 0.3,             # tone amplitude 0-1, before audio.tx_gain
        "decode": True,           # watch received audio for selcalls
        "decode_min_digits": 3,   # shorter sequences are ignored (guards against chance matches)
        "presets": [],            # quick-dial list: [{name: Base, digits: "12345"}]
    },
    "clips": {
        "enabled": True,
        "dir": "clips",           # where uploaded clips are kept (relative to the config file)
        "max_seconds": 60,        # longest clip accepted; must fit inside ptt.max_tx_seconds
        "max_files": 50,
        "tx_delay_ms": 300,       # keyed silence before the clip while the transmitter comes up
        "level": 0.5,             # every clip is played with its peak at this level, before audio.tx_gain
    },
    "record": {
        "enabled": True,          # the orange button records received audio into the clip list
        "max_minutes": 15,        # each recording is split into parts this long
        "keep_mb": 2000,          # oldest recordings are deleted beyond this (about 17 hours)
        "min_free_mb": 300,       # recording stops if the disk gets this full
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
    for key in ("tx_bass_db", "tx_treble_db"):
        if not -20 <= float(a[key]) <= 20:
            raise SystemExit(f"config: audio.{key} must be between -20 and 20 (0 = flat)")
    if not 100 <= float(a["tx_bass_hz"]) <= 1500 or not 1000 <= float(a["tx_treble_hz"]) <= 6000:
        raise SystemExit("config: audio.tx_bass_hz must be 100-1500 and audio.tx_treble_hz 1000-6000")
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
    _check_clips(cfg["clips"], cfg["ptt"])
    _check_record(cfg["record"])

    # Resolve the state file and clip folder relative to the config file.
    for section, key in (("server", "state_file"), ("clips", "dir")):
        p = Path(cfg[section][key])
        if not p.is_absolute():
            p = path.resolve().parent / p
        cfg[section][key] = str(p)
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
    lo, hi = selcall.TONE_MS_RANGE
    if sc["tone_ms"] is not None and not lo <= int(sc["tone_ms"]) <= hi:
        raise SystemExit(f"config: selcall.tone_ms must be {lo}-{hi}, or null for the standard's own")
    if not 0 < float(sc["level"]) <= 1:
        raise SystemExit("config: selcall.level must be between 0 and 1")
    if not 0 <= int(sc["tx_delay_ms"]) <= 3000:
        raise SystemExit("config: selcall.tx_delay_ms must be 0-3000")
    if not 1 <= int(sc["decode_min_digits"]) <= selcall.MAX_DIGITS:
        raise SystemExit(f"config: selcall.decode_min_digits must be 1-{selcall.MAX_DIGITS}")


def _check_clips(cl, ptt):
    """Validate the clips section in place."""
    if not cl["enabled"]:
        return
    cl["max_seconds"] = float(cl["max_seconds"])
    room = float(ptt["max_tx_seconds"]) - int(cl["tx_delay_ms"]) / 1000 - 2
    if not 1 <= cl["max_seconds"] <= room:
        raise SystemExit(f"config: clips.max_seconds must be 1-{room:g} "
                         "(clips have to finish inside ptt.max_tx_seconds)")
    if not 1 <= int(cl["max_files"]) <= 500:
        raise SystemExit("config: clips.max_files must be 1-500")
    if not 0 <= int(cl["tx_delay_ms"]) <= 3000:
        raise SystemExit("config: clips.tx_delay_ms must be 0-3000")
    if not 0 < float(cl["level"]) <= 1:
        raise SystemExit("config: clips.level must be between 0 and 1")


def _check_record(rec):
    if not 0.1 <= float(rec["max_minutes"]) <= 120:
        raise SystemExit("config: record.max_minutes must be 0.1-120")
    if float(rec["keep_mb"]) < 50:
        raise SystemExit("config: record.keep_mb must be at least 50")
    if float(rec["min_free_mb"]) < 0:
        raise SystemExit("config: record.min_free_mb can't be negative")
