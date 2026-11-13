"""Text to speech: the agent's voice.

Backends (bot.tts.backend), all returning float32 audio at the wire rate (16 kHz):
  piper       offline, on the Pi (pip install piper-tts). voice: path of a .onnx voice; its
              .onnx.json must sit beside it. Your own Australian voice goes here once trained
              (docs/voice/README.md). Faster than real time on a Pi 5.
  azure       Azure AI Speech. voice e.g. en-AU-NatashaNeural or en-AU-WilliamNeural;
              region e.g. australiaeast; api_key is the resource key.
  google      Google Cloud Text-to-Speech. voice e.g. en-AU-Neural2-B (male) or en-AU-Neural2-C
              (female); api_key is an API key allowed to use the service.
  elevenlabs  ElevenLabs. voice: a voice id from your voice library (pick an Australian one);
              model defaults to eleven_flash_v2_5, the low-latency one.
  mock        a short warble per word, for testing without a voice.

The cloud ones need the internet (the USB modem) and add about 0.5-1.5 s per reply.
"""

import asyncio
import base64
import logging
import re
from html import escape

import aiohttp
import numpy as np

from .capture import read_wav, resample

log = logging.getLogger("bot.tts")


def clean(text, pronounce):
    """Plain speakable text: no emoji, markdown or stage directions; pronunciation fixes applied."""
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

    def speak(self, text):
        length = 1.0 / self.speed
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

    async def speak(self, text):
        rate_pct = f"{(float(self.cfg['speed']) - 1) * 100:+.0f}%"
        ssml = (f"<speak version='1.0' xml:lang='en-AU'><voice name='{escape(self.voice)}'>"
                f"<prosody rate='{rate_pct}'>{escape(text)}</prosody></voice></speak>")
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

    async def speak(self, text):
        body = {"input": {"text": text},
                "voice": {"languageCode": "-".join(self.voice.split("-")[:2]), "name": self.voice},
                "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 16000,
                                "speakingRate": float(self.cfg["speed"])}}
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
            raise SystemExit("config: bot.tts.voice must be an ElevenLabs voice id")

    async def speak(self, text):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.cfg['voice']}?output_format=pcm_16000"
        body = {"text": text, "model_id": self.cfg["model"] or "eleven_flash_v2_5"}
        if float(self.cfg["speed"]) != 1.0:
            body["voice_settings"] = {"speed": float(self.cfg["speed"])}
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

    def speak(self, text):
        out = []
        for i, _ in enumerate(text.split()):
            t = np.arange(int(self.rate * 0.22)) / self.rate
            f = 300 + 60 * (i % 4)
            out += [0.4 * np.sin(2 * np.pi * f * t) * np.hanning(len(t)), np.zeros(int(self.rate * 0.05))]
        return np.concatenate(out or [np.zeros(1)]).astype(np.float32)


BACKENDS = {"piper": Piper, "azure": Azure, "google": Google, "elevenlabs": ElevenLabs, "mock": MockTTS}


def create(cfg, rate):
    return BACKENDS[cfg["backend"]](cfg, rate)


async def speak(engine, text):
    if engine.is_async:
        return await engine.speak(text)
    return await asyncio.to_thread(engine.speak, text)
