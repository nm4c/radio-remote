"""The AI agent: listens to the channel, works out who is talking, and (if allowed) answers.

    received audio -> OverCapture -> queue -> worker:  speech to text
                                                       speaker embedding -> People (who is it?)
                                                       LLM (persona + their notes + recent overs)
                                                       learn: their name, a note
                                                       TTS -> reply -> sender
    sender: (approve mode: wait for an operator)  ->  wait for a quiet channel  ->  transmit

Modes (switched from the web page; bot.mode is the start-up one):
  listen   transcribe and identify only; never calls the LLM, never transmits
  approve  each reply waits in the web page for an operator to send or discard it
  auto     replies go on air by themselves

Behaviour is set by the config (persona, reply_policy, rate limits, learning), not fixed here.
What is fixed is radio mechanics: it never keys up while the channel is busy or someone else is
transmitting, every transmission is bounded by bot.max_reply_s and the server's own
ptt.max_tx_seconds watchdog, and any operator can stop it or switch it to listen.

Research log: <bot.dir>/log/YYYY-MM-DD.jsonl, one JSON object per line: every over (time,
length, transcript, who it was matched to and how closely), every LLM decision (prompt size,
raw answer, latency, reply or silence) and what became of each reply (sent, dropped and why,
stopped, discarded). With bot.keep_audio each over's audio is kept in <bot.dir>/audio/<date>/.
"""

import asyncio
import collections
import json
import logging
import secrets
import shutil
import time
from pathlib import Path

import numpy as np

from . import brain, stt, tts
from .capture import OverCapture, to_float, wav_bytes
from .speakers import People, create_embedder

log = logging.getLogger("bot")
for _noisy in ("httpx", "httpx2", "huggingface_hub", "speechbrain", "faster_whisper"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)  # model libraries chat at INFO
BOT_CLIENT_ID = 0      # owner id the agent keys the radio with (operators' ids start at 1)
HISTORY = 200          # items (overs and replies) kept for the web page
SHOWN = 80             # of which a newly connected operator is sent this many
QUEUE_MAX = 20         # overs waiting to be processed before the oldest are dropped


class BotOperator:
    """Stands in for an operator when the agent transmits, so the PTT watchdog, the audit log
    and the 'in use by' display treat it like anyone else."""

    def __init__(self, name):
        self.id = BOT_CLIENT_ID
        self.name = f"{name} (AI)"
        self.username = "bot"
        self.remote = None
        self.last_hold = 0.0
        self.fx = None
        self.rtc = None
        self.authed = False
        self.control = True

    def send_json(self, obj):
        pass


class Reply:
    def __init__(self, over_id, text, seq):
        self.id = secrets.token_hex(5)
        self.over_id = over_id
        self.text = text
        self.seq = seq            # overs finished when it was written (for drop_stale)
        self.samples = None
        self.decision = None      # approve mode: asyncio.Future -> True (send) / False (discard)
        self.decision_by = None   # "by <operator>" who discarded it
        self.stopped_by = None    # "by <operator>" who stopped it on air
        self.created = time.time()


