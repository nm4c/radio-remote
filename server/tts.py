"""Text to speech, for the Say panel (tts: in config.yaml) and the AI agent's voice (bot.tts).

Backends, all returning float32 audio at the wire rate (16 kHz):
  piper       offline, on the Pi (pip install piper-tts). voice: path of a .onnx voice; its
              .onnx.json must sit beside it. Piper's own voices are American or British; an
              Australian one has to be trained (docs/voice/README.md). Faster than real time on a Pi 5.
  azure       Azure AI Speech. voice e.g. en-AU-NatashaNeural (female) or en-AU-WilliamNeural
              (male); region e.g. australiaeast; api_key is the resource key.
  google      Google Cloud Text-to-Speech. voice e.g. en-AU-Neural2-B (male) or en-AU-Neural2-C
              (female); api_key is an API key allowed to use the service.
  elevenlabs  ElevenLabs. voice: a voice id from your voice library (pick an Australian one);
              model defaults to eleven_flash_v2_5, the low-latency one.
  mock        a short warble per word, for testing without a voice.

The cloud ones need the internet (the USB modem) and take about 0.5-1.5 s per sentence.

Tone (the AI agent picks one per reply): normal, raised or shout. The cloud voices are asked to
speak faster, higher and louder (Azure and Google through prosody; ElevenLabs through capitals,
and an audio tag on its v3 model); Piper only gets the speed. Then every backend gets the same
"too close to the mic" treatment: driven into soft clipping with the presence pushed up. A radio
transmits the same peak deviation whatever happens, so that density, not more level, is what
makes it sound like someone yelling on air.
"""

import asyncio
import base64
import logging
import re
from html import escape

import aiohttp
import numpy as np

from .audio import read_wav, resample

log = logging.getLogger("tts")

TONES = ("normal", "raised", "shout")
# tone -> speed factor, pitch (semitones), extra volume (dB), drive (soft clipping), presence
TONE_SHAPE = {"normal": (1.0, 0, 0, 0, 0.0), "raised": (1.06, 1.5, 4, 2.5, 0.35),
              "shout": (1.12, 3.5, 8, 6.0, 0.7)}


def shape(x, tone):
    """The "yelling into the mic" sound: presence boost, then soft clipping. Level is left to
    the caller (it normalises the peak anyway)."""
    _, _, _, drive, presence = TONE_SHAPE.get(tone, TONE_SHAPE["normal"])
    if not drive or not len(x):
        return x
    x = x + presence * np.diff(x, prepend=x[:1]) * 4          # first-order lift of the upper mids
    x = x / (np.max(np.abs(x)) or 1)
    return (np.tanh(drive * x) / np.tanh(drive)).astype(np.float32)


def clean(text, pronounce, stage_directions=True):
    """Plain speakable text: no emoji or markdown, pronunciation fixes applied, and (for the
    LLM's replies) no stage directions."""
    if stage_directions:
        text = re.sub(r"\*[^*]{0,40}\*|\([^)]{0,40}\)|\[[^\]]{0,40}\]", " ", text)   # *grins*, (laughs), [static]
    text = re.sub(r"[^\w\s.,!?;:'\"&%$/-]", " ", text)
    for word, say in (pronounce or {}).items():
        text = re.sub(rf"\b{re.escape(word)}\b", say, text, flags=re.I)
    return " ".join(text.split())


class Piper:
    is_async = False

    def __init__(self, cfg, rate):
        from piper import PiperVoice  # optional dependency (requirements-bot.txt)
        self.rate = rate
        self.speed = float(cfg["speed"]) or 1.0
        log.info("loading Piper voice %s", cfg["voice"])
        self.voice = PiperVoice.load(cfg["voice"])
        self.src_rate = int(self.voice.config.sample_rate)

    def speak(self, text, tone="normal"):
        length = 1.0 / (self.speed * TONE_SHAPE[tone][0])
        if hasattr(self.voice, "synthesize_stream_raw"):  # piper-tts 1.2
            raw = b"".join(self.voice.synthesize_stream_raw(text, length_scale=length))
        else:                                             # piper-tts 1.3+
            from piper import SynthesisConfig
            raw = b"".join(c.audio_int16_bytes for c in self.voice.synthesize(
                text, syn_config=SynthesisConfig(length_scale=length)))
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
        return resample(x, self.src_rate, self.rate)


