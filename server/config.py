"""Configuration loading with defaults."""

import copy
from pathlib import Path

import yaml

from . import selcall

DEFAULTS = {
    "radio": {
        "name": "Radio",
        "type": "buttons",   # buttons = external radio via the RadPi Control HAT, sa818 = RadPi SA818 HAT
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "password": "",
        "tls_cert": None,
        "tls_key": None,
        "state_file": "state.json",
        "db_file": "radio.db",      # operator accounts, sessions and the audit log (SQLite)
        "allowed_origins": [],      # extra page origins allowed to open the WebSocket (e.g. a proxy's)
        "session_days": 30,         # "keep me logged in" lasts this long after last use
        "audit_days": 365,          # audit log entries older than this are deleted
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
    "voice_fx": {
        "enabled": True,          # voice effects (Vader, Dalek...) for live voice, switched with P / P1
    },
    "record": {
        "enabled": True,          # the orange button records received audio into the clip list
        "max_minutes": 15,        # each recording is split into parts this long
        "keep_mb": 2000,          # oldest recordings are deleted beyond this (about 17 hours)
        "min_free_mb": 300,       # recording stops if the disk gets this full
        "mode": "continuous",     # default when recording starts: continuous, or vox (only when active)
        "include_tx": True,       # while we transmit, record what we send (the receiver is muted then)
        "vox_hang_ms": 2000,      # vox: keep recording this long after the channel goes quiet
        "vox_preroll_ms": 500,    # vox: and include this much from before it got busy
        "vox_gap_ms": 300,        # vox: silence put between overs
        "vox_close_idle_minutes": 10,  # vox: finish the part (so it's listed) after this long quiet
    },
    "webrtc": {
        "enabled": True,
        "ice_servers": [],
        "rx_jitter_packets": 4,
        "release_ms": 120,
        "tx_prebuffer_ms": 40,
        "tx_max_ms": 200,
    },
    # Say: type text in the web page, pick a voice, and it's spoken on air (server/tts.py). Off
    # by default; the page shows the panel only when it's on. See the tts: section of
    # config.example.yaml for Australian voices.
    "tts": {
        "enabled": False,
        "voices": [],               # [{name, backend, voice, speed}]; the first is the default
        "max_chars": 600,           # longest text accepted (also capped by ptt.max_tx_seconds)
        "level": 0.5,               # peak level (audio.tx_gain applies on top)
        "tx_delay_ms": 300,         # keyed silence before speaking, while the transmitter comes up
        "pronounce": {"RadPi": "Rad Pie"},  # text replacements before speaking
        "azure": {"api_key": None, "region": None},        # region e.g. australiaeast
        "google": {"api_key": None},
        "elevenlabs": {"api_key": None, "model": None},    # model null = eleven_flash_v2_5
    },
    # The AI agent (server/bot/). Off by default: with enabled false nothing of it is loaded and
    # the web page shows no AI panel. See the bot: section of config.example.yaml.
    "bot": {
        "enabled": False,
        "name": "RadPi",            # what it calls itself on air; also always a wake word
        "dir": "bot",               # people, notes, logs and over audio (relative to this file)
        "mode": "listen",           # at start-up: listen (never transmits), approve, or auto
        "persona": None,            # persona prompt file; null = server/bot/prompts/banter.md
        "reply_policy": "llm",      # llm (it decides), always, or addressed (only when named)
        "wake_words": [],           # more names that count as addressing it
        "learn_anonymous": True,    # give unknown voices a pseudonym and remember them
        "learn_notes": True,        # let the LLM keep notes on people and pick up their names
        "tones": True,              # let it raise its voice or shout when the moment calls for it
        "opt_out_phrases": [],      # e.g. ["forget me"]: deletes the speaker's profile. Empty = off
        "context_overs": 12,        # recent overs shown to the LLM
        "min_over_s": 0.8,          # shorter overs (kerchunks) are ignored
        "max_over_s": 60,           # longer ones are cut into parts
        "preroll_ms": 300,          # audio kept from before the channel went busy
        "hang_ms": 700,             # an over ends after the channel is quiet this long
        "reply_gap_ms": 1500,       # the channel must be quiet this long before it keys up
        "reply_wait_s": 20,         # give up on a reply if the channel doesn't go quiet
        "drop_stale": True,         # drop a reply if someone else has spoken since
        "max_reply_s": 30,          # longest reply on air (also capped by ptt.max_tx_seconds)
        "max_replies_per_hour": None,  # null = no limit
        "min_reply_gap_s": 0,       # seconds between its transmissions
        "approve_timeout_s": 60,    # approve mode: a reply nobody approves is dropped after this
        "tx_delay_ms": 300,         # keyed silence before speaking, while the transmitter comes up
        "level": 0.5,               # peak level of its voice (audio.tx_gain applies on top)
        "keep_audio": True,         # save each over's audio with the log
        "retention_days": None,     # delete logs and audio older than this; null = keep
        "pronounce": {"RadPi": "Rad Pie"},  # text replacements before speaking
        "stt": {
            "backend": "faster_whisper",  # faster_whisper, openai (any compatible server), or mock
            "model": "base.en",
            "language": "en",
            "compute_type": "int8",
            "beam_size": 1,
            "url": None,                  # openai: e.g. http://127.0.0.1:8000/v1
            "api_key": None,
            "prompt": None,               # vocabulary hint (names, callsigns, local places)
        },
        "speaker": {
            "backend": "ecapa",           # ecapa (SpeechBrain), or basic (numpy, rough; for testing)
            "match": None,                # similarity to count as the same person; null = backend default
            "update": None,               # above this, the over also refines their voiceprint
            "new": None,                  # below this it's a new caller; between new and match, not sure
            "merge": None,                # anonymous callers this alike are merged into one
            "min_enrol_s": 2.5,           # shortest over that can start a new voiceprint
            "max_samples": 30,            # voiceprint samples kept per person
            "model_dir": None,            # where the ecapa model is cached; null = <dir>/models
        },
        "llm": {
            "backend": "ollama",          # ollama (also hailo-ollama), openai (llama.cpp, cloud...), mock
            "url": "http://127.0.0.1:11434",
            "model": "qwen2.5:1.5b",
            "api_key": None,
            "temperature": 0.9,
            "max_tokens": 160,
            "timeout_s": 60,
        },
        "tts": {
            "backend": "piper",           # piper, azure, google, elevenlabs, or mock
            "voice": None,                # piper: .onnx path; azure/google: voice name; elevenlabs: voice id
            "model": None,                # elevenlabs: model id (null = eleven_flash_v2_5)
            "api_key": None,
            "region": None,               # azure: e.g. australiaeast
            "speed": 1.0,
        },
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

    srv = cfg["server"]
    if not 1 <= float(srv["session_days"]) <= 365:
        raise SystemExit("config: server.session_days must be 1-365")
    if float(srv["audit_days"]) < 0:
        raise SystemExit("config: server.audit_days can't be negative (0 = keep forever)")
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
    _check_tts(cfg["tts"], cfg["ptt"])
    # The agent's voice can use the Say panel's cloud keys instead of repeating them.
    bt = cfg["bot"]["tts"]
    keys = cfg["tts"].get(bt["backend"]) if bt["backend"] in ("azure", "google", "elevenlabs") else None
    if keys:
        bt["api_key"] = bt["api_key"] or keys.get("api_key")
        bt["region"] = bt["region"] or keys.get("region")
    _check_bot(cfg["bot"], cfg["ptt"])

    # Resolve the state file and clip folder relative to the config file.
    for section, key in (("server", "state_file"), ("server", "db_file"), ("clips", "dir"), ("bot", "dir")):
        p = Path(cfg[section][key])
        if not p.is_absolute():
            p = path.resolve().parent / p
        cfg[section][key] = str(p)
    bot = cfg["bot"]
    # persona: a bare name picks a built-in prompt (server/bot/prompts/<name>.md), e.g. "cb".
    builtin = Path(__file__).resolve().parent / "bot" / "prompts" / f"{bot.get('persona')}.md"
    if bot.get("persona") and "/" not in bot["persona"] and builtin.is_file():
        bot["persona"] = str(builtin)
    for sub, key in (("tts", "voice"), (None, "persona")):
        holder = bot[sub] if sub else bot
        v = holder.get(key)
        if v and (sub is None or bot["tts"]["backend"] == "piper") and not Path(v).is_absolute():
            holder[key] = str(path.resolve().parent / v)
    for v in cfg["tts"]["voices"] if cfg["tts"]["enabled"] else ():
        if v["backend"] == "piper" and not Path(v["voice"]).is_absolute():
            v["voice"] = str(path.resolve().parent / v["voice"])
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


TTS_BACKENDS = ("piper", "azure", "google", "elevenlabs", "mock")


def _check_tts(t, ptt):
    """Validate the tts (Say panel) section in place. Only checked when it's switched on."""
    if not t["enabled"]:
        return
    voices = t["voices"]
    if not isinstance(voices, list) or not voices:
        raise SystemExit("config: tts.voices needs at least one voice (see config.example.yaml)")
    names = set()
    for i, v in enumerate(voices):
        if not isinstance(v, dict) or not str(v.get("name") or "").strip():
            raise SystemExit(f"config: tts.voices[{i}] needs a name")
        v["name"] = str(v["name"]).strip()[:40]
        if v["name"] in names:
            raise SystemExit(f"config: tts.voices: {v['name']!r} is listed twice")
        names.add(v["name"])
        b = v.get("backend")
        if b not in TTS_BACKENDS:
            raise SystemExit(f"config: tts voice {v['name']!r}: backend must be one of {', '.join(TTS_BACKENDS)}")
        if b != "mock" and not v.get("voice"):
            raise SystemExit(f"config: tts voice {v['name']!r} needs voice: "
                             + ("the path of a Piper .onnx voice" if b == "piper" else "the backend's voice name or id"))
        if not 0.5 <= float(v.get("speed") or 1.0) <= 2:
            raise SystemExit(f"config: tts voice {v['name']!r}: speed must be 0.5-2")
        keys = t.get(b) or {}
        if b in ("azure", "google", "elevenlabs") and not keys.get("api_key"):
            raise SystemExit(f"config: tts.{b}.api_key is needed for the voice {v['name']!r}")
        if b == "azure" and not keys.get("region"):
            raise SystemExit("config: tts.azure.region is needed (e.g. australiaeast)")
    if not 20 <= int(t["max_chars"]) <= 5000:
        raise SystemExit("config: tts.max_chars must be 20-5000")
    if not 0 < float(t["level"]) <= 1:
        raise SystemExit("config: tts.level must be between 0 and 1")
    if not 0 <= int(t["tx_delay_ms"]) <= 3000:
        raise SystemExit("config: tts.tx_delay_ms must be 0-3000")
    if float(ptt["max_tx_seconds"]) - int(t["tx_delay_ms"]) / 1000 - 2 < 2:
        raise SystemExit("config: ptt.max_tx_seconds is too short for tts")


def _check_bot(b, ptt):
    """Validate the bot section in place. Only checked when it's switched on."""
    if not b["enabled"]:
        return
    choices = {
        "mode": ("listen", "approve", "auto"),
        "reply_policy": ("llm", "always", "addressed"),
    }
    for key, allowed in choices.items():
        if b[key] not in allowed:
            raise SystemExit(f"config: bot.{key} must be one of {', '.join(allowed)}")
    backends = {
        "stt": ("faster_whisper", "openai", "mock"),
        "speaker": ("ecapa", "basic"),
        "llm": ("ollama", "openai", "mock"),
        "tts": TTS_BACKENDS,
    }
    for sub, allowed in backends.items():
        if b[sub]["backend"] not in allowed:
            raise SystemExit(f"config: bot.{sub}.backend must be one of {', '.join(allowed)}")
    t = b["tts"]
    if t["backend"] == "piper" and not t["voice"]:
        raise SystemExit("config: bot.tts.voice must be the path of a Piper .onnx voice")
    if t["backend"] in ("azure", "google", "elevenlabs") and not t["api_key"]:
        raise SystemExit(f"config: bot.tts.api_key is needed for {t['backend']}")
    if t["backend"] == "azure" and not t["region"]:
        raise SystemExit("config: bot.tts.region is needed for azure (e.g. australiaeast)")
    if b["stt"]["backend"] == "openai" and not b["stt"]["url"]:
        raise SystemExit("config: bot.stt.url is needed for the openai backend")
    room = float(ptt["max_tx_seconds"]) - int(b["tx_delay_ms"]) / 1000 - 2
    b["max_reply_s"] = min(float(b["max_reply_s"]), room)
    if b["max_reply_s"] < 2:
        raise SystemExit("config: bot.max_reply_s is too short (or ptt.max_tx_seconds is)")
    if not 0 < float(b["level"]) <= 1:
        raise SystemExit("config: bot.level must be between 0 and 1")
    if b["max_replies_per_hour"] is not None and int(b["max_replies_per_hour"]) < 1:
        raise SystemExit("config: bot.max_replies_per_hour must be 1 or more, or null for no limit")
    if b["retention_days"] is not None and float(b["retention_days"]) <= 0:
        raise SystemExit("config: bot.retention_days must be more than 0, or null to keep everything")
    if not 0.2 <= float(b["min_over_s"]) < float(b["max_over_s"]) <= 300:
        raise SystemExit("config: bot.min_over_s and max_over_s must be 0.2-300 s, min below max")


def _check_record(rec):
    if not 0.1 <= float(rec["max_minutes"]) <= 120:
        raise SystemExit("config: record.max_minutes must be 0.1-120")
    if rec["mode"] not in ("continuous", "vox"):
        raise SystemExit("config: record.mode must be continuous or vox")
    for key, hi in (("vox_hang_ms", 30000), ("vox_preroll_ms", 5000), ("vox_gap_ms", 5000)):
        if not 0 <= int(rec[key]) <= hi:
            raise SystemExit(f"config: record.{key} must be 0-{hi}")
    if not 0.5 <= float(rec["vox_close_idle_minutes"]) <= 120:
        raise SystemExit("config: record.vox_close_idle_minutes must be 0.5-120")
    if float(rec["keep_mb"]) < 50:
        raise SystemExit("config: record.keep_mb must be at least 50")
    if float(rec["min_free_mb"]) < 0:
        raise SystemExit("config: record.min_free_mb can't be negative")