class Agent:
    def __init__(self, cfg, head):
        self.cfg = cfg
        self.b = b = cfg["bot"]
        self.head = head
        self.rate = int(cfg["audio"]["wire_rate"])
        self.dir = Path(b["dir"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name = str(b["name"])
        self.operator = BotOperator(self.name)
        self.mode = b["mode"]
        self.persona = brain.load_persona(b["persona"])
        self.wake = [w.lower() for w in [self.name, *(b["wake_words"] or [])] if w]
        self.capture = OverCapture(self.rate, b["preroll_ms"], b["hang_ms"], float(b["min_over_s"]),
                                   float(b["max_over_s"]), self._on_over)
        self.people = People(self.dir / "people", b["speaker"], b["speaker"]["backend"])
        self.stt = self.embedder = self.llm = self.voice = None
        self.status = {"stt": "loading", "speaker": "loading", "llm": "ready", "tts": "loading"}
        self.queue = asyncio.Queue(QUEUE_MAX)
        self.items = collections.OrderedDict()   # id -> item shown in the web page
        self.context = collections.deque(maxlen=int(b["context_overs"]))   # (t, who, text) for the LLM
        self.embeddings = collections.OrderedDict()  # over id -> (embedding, seconds), for enrolling
        self.seq = 0               # overs finished so far
        self.pending = None        # the Reply waiting to go on air
        self.sent_times = collections.deque()
        self.last_tx_end = 0.0
        self.stage = None          # what the worker is doing now, for the web page
        self._tasks = set()

    # ---- start-up ---------------------------------------------------------------

    async def run(self):
        """Load the models (in threads, so the radio keeps working meanwhile), then process overs."""
        b = self.b
        loaders = {
            "stt": lambda: stt.create(b["stt"], self.rate),
            "speaker": lambda: create_embedder(b["speaker"], self.rate, self.dir),
            "tts": lambda: tts.create(b["tts"], self.rate),
        }
        for key, make in loaders.items():
            try:
                obj = await asyncio.to_thread(make)
                setattr(self, {"stt": "stt", "speaker": "embedder", "tts": "voice"}[key], obj)
                self.status[key] = "ready"
            except ImportError as e:  # installed without the AI agent's packages
                self.status[key] = f"not installed ({e.name}): run  sudo ./deploy/update.sh --with-ai"
                log.error("AI agent %s: %s is not installed. Run: sudo ./deploy/update.sh --with-ai", key, e.name)
            except Exception as e:  # bad model path, no network for a first download...
                self.status[key] = f"error: {e}"
                log.error("AI agent %s: %s", key, e)
        try:
            self.llm = brain.create(b["llm"])
        except Exception as e:
            self.status["llm"] = f"error: {e}"
        log.info("AI agent ready (%s mode): %s", self.mode, ", ".join(f"{k} {v}" for k, v in self.status.items()))
        self._broadcast(status=self.status)
        self._spawn(self._housekeeping())
        while True:
            pcm, started, seconds, seq = await self.queue.get()
            try:
                await self._process(pcm, started, seconds, seq)
            except Exception:
                log.exception("AI agent: processing an over failed")
            finally:
                self._set_stage(None)

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _housekeeping(self):
        while True:
            days = self.b["retention_days"]
            if days:
                try:
                    await asyncio.to_thread(self._prune, float(days))
                except OSError as e:
                    log.error("AI agent: pruning old logs: %s", e)
            await asyncio.sleep(6 * 3600)

    def _prune(self, days):
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
        for f in (self.dir / "log").glob("*.jsonl"):
            if f.stem < cutoff:
                f.unlink()
        for d in (self.dir / "audio").glob("*"):
            if d.is_dir() and d.name < cutoff:
                shutil.rmtree(d)

    # ---- audio in (event loop) -------------------------------------------------------

    def feed(self, pcm, busy, keyed):
        self.capture.feed(pcm, busy, keyed)

    def _on_over(self, pcm, started, seconds):
        self.seq += 1
        if self.queue.full():
            self.queue.get_nowait()
            log.warning("AI agent is falling behind: dropped the oldest waiting over")
        self.queue.put_nowait((pcm, started, seconds, self.seq))

    # ---- one over ----------------------------------------------------------------------

    async def _process(self, pcm, started, seconds, seq):
        b = self.b
        over_id = secrets.token_hex(5)
        samples = to_float(pcm)
        item = {"id": over_id, "kind": "over", "t": started, "secs": round(seconds, 1), "text": None,
                "person": None, "who": None, "who_kind": None, "score": None}
        self._upsert(item)
        if b["keep_audio"]:
            day = self.dir / "audio" / time.strftime("%Y-%m-%d", time.localtime(started))
            day.mkdir(parents=True, exist_ok=True)
            (day / f"{over_id}.wav").write_bytes(wav_bytes(pcm, self.rate))

        # Words.
        text = ""
        if self.stt:
            self._set_stage("transcribing")
            t0 = time.monotonic()
            hint = ", ".join(p["name"] for p in self.people.list() if p["kind"] != "anonymous")
            try:
                if self.stt.is_async:
                    text = await self.stt.transcribe(samples, hint)
                else:
                    text = await asyncio.to_thread(self.stt.transcribe, samples, hint)
            except Exception as e:
                log.error("AI agent: speech to text: %s", e)
            item["stt_ms"] = int((time.monotonic() - t0) * 1000)
        item["text"] = text

        # Voice.
        person, score, emb = None, None, None
        if self.embedder and seconds >= 1.0:
            self._set_stage("identifying")
            try:
                emb = await asyncio.to_thread(self.embedder.embed, samples)
            except Exception as e:
                log.error("AI agent: speaker embedding: %s", e)
        if emb is not None:
            person, score = self.people.identify(emb)
            if person is None and b["learn_anonymous"] and text and seconds >= float(b["speaker"]["min_enrol_s"]):
                person = self.people.create(None, "anonymous", emb)
                item["new_person"] = True
            elif person is not None and score >= self.people.update:
                self.people.add_sample(person["id"], emb)
            self.embeddings[over_id] = (emb, seconds)
            while len(self.embeddings) > 100:
                self.embeddings.popitem(last=False)
        if person:
            self.people.seen(person["id"])
        item.update(person=person and person["id"], who=person and person["name"],
                    who_kind=person and person["kind"], score=None if score is None else round(score, 3))
        self._upsert(item)
        self._log("over", **item, audio=bool(b["keep_audio"]))
        if item.get("new_person"):
            self._broadcast(people=self.people.list())
        if not text:
            return
        label = person["name"] if person else "an unidentified speaker"
        self.context.append((started, label, text))

        lowered = text.lower()
        if person and any(p.lower() in lowered for p in b["opt_out_phrases"] or []):
            self.people.delete(person["id"])
            self._log("opt_out", person=person["id"], name=person["name"], over=over_id)
            self._broadcast(people=self.people.list())
            return
        if self.mode == "listen" or not self.llm:
            return
        addressed = any(w in lowered for w in self.wake)
        policy = b["reply_policy"]
        if policy == "addressed" and not addressed:
            return

        # Thinking.
        self._set_stage("thinking")
        notes = self.people.notes(person["id"]) if person else ""
        who = label if not person or person["kind"] != "anonymous" else f"{label} (name not known yet)"
        history = list(self.context)[:-1]
        messages = brain.build_messages(self.persona, self.name, policy, bool(b["learn_notes"]), who,
                                        notes, history, text)
        t0 = time.monotonic()
        try:
            raw = await self.llm.chat(messages)
        except Exception as e:
            log.error("AI agent: LLM: %s", e)
            self._log("llm_error", over=over_id, error=str(e))
            return
        llm_ms = int((time.monotonic() - t0) * 1000)
        out = brain.parse(raw)
        self._log("decision", over=over_id, policy=policy, addressed=addressed, llm_ms=llm_ms,
                  prompt_chars=sum(len(m["content"]) for m in messages), raw=raw, **out)

        # Learning.
        if person and b["learn_notes"]:
            changed = False
            if out["name"] and person["kind"] == "anonymous" and len(out["name"]) <= 40:
                existing = self.people.find_name(out["name"])
                if existing:
                    self.people.merge(person["id"], existing["id"])
                    person = existing
                else:
                    self.people.rename(person["id"], out["name"], "acquainted")
                self._log("learned_name", person=person["id"], name=out["name"], over=over_id)
                changed = True
            if out["note"]:
                self.people.add_note(person["id"], out["note"])
                self._log("note", person=person["id"], note=out["note"], over=over_id)
                changed = True
            if changed:
                self._broadcast(people=self.people.list())

        if not out["reply"] or self.mode == "listen":
            return
        reply = Reply(over_id, out["reply"], seq)
        spoken = tts.clean(reply.text, b["pronounce"])
        if not spoken or not self.voice:
            return
        self._set_stage("speaking")
        try:
            samples = await tts.speak(self.voice, spoken)
        except Exception as e:
            log.error("AI agent: text to speech: %s", e)
            self._log("tts_error", over=over_id, error=str(e))
            return
        limit = int(float(b["max_reply_s"]) * self.rate)
        if len(samples) > limit:  # cut, with a short fade so it doesn't click
            samples = samples[:limit]
            fade = min(len(samples), int(0.05 * self.rate))
            samples[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        peak = float(np.max(np.abs(samples))) if len(samples) else 0
        reply.samples = samples * (float(b["level"]) / peak) if peak else samples
        reply_item = {"id": reply.id, "kind": "reply", "t": time.time(), "text": reply.text, "over": over_id,
                      "secs": round(len(samples) / self.rate, 1), "status": "queued", "llm_ms": llm_ms}
        self._upsert(reply_item)
        if self.pending:
            self._finish(self.pending, "dropped", "a newer reply replaced it")
        self.pending = reply
        self._spawn(self._deliver(reply))

    # ---- replies out --------------------------------------------------------------------

    async def _deliver(self, reply):
        b = self.b
        if self.mode == "approve":
            reply.decision = asyncio.get_running_loop().create_future()
            self._update(reply.id, status="pending")
            try:
                ok = await asyncio.wait_for(asyncio.shield(reply.decision), float(b["approve_timeout_s"]))
            except asyncio.TimeoutError:
                return self._finish(reply, "dropped", "nobody approved it in time")
            if not ok:
                return self._finish(reply, "discarded", reply.decision_by)
        if self.pending is not reply:
            return
        self._update(reply.id, status="waiting")
        gap = float(b["reply_gap_ms"]) / 1000
        deadline = time.monotonic() + float(b["reply_wait_s"])
        quiet_since = None
        while True:
            if self.pending is not reply:
                return
            if self.mode == "listen":
                return self._finish(reply, "dropped", "switched to listen")
            if b["drop_stale"] and self.seq > reply.seq:
                return self._finish(reply, "dropped", "someone else spoke first")
            busy, on_air = self.head.bot_channel_state()
            if busy or on_air or self.capture.active:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.monotonic()
            if quiet_since is not None and time.monotonic() - quiet_since >= gap:
                break
            if time.monotonic() > deadline:
                return self._finish(reply, "dropped", "the channel didn't go quiet")
            await asyncio.sleep(0.1)
        now = time.time()
        while self.sent_times and now - self.sent_times[0] > 3600:
            self.sent_times.popleft()
        per_hour = b["max_replies_per_hour"]
        if per_hour and len(self.sent_times) >= int(per_hour):
            return self._finish(reply, "dropped", f"limit of {per_hour} an hour")
        if now - self.last_tx_end < float(b["min_reply_gap_s"]):
            return self._finish(reply, "dropped", "too soon after its last one")
        self._update(reply.id, status="on air")
        try:
            done = await self.head.bot_transmit(reply.samples, reply.text, int(b["tx_delay_ms"]))
        except Exception as e:
            return self._finish(reply, "dropped", f"couldn't transmit: {e}")
        self.last_tx_end = time.time()
        self.sent_times.append(self.last_tx_end)
        self.context.append((now, f"{self.name} (you)", reply.text))
        self._finish(reply, "sent" if done else "stopped", None if done else reply.stopped_by)

    def _finish(self, reply, status, reason=None):
        if self.pending is reply:
            self.pending = None
        if reply.decision and not reply.decision.done():
            reply.decision.cancel()
        self._update(reply.id, status=status, reason=reason)
        item = self.items.get(reply.id, {})
        self._log("reply", id=reply.id, over=reply.over_id, text=reply.text, status=status, reason=reason,
                  secs=item.get("secs"), waited_s=round(time.time() - reply.created, 1))

    # ---- web page ------------------------------------------------------------------------

    def hello(self):
        return {"name": self.name, "mode": self.mode, "policy": self.b["reply_policy"], "status": self.status,
                "stage": self.stage, "people": self.people.list(), "items": list(self.items.values())[-SHOWN:],
                "backends": {k: self.b[k]["backend"] for k in ("stt", "speaker", "llm", "tts")}}

    def handle(self, c, kind, m):
        """Messages from control operators. Returns a notice text for refused ones."""
        if kind == "bot_mode":
            mode = m.get("mode")
            if mode not in ("listen", "approve", "auto") or mode == self.mode:
                return None
            self.mode = mode
            log.info("%s switched the AI agent to %s", c.name, mode)
            self.head.bot_audit(c, "bot_mode", mode=mode)
            self._log("mode", mode=mode, by=c.username)
            if mode == "listen" and self.pending:
                self._finish(self.pending, "dropped", "switched to listen")
            self._broadcast(mode=mode)
        elif kind in ("bot_approve", "bot_reject"):
            r = self.pending
            if not r or r.id != m.get("id") or not r.decision or r.decision.done():
                return "That reply has already gone"
            r.decision_by = f"by {c.name}"
            r.decision.set_result(kind == "bot_approve")
            self.head.bot_audit(c, kind, text=r.text)
        elif kind == "bot_stop":
            if self.pending:
                self.pending.stopped_by = f"by {c.name}"
            self.head.bot_stop(c)
        elif kind == "bot_enrol":
            return self._enrol(c, m.get("over"), str(m.get("name") or "").strip())
        elif kind == "bot_rename":
            p = self.people.get(m.get("person"))
            name = str(m.get("name") or "").strip()
            if not p or not name:
                return "Nobody to rename"
            other = self.people.find_name(name)
            if other and other["id"] != p["id"]:
                self.people.merge(p["id"], other["id"])
            else:
                self.people.rename(p["id"], name, "known" if p["kind"] == "anonymous" else None)
            self._log("rename", person=p["id"], name=name, by=c.username)
            self._broadcast(people=self.people.list())
        elif kind == "bot_forget":
            p = self.people.delete(m.get("person"))
            if p:
                self._log("forget", person=p["id"], name=p["name"], by=c.username)
                self.head.bot_audit(c, "bot_forget", name=p["name"])
                self._broadcast(people=self.people.list())
        return None

    def _enrol(self, c, over_id, name):
        """Name the voice in one over: makes or updates a known person."""
        if not name:
            return "Type a name"
        if over_id not in self.embeddings:
            return "That over is too short or too old to learn a voice from"
        emb, _ = self.embeddings[over_id]
        item = self.items.get(over_id) or {}
        target = self.people.find_name(name)
        current = self.people.get(item.get("person")) if item.get("person") else None
        if target:
            if current and current is not target:
                self.people.merge(current["id"], target["id"])
            self.people.add_sample(target["id"], emb)
        elif current:
            target = self.people.rename(current["id"], name, "known")
        else:
            target = self.people.create(name, "known", emb)
        # Relabel this over and any other recent ones of the merged/renamed person.
        for it in self.items.values():
            if it.get("kind") == "over" and (it["id"] == over_id or (current and it.get("person") == current["id"])):
                it.update(person=target["id"], who=target["name"], who_kind=target["kind"])
                self._broadcast(items=[it])
        self._log("enrol", over=over_id, person=target["id"], name=name, by=c.username)
        self.head.bot_audit(c, "bot_enrol", name=name)
        self._broadcast(people=self.people.list())
        return None

    def _set_stage(self, stage):
        if stage != self.stage:
            self.stage = stage
            self._broadcast(stage=stage)

    def _upsert(self, item):
        self.items[item["id"]] = item
        self.items.move_to_end(item["id"])
        while len(self.items) > HISTORY:
            self.items.popitem(last=False)
        self._broadcast(items=[item])

    def _update(self, item_id, **changes):
        it = self.items.get(item_id)
        if it:
            it.update(changes)
            self._broadcast(items=[it])

    def _broadcast(self, **fields):
        self.head.bot_broadcast({"type": "bot", **fields})

    def _log(self, event, **data):
        try:
            d = self.dir / "log"
            d.mkdir(exist_ok=True)
            rec = {"at": round(time.time(), 2), "event": event, **data}
            with (d / f"{time.strftime('%Y-%m-%d')}.jsonl").open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError as e:
            log.error("AI agent log: %s", e)

