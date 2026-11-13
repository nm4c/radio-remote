"""Speech to text for each over.

Backends (bot.stt.backend):
  faster_whisper  Whisper on the Pi's CPU (pip install faster-whisper). base.en takes a few
                  seconds per over on a Pi 5; tiny.en is quicker and rougher, small.en better and slower.
  openai          any server with the OpenAI /audio/transcriptions API: OpenAI itself, Speaches,
                  LocalAI, or a Whisper server on the AI HAT+ 2 that offers that API.
  mock            canned phrases, for trying the rest of the agent without a model.

transcribe(samples, hint) takes float32 audio at 16 kHz; hint is extra vocabulary (known names).
Blocking backends run in a worker thread (see agent.py); the HTTP one is async.
"""

import logging
import random

import aiohttp

from .capture import to_pcm, wav_bytes

log = logging.getLogger("bot.stt")


class FasterWhisper:
    is_async = False

    def __init__(self, cfg):
        from faster_whisper import WhisperModel  # optional dependency (requirements-bot.txt)
        self.cfg = cfg
        log.info("loading Whisper model %s (%s)", cfg["model"], cfg["compute_type"])
        self.model = WhisperModel(cfg["model"], device="cpu", compute_type=cfg["compute_type"])

    def transcribe(self, samples, hint=""):
        prompt = " ".join(p for p in (self.cfg["prompt"], hint) if p) or None
        segments, _ = self.model.transcribe(
            samples, language=self.cfg["language"] or None, beam_size=int(self.cfg["beam_size"]),
            initial_prompt=prompt, condition_on_previous_text=False, vad_filter=False)
        return " ".join(s.text.strip() for s in segments).strip()


class OpenAITranscribe:
    is_async = True

    def __init__(self, cfg, rate):
        self.cfg = cfg
        self.rate = rate
        self.url = cfg["url"].rstrip("/") + "/audio/transcriptions"

    async def transcribe(self, samples, hint=""):
        form = aiohttp.FormData()
        form.add_field("file", wav_bytes(to_pcm(samples), self.rate), filename="over.wav", content_type="audio/wav")
        form.add_field("model", self.cfg["model"])
        if self.cfg["language"]:
            form.add_field("language", self.cfg["language"])
        prompt = " ".join(p for p in (self.cfg["prompt"], hint) if p)
        if prompt:
            form.add_field("prompt", prompt)
        headers = {"Authorization": f"Bearer {self.cfg['api_key']}"} if self.cfg["api_key"] else {}
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(self.url, data=form, headers=headers) as r:
                if r.status != 200:
                    raise RuntimeError(f"transcription server: HTTP {r.status} {(await r.text())[:200]}")
                return ((await r.json()).get("text") or "").strip()


class MockSTT:
    is_async = False
    LINES = [
        "Anyone got a copy on this channel, over.",
        "Yeah righto, heading back to the depot now.",
        "Has anyone seen the keys for the white ute?",
        "Radio check, how do you read me.",
        "Weather's turning, might knock off early this arvo.",
        "G'day all, it's Dave, just checking in.",
    ]

    def __init__(self, cfg=None):
        self.rng = random.Random(1)

    def transcribe(self, samples, hint=""):
        return self.rng.choice(self.LINES)


def create(cfg, rate):
    kind = cfg["backend"]
    if kind == "faster_whisper":
        return FasterWhisper(cfg)
    if kind == "openai":
        return OpenAITranscribe(cfg, rate)
    return MockSTT(cfg)
