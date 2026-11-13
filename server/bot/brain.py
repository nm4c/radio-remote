"""The LLM: decides whether to say something, what, and what to remember.

Every over that reaches it becomes one chat request: the persona (a prompt file you can edit
freely), what the agent knows about the speaker, the last few overs on the channel, and the new
over. The model answers with a JSON object:

  {"reply": "...", "name": null, "note": null}

  reply  what to say on air, or "" to stay quiet
  name   the speaker's own name or callsign if they just said it (an anonymous caller becomes
         acquainted under that name)
  note   one short new thing worth remembering about the speaker (added to their notes)

Small models sometimes wrap the JSON in other text, or ignore the format. parse() takes the
first {...} it finds; failing that, the whole answer is taken as the reply.

Backends (bot.llm.backend):
  ollama  Ollama's /api/chat, which hailo-ollama on the AI HAT+ 2 also serves. Asks for JSON
          output (format: json), which keeps small models on the rails.
  openai  any OpenAI-compatible /chat/completions: llama.cpp's server, vLLM, LM Studio, or a
          cloud API. url is the base (ending in /v1).
  mock    canned answers.
"""

import json
import logging
import random
import re
import time
from pathlib import Path

import aiohttp

log = logging.getLogger("bot.brain")
DEFAULT_PERSONA = Path(__file__).resolve().parent / "prompts" / "banter.md"

POLICY_TEXT = {
    "llm": "Most overs need no answer: reply only when you have something genuinely funny, warm or "
           "useful to add, or someone speaks to you. Otherwise give an empty reply.",
    "always": "Reply to this over (the reply must not be empty).",
    "addressed": "You have been spoken to by name: reply.",
}

SPOKEN = ("Your reply is spoken over a two-way radio: plain words only (no emoji, lists or stage "
          "directions), short (one or two sentences), and never pretend to be one of the people on the channel.")
FORMAT_LEARN = ('Answer with one JSON object and nothing else:\n'
                '{"reply": "<what you say on air, or empty to stay quiet>", '
                '"name": <the speaker\'s own name or callsign if they just said it, else null>, '
                '"note": <one short new fact worth remembering about this speaker, else null>}\n' + SPOKEN)
FORMAT_SIMPLE = ('Answer with one JSON object and nothing else:\n'
                 '{"reply": "<what you say on air, or empty to stay quiet>"}\n' + SPOKEN)


def load_persona(path):
    p = Path(path) if path else DEFAULT_PERSONA
    try:
        return p.read_text().strip()
    except OSError as e:
        log.error("can't read the persona %s (%s): using the built-in one", p, e)
        return DEFAULT_PERSONA.read_text().strip()


def build_messages(persona, name, policy, learn, speaker, notes, history, text):
    """speaker: a display label; notes: what's known about them; history: [(when, who, text)]."""
    system = "\n\n".join([
        persona.replace("{name}", name),
        f'You are "{name}", listening to a two-way radio channel. {POLICY_TEXT[policy]}',
        FORMAT_LEARN if learn else FORMAT_SIMPLE,
    ])
    lines = [f"[{time.strftime('%H:%M', time.localtime(t))}] {who}: {said}" for t, who, said in history]
    parts = []
    if lines:
        parts.append("Recent overs on the channel:\n" + "\n".join(lines))
    parts.append(f"About {speaker}:\n{notes.strip()}" if notes and notes.strip()
                 else f"You know nothing yet about {speaker}.")
    parts.append(f'New over from {speaker}: "{text}"')
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


def parse(raw):
    """The model's answer as {"reply", "name", "note"} (missing parts None / "")."""
    out = {"reply": "", "name": None, "note": None}
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    data = None
    if m:
        try:
            data = json.loads(m.group(0))
        except ValueError:
            data = None
    if isinstance(data, dict):
        for k in out:
            v = data.get(k)
            if isinstance(v, str) and v.strip() and v.strip().lower() not in ("null", "none"):
                out[k] = v.strip()
        out["reply"] = out["reply"] or ""
    elif raw and not raw.startswith("{"):
        out["reply"] = raw.strip('"').strip()
    return out


class OllamaLLM:
    def __init__(self, cfg):
        self.cfg = cfg
        self.url = cfg["url"].rstrip("/") + "/api/chat"

    async def chat(self, messages):
        body = {"model": self.cfg["model"], "messages": messages, "stream": False, "format": "json",
                "options": {"temperature": float(self.cfg["temperature"]), "num_predict": int(self.cfg["max_tokens"])}}
        timeout = aiohttp.ClientTimeout(total=float(self.cfg["timeout_s"]))
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(self.url, json=body) as r:
                if r.status != 200:
                    raise RuntimeError(f"LLM server: HTTP {r.status} {(await r.text())[:200]}")
                data = await r.json(content_type=None)
        return (data.get("message") or {}).get("content", "")


class OpenAILLM:
    def __init__(self, cfg):
        self.cfg = cfg
        self.url = cfg["url"].rstrip("/") + "/chat/completions"

    async def chat(self, messages):
        body = {"model": self.cfg["model"], "messages": messages, "temperature": float(self.cfg["temperature"]),
                "max_tokens": int(self.cfg["max_tokens"]), "response_format": {"type": "json_object"}}
        headers = {"Authorization": f"Bearer {self.cfg['api_key']}"} if self.cfg["api_key"] else {}
        timeout = aiohttp.ClientTimeout(total=float(self.cfg["timeout_s"]))
        async with aiohttp.ClientSession(timeout=timeout) as s:
            for attempt in (1, 2):
                async with s.post(self.url, json=body, headers=headers) as r:
                    text = await r.text()
                if r.status == 400 and attempt == 1 and "response_format" in text:
                    body.pop("response_format")  # a server without JSON mode: ask again without it
                    continue
                if r.status != 200:
                    raise RuntimeError(f"LLM server: HTTP {r.status} {text[:200]}")
                data = json.loads(text)
                break
        return data["choices"][0]["message"]["content"]


class MockLLM:
    QUIPS = ["Copy that, and may your ute start first go.",
             "Loud and clear, unlike your directions last week.",
             "Righto, I'll put the kettle on.", "", ""]

    def __init__(self, cfg=None):
        self.rng = random.Random(2)

    async def chat(self, messages):
        return json.dumps({"reply": self.rng.choice(self.QUIPS), "name": None, "note": None})


def create(cfg):
    if cfg["backend"] == "ollama":
        return OllamaLLM(cfg)
    if cfg["backend"] == "openai":
        return OpenAILLM(cfg)
    return MockLLM(cfg)