class Azure:
    is_async = True

    def __init__(self, cfg, rate):
        self.cfg = cfg
        self.rate = rate
        self.url = f"https://{cfg['region']}.tts.speech.microsoft.com/cognitiveservices/v1"
        self.voice = cfg["voice"] or "en-AU-NatashaNeural"

    async def speak(self, text, tone="normal"):
        speed, pitch, gain = TONE_SHAPE[tone][:3]
        rate_pct = f"{(float(self.cfg['speed']) * speed - 1) * 100:+.0f}%"
        extra = f" pitch='{pitch:+.1f}st' volume='{gain:+.0f}dB'" if tone != "normal" else ""
        ssml = (f"<speak version='1.0' xml:lang='en-AU'><voice name='{escape(self.voice)}'>"
                f"<prosody rate='{rate_pct}'{extra}>{escape(text)}</prosody></voice></speak>")
        headers = {"Ocp-Apim-Subscription-Key": self.cfg["api_key"], "Content-Type": "application/ssml+xml",
                   "X-Microsoft-OutputFormat": "raw-16khz-16bit-mono-pcm", "User-Agent": "radpi"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(self.url, data=ssml.encode(), headers=headers) as r:
                if r.status != 200:
                    raise RuntimeError(f"Azure TTS: HTTP {r.status} {(await r.text())[:200]}")
                raw = await r.read()
        return resample(np.frombuffer(raw, "<i2").astype(np.float32) / 32768, 16000, self.rate)


class Google:
    is_async = True

    def __init__(self, cfg, rate):
        self.cfg = cfg
        self.rate = rate
        self.voice = cfg["voice"] or "en-AU-Neural2-B"

    async def speak(self, text, tone="normal"):
        speed, pitch, gain = TONE_SHAPE[tone][:3]
        audio = {"audioEncoding": "LINEAR16", "sampleRateHertz": 16000,
                 "speakingRate": float(self.cfg["speed"]) * speed}
        if tone != "normal":
            audio.update(pitch=pitch, volumeGainDb=gain)
        body = {"input": {"text": text},
                "voice": {"languageCode": "-".join(self.voice.split("-")[:2]), "name": self.voice},
                "audioConfig": audio}
        url = "https://texttospeech.googleapis.com/v1/text:synthesize"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(url, json=body, headers={"X-Goog-Api-Key": self.cfg["api_key"]}) as r:
                if r.status != 200:
                    raise RuntimeError(f"Google TTS: HTTP {r.status} {(await r.text())[:200]}")
                data = await r.json()
        return read_wav(base64.b64decode(data["audioContent"]), self.rate)


class ElevenLabs:
    is_async = True

    def __init__(self, cfg, rate):
        self.cfg = cfg
        self.rate = rate
        if not cfg["voice"]:
            raise SystemExit("config: an ElevenLabs voice needs voice: <voice id>")

    async def speak(self, text, tone="normal"):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.cfg['voice']}?output_format=pcm_16000"
        model = self.cfg["model"] or "eleven_flash_v2_5"
        if tone == "shout":
            text = text.upper()
        if tone != "normal":
            text = text.rstrip(".") + ("!" if not text.endswith(("!", "?")) else "")
            if model.startswith("eleven_v3"):
                text = ("[shouting] " if tone == "shout" else "[angry] ") + text
        body = {"text": text, "model_id": model}
        speed = min(1.2, float(self.cfg["speed"]) * TONE_SHAPE[tone][0])   # ElevenLabs allows 0.7-1.2
        if speed != 1.0:
            body["voice_settings"] = {"speed": speed}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(url, json=body, headers={"xi-api-key": self.cfg["api_key"]}) as r:
                if r.status != 200:
                    raise RuntimeError(f"ElevenLabs: HTTP {r.status} {(await r.text())[:200]}")
                raw = await r.read()
        return resample(np.frombuffer(raw, "<i2").astype(np.float32) / 32768, 16000, self.rate)


class MockTTS:
    is_async = False

    def __init__(self, cfg, rate):
        self.rate = rate

    def speak(self, text, tone="normal"):
        out = []
        for i, _ in enumerate(text.split()):
            t = np.arange(int(self.rate * 0.22)) / self.rate
            f = 300 + 60 * (i % 4)
            out += [0.4 * np.sin(2 * np.pi * f * t) * np.hanning(len(t)), np.zeros(int(self.rate * 0.05))]
        return np.concatenate(out or [np.zeros(1)]).astype(np.float32)


BACKENDS = {"piper": Piper, "azure": Azure, "google": Google, "elevenlabs": ElevenLabs, "mock": MockTTS}


def create(cfg, rate):
    return BACKENDS[cfg["backend"]](cfg, rate)


async def speak(engine, text, tone="normal"):
    tone = tone if tone in TONES else "normal"
    if engine.is_async:
        x = await engine.speak(text, tone)
    else:
        x = await asyncio.to_thread(engine.speak, text, tone)
    return shape(x, tone)


# ---- the Say panel ------------------------------------------------------------

class Speech:
    """Operators type something, pick a voice, and it's spoken on air (tts: in config.yaml).

    Each voice in tts.voices names a backend and that backend's voice; the cloud keys are shared
    per backend (tts.azure, tts.google, tts.elevenlabs). A voice's engine is made the first time
    it's used and kept, so a Piper voice is only loaded into memory once it's picked.
    """

    def __init__(self, cfg, rate):
        self.cfg = cfg
        self.rate = rate
        self.voices = {v["name"]: v for v in cfg["voices"]}
        self.engines = {}
        self.lock = asyncio.Lock()  # one at a time: Piper keeps a CPU core busy while it works

    def hello(self):
        return {"voices": [{"name": v["name"], "backend": v["backend"]} for v in self.cfg["voices"]],
                "max_chars": int(self.cfg["max_chars"])}

    def _engine_cfg(self, v):
        keys = self.cfg.get(v["backend"]) or {}
        return {"backend": v["backend"], "voice": v.get("voice"), "speed": float(v.get("speed") or 1.0),
                "model": v.get("model") or keys.get("model"), "api_key": keys.get("api_key"),
                "region": keys.get("region")}

    async def say(self, voice, text):
        """float32 audio of `text` in `voice`, peak at tts.level. ValueError for bad requests,
        anything else for a backend that failed."""
        v = self.voices.get(voice)
        if v is None:
            raise ValueError("That voice isn't in config.yaml any more (reload the page)")
        text = clean(str(text)[:int(self.cfg["max_chars"])], self.cfg["pronounce"], stage_directions=False)
        if not text:
            raise ValueError("Nothing to say")
        async with self.lock:
            engine = self.engines.get(voice)
            if engine is None:
                engine = await asyncio.to_thread(create, self._engine_cfg(v), self.rate)
                self.engines[voice] = engine
            x = await speak(engine, text)
        x = np.asarray(x, np.float32)
        peak = float(np.max(np.abs(x))) if len(x) else 0.0
        if not peak:
            raise RuntimeError("the voice came back silent")
        return x * (float(self.cfg["level"]) / peak)
