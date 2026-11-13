"""Remote head server: WebSocket control + audio, and the web UI."""

import argparse
import asyncio
import collections
import hmac
import itertools
import json
import logging
import secrets
import ssl
import time
from pathlib import Path

import numpy as np
from aiohttp import WSMsgType, web

from .audio import create_audio
from .config import load
from . import clips, rtc, selcall, voicefx
from .auth import LoginLimiter, UserDB, AuthError, check_password, hash_password, verify_password
from .eq import TxEq
from .recorder import Recorder
from .radio import RadioError, create_radio

log = logging.getLogger("server")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
AUDIO_QUEUE_LIMIT = 25  # frames (~0.5 s) before we start dropping RX audio for a slow client
SELCALL_LOG = 20        # recent selcalls (sent and received) kept for operators who join later
PENDING_PER_IP = 8      # WebSocket connections from one address that haven't logged in yet
AUTH_RECHECK_S = 5      # how often connected operators are re-checked against the user database

# Messages only 'control' accounts may send. Everything else a 'listen' account sends is ignored.
CONTROL_MESSAGES = {"ptt", "ptt_hold", "channel", "selcall", "voice_fx", "record",
                    "clip_play", "clip_stop", "clip_delete", "clip_rename",
                    "bot_mode", "bot_approve", "bot_reject", "bot_stop", "bot_enrol", "bot_rename", "bot_forget"}


class Client:
    _ids = itertools.count(1)

    def __init__(self, ws, remote):
        self.id = next(Client._ids)
        self.ws = ws
        self.remote = remote
        self.name = f"Operator {self.id}"
        self.authed = False
        self.user = None          # the account row once logged in
        self.session = None       # hash of this login's session token (see auth.UserDB)
        self.token = secrets.token_urlsafe(18)  # authorises this operator's clip uploads (HTTP)
        self.fx = None  # voice effect preset this operator has switched on, or None
        self.last_hold = 0.0
        self.queue = asyncio.Queue()
        self.audio_queued = 0
        self.rtc = None  # RtcPeer once the browser has set up WebRTC audio
        self.tx_open = False  # accepting this operator's WebRTC mic audio (PTT held, or just released)
        self.sender = asyncio.create_task(self._send_loop())

    @property
    def control(self):
        return bool(self.user) and self.user["role"] == "control"

    @property
    def username(self):
        return self.user["username"] if self.user else None

    def send_json(self, obj):
        self.queue.put_nowait(json.dumps(obj))

    def send_audio(self, pcm):
        if self.audio_queued < AUDIO_QUEUE_LIMIT:
            self.audio_queued += 1
            self.queue.put_nowait(pcm)

    async def _send_loop(self):
        try:
            while True:
                item = await self.queue.get()
                if isinstance(item, bytes):
                    self.audio_queued -= 1
                    await self.ws.send_bytes(item)
                else:
                    await self.ws.send_str(item)
        except (ConnectionError, RuntimeError):
            pass  # socket closed; the receive loop will clean up


class RemoteHead:
    def __init__(self, cfg):
        self.cfg = cfg
        self.clients = set()
        self.loop = None
        self.radio = create_radio(cfg, self.broadcast_state)
        srv = cfg["server"]
        self.db = UserDB(srv["db_file"], srv["session_days"])
        self.limiter = LoginLimiter()
        self.pending = {}         # ip -> WebSocket connections not yet logged in
        self._tx = None           # the transmission in progress, for the audit log
        self._migrate_password()
        self.audio = create_audio(cfg, self._on_rx_audio)
        self._tasks = set()
        self._ptt_gen = 0
        self.rtc_ok = rtc.setup(cfg)
        # Bass/treble for transmitted voice and clips (not selcall tones). One stream at a time
        # goes on air, so one filter state is enough; it is reset at each PTT press.
        a = cfg["audio"]
        self.tx_eq = TxEq(int(a["wire_rate"]), float(a["tx_bass_db"]), float(a["tx_bass_hz"]),
                          float(a["tx_treble_db"]), float(a["tx_treble_hz"]))
        # Voice effects: each operator picks one (Client.fx); the transmitting operator's runs on
        # their voice only (not clips or selcalls). Level matching is measured now, not at PTT.
        self.fx_on = bool(cfg["voice_fx"]["enabled"])
        self.tx_fx = None
        if self.fx_on:
            for name in voicefx.PRESETS:
                voicefx.VoiceFx(name, int(a["wire_rate"]))
        log.info("audio to browsers: %s", "WebRTC (WebSocket fallback)" if self.rtc_ok else "WebSocket")
        # Selcall: digits being sent right now (the radio is keyed for them), recent calls, and the
        # decoder that watches received audio.
        self.sc = cfg["selcall"]
        self.selcall_tx = None
        self.selcall_log = collections.deque(maxlen=SELCALL_LOG)
        self.selcall_rx = None
        if self.sc["enabled"] and self.sc["decode"]:
            self.selcall_rx = selcall.Decoder(self.sc["standard"], int(cfg["audio"]["wire_rate"]),
                                              int(self.sc["decode_min_digits"]), tone_ms=self.sc["tone_ms"])
        # Audio clips: the stored clips, and the one on air now ({"id", "name"}, the radio is keyed).
        self.cl = cfg["clips"]
        self.clips = None
        if self.cl["enabled"]:
            self.clips = clips.ClipStore(self.cl["dir"], int(cfg["audio"]["wire_rate"]),
                                         self.cl["max_seconds"], int(self.cl["max_files"]))
        self.clip_tx = None
        # Recording of received audio into the clip list (needs clips on).
        self.recorder = None
        if self.clips and cfg["record"]["enabled"]:
            self.recorder = Recorder(self.clips, int(cfg["audio"]["wire_rate"]), cfg["record"], self._record_changed)
        # The AI agent (bot.enabled): only imported when it's switched on. bot_tx is its reply on
        # air now ({"text"}), which, like a clip, stops PTT presses meanwhile.
        self.bot = None
        self.bot_tx = None
        if cfg["bot"]["enabled"]:
            from .bot import Agent
            self.bot = Agent(cfg, self)

    def _migrate_password(self):
        """First start with the user database: the old shared server.password becomes a
        'control' account called 'operator', so an existing install keeps working."""
        pw = str(self.cfg["server"].get("password") or "")
        if self.db.count() or not pw or pw == "change-me":
            if not self.db.count():
                log.warning("no operator accounts yet: create one with  sudo radpi-users add <name>")
            return
        try:
            self.db.add("operator", check_password(pw), "control", "Operator")
        except AuthError as e:
            log.warning("couldn't turn server.password into an account (%s): create one with radpi-users", e)
            return
        self.db.log("user_added", "migration", None, username="operator", role="control")
        log.warning("created account 'operator' (control) from server.password. Make personal accounts "
                    "with radpi-users, then remove server.password from the config.")

    def audit(self, c, event, **detail):
        """Write to the audit log as operator c (or the system, when c is None)."""
        try:
            self.db.log(event, c.username if c else None, c.remote if c else None, **detail)
        except Exception as e:  # the radio must keep working even if the log can't be written
            log.error("audit log: %s", e)

    # ---- helpers -------------------------------------------------------

    def _authed(self):
        return [c for c in self.clients if c.authed]

    def _client(self, cid):
        if self.bot and cid == self.bot.operator.id:
            return self.bot.operator
        return next((c for c in self.clients if c.id == cid), None)

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _busy(self):
        hw = self.radio.busy_input
        if hw is not None:
            return hw
        return self.audio.rx_dbfs > self.cfg["audio"]["busy_threshold_dbfs"]

    # ---- broadcast -----------------------------------------------------

    def state_for(self, c):
        r = self.radio
        owner = self._client(r.ptt_owner)
        remaining = None
        if r.ptt_since is not None:
            remaining = max(0.0, self.cfg["ptt"]["max_tx_seconds"] - (time.monotonic() - r.ptt_since))
        return {
            "type": "state",
            "channel": r.channel,
            "channel_name": r.channel_name(r.channel),
            "stepping": r.stepping,
            "info": r.channel_info(),
            "error": r.error,
            "ptt": {
                "active": r.ptt_owner is not None,
                "mine": r.ptt_owner == c.id,
                "by": owner.name if owner else None,
                "remaining_s": remaining,
            },
            "operators": sorted(x.name for x in self._authed()),
            "selcall_tx": self.selcall_tx,
            "clip_tx": {"id": self.clip_tx["id"], "name": self.clip_tx["name"]} if self.clip_tx else None,
            "bot_tx": self.bot_tx["text"] if self.bot_tx else None,
            "recording": self.recorder.status() if self.recorder else None,
            "fx": c.fx,
        }

    def broadcast_state(self):
        for c in self._authed():
            c.send_json(self.state_for(c))

    def _on_rx_audio(self, pcm):
        # Called from the audio thread.
        if self.loop:
            self.loop.call_soon_threadsafe(self._broadcast_audio, pcm)

    def _broadcast_audio(self, pcm):
        owner = self.radio.ptt_owner
        # One frame of what was sent to the radio, taken every frame so it stays in step.
        sent = self.audio.take_tx_monitor(len(pcm))
        if self.recorder and self.recorder.active:
            keyed = owner is not None
            # While we transmit the receiver is muted: record our side instead (record.include_tx).
            self.recorder.feed(sent if keyed and self.recorder.include_tx else pcm,
                               active=keyed or self._busy())
        if self.bot:
            self.bot.feed(pcm, self._busy(), owner is not None)
        if self.selcall_rx:
            if owner is None:
                for digits in self.selcall_rx.feed(pcm):
                    self._selcall_event("rx", digits)
            else:
                self.selcall_rx.blank()  # our own transmission: drop half sequences, skip their tails
        silence = None
        for c in self.clients:
            if not c.authed:
                continue
            if c.rtc and c.rtc.ready:
                # keep the stream running for the transmitting operator, but silent
                if c.id == owner:
                    silence = silence or bytes(len(pcm))
                c.rtc.feed(silence if c.id == owner else pcm)
            elif c.id != owner:
                c.send_audio(pcm)

    # ---- WebRTC ----------------------------------------------------------

    async def _rtc_offer(self, c, sdp):
        if c.rtc:
            await c.rtc.close()
            c.rtc = None
        peer = rtc.RtcPeer(self.cfg, lambda pcm: self._rtc_tx(c, pcm), lambda st: self._rtc_state(c, st))
        c.rtc = peer
        try:
            answer = await peer.answer(sdp)
        except Exception as e:  # bad SDP, no common codec...
            log.warning("WebRTC set-up for %s failed: %s", c.name, e)
            c.rtc = None
            await peer.close()
            c.send_json({"type": "rtc_failed", "text": str(e)})
            return
        if c.rtc is peer:
            c.send_json({"type": "rtc_answer", "sdp": answer})

    def _rtc_tx(self, c, pcm):
        # The browser sends packets all the time (silence when PTT is up): only take them while keyed.
        if c.tx_open and self.radio.ptt_owner == c.id:
            self.audio.push_tx(self._voice(pcm))

    def _rtc_state(self, c, state):
        if state == "connected":
            log.info("%s: audio over WebRTC", c.name)
        elif state in ("failed", "closed") and c.rtc and not c.rtc.ready:
            log.info("%s: WebRTC %s, audio over WebSocket", c.name, state)

    async def _rtc_close(self, c):
        peer, c.rtc = c.rtc, None
        if peer:
            await peer.close()

    # ---- WebSocket -------------------------------------------------------

    def _origin_ok(self, request):
        """Refuse WebSocket connections opened by pages on other sites (cross-site WebSocket
        hijacking). Browsers always send Origin; other clients may leave it out."""
        origin = request.headers.get("Origin")
        if not origin:
            return True
        allowed = {f"{p}://{request.host}" for p in ("http", "https")} | set(self.cfg["server"]["allowed_origins"] or [])
        return origin.rstrip("/") in allowed

    async def ws_handler(self, request):
        ip = request.remote
        if not self._origin_ok(request):
            log.warning("refused WebSocket from %s: origin %s", ip, request.headers.get("Origin"))
            raise web.HTTPForbidden(text="cross-origin WebSocket refused")
        if self.pending.get(ip, 0) >= PENDING_PER_IP:
            raise web.HTTPTooManyRequests(text="too many connections waiting to log in")
        ws = web.WebSocketResponse(heartbeat=10, max_msg_size=64 * 1024)
        await ws.prepare(request)
        c = Client(ws, ip)
        c.agent = request.headers.get("User-Agent", "")
        self.clients.add(c)
        self.pending[ip] = self.pending.get(ip, 0) + 1
        try:
            try:
                ok = await self._authenticate(c)
            finally:
                self.pending[ip] -= 1
                if not self.pending[ip]:
                    del self.pending[ip]
            if not ok:
                return ws
            log.info("%s (%s, %s) connected from %s", c.name, c.username, c.user["role"], c.remote)
            c.send_json({
                "type": "hello",
                "user": {"username": c.username, "display": c.name, "role": c.user["role"]},
                "radio_name": str(self.cfg["radio"]["name"]),
                "channels": self.radio.channel_list(),
                "radio_type": self.radio.kind,
                "can_sync": self.radio.can_sync,
                "can_tune": self.radio.can_tune,
                "band": list(getattr(self.radio, "band", ())) or None,
                "max_tx_seconds": self.cfg["ptt"]["max_tx_seconds"],
                "hold_interval_ms": max(100, self.cfg["ptt"]["hold_timeout_ms"] // 5),
                "wire_rate": self.cfg["audio"]["wire_rate"],
                "webrtc": self.rtc_ok,
                "frame_samples": self.cfg["audio"]["wire_rate"] * self.cfg["audio"]["frame_ms"] // 1000,
                "selcall": {
                    "standard": self.sc["standard"],
                    "tone_ms": self.sc["tone_ms"] or selcall.STANDARDS[self.sc["standard"]]["tone_ms"],
                    "max_digits": selcall.MAX_DIGITS,
                    "my_id": self.sc["my_id"],
                    "presets": self.sc["presets"],
                    "decode": bool(self.selcall_rx),
                    "log": list(self.selcall_log),
                } if self.sc["enabled"] else None,
                "voice_fx": voicefx.preset_list() if self.fx_on else None,
                "clips": {
                    "max_seconds": self.cl["max_seconds"],
                    "name_max": clips.NAME_MAX,
                    "max_files": int(self.cl["max_files"]),
                    "max_play_seconds": self._max_play_seconds(),
                    "record": bool(self.recorder),
                    "record_mode": self.cfg["record"]["mode"],
                    "record_minutes": float(self.cfg["record"]["max_minutes"]),
                    "list": self.clips.list(),
                } if self.clips else None,
                "bot": self.bot.hello() if self.bot else None,
            })
            self.broadcast_state()

            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    if c.control and self.radio.ptt_owner == c.id and not (c.rtc and c.rtc.ready):
                        self.audio.push_tx(self._voice(msg.data))
                elif msg.type == WSMsgType.TEXT:
                    self._handle(c, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(c)
            if self.radio.ptt_owner == c.id:
                self.radio.unkey("operator disconnected")
                self.audio.clear_tx()
            c.sender.cancel()
            if c.rtc:
                self._spawn(self._rtc_close(c))
            if c.authed:
                log.info("%s disconnected", c.name)
                self.audit(c, "disconnect")
                self.broadcast_state()
        return ws

    async def _authenticate(self, c):
        """The first message logs in, with a username and password or a session token:
            {"type": "auth", "username": "...", "password": "...", "remember": true}
            {"type": "auth", "session": "<token from an earlier login>"}
        Success answers with the account and a session token for reconnecting; the browser
        keeps the token (not the password), in localStorage only if 'remember' was ticked."""
        try:
            msg = await c.ws.receive(timeout=10)
            data = json.loads(msg.data) if msg.type == WSMsgType.TEXT else {}
            if not isinstance(data, dict) or data.get("type") != "auth":
                data = {}
        except (asyncio.TimeoutError, ValueError, TypeError):
            data = {}

        async def refuse(text, code="login"):
            await asyncio.sleep(1.0)  # slow down guessing
            c.send_json({"type": "auth", "ok": False, "code": code, "error": text})
            await asyncio.sleep(0.2)
            await c.ws.close()
            return False

        if data.get("session"):
            user, session = self.db.resume_session(str(data["session"]))
            if not user:
                self.audit(c, "session_refused")
                return await refuse("Your login has expired. Please log in again.", "session")
            token = None
        else:
            username = str(data.get("username") or "").strip().lower()[:32]
            wait = self.limiter.blocked(c.remote, username)
            if wait:
                self.db.log("login_blocked", username or None, c.remote)
                return await refuse(f"Too many failed logins. Try again in {int(wait // 60) + 1} min.", "locked")
            user, stored = self.db.hash_for(username)
            # scrypt is slow on purpose: run it off the event loop so the radio stays responsive
            ok = await asyncio.get_running_loop().run_in_executor(
                None, verify_password, str(data.get("password") or "")[:256], stored)
            if not (ok and user and user["active"]):
                self.limiter.failed(c.remote, username)
                self.db.log("login_failed", username or None, c.remote,
                            reason="disabled" if (ok and user) else "bad password")
                log.warning("failed login for %r from %s", username, c.remote)
                return await refuse("Wrong username or password")
            self.limiter.succeeded(c.remote, username)
            self.db.mark_login(user)
            token, session = self.db.new_session(user, c.remote, c.agent, bool(data.get("remember")))
        c.user, c.session, c.name = user, session, user["display"]
        c.authed = True
        self.audit(c, "login", via="session" if token is None else "password")
        c.send_json({"type": "auth", "ok": True, "name": c.name, "username": c.username,
                     "role": user["role"], "token": c.token, "session": token})
        return True

    async def _account_message(self, c, kind, m):
        """Messages every operator may send about their own account."""
        if kind == "logout":
            self.db.end_session(c.session)
            self.audit(c, "logout")
            await c.ws.close()
        elif kind == "change_password":
            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(None, verify_password, str(m.get("old") or "")[:256],
                                            self.db.get(user_id=c.user["id"])["pw_hash"])
            if not ok:
                self.audit(c, "password_change_failed")
                await asyncio.sleep(1.0)
                return c.send_json({"type": "password", "ok": False, "error": "Current password is wrong"})
            try:
                new = check_password(m.get("new"))
            except AuthError as e:
                return c.send_json({"type": "password", "ok": False, "error": str(e).capitalize()})
            pw_hash = await loop.run_in_executor(None, hash_password, new)
            self.db.set_pw_hash(c.user["id"], pw_hash, keep_session=c.session)
            self.audit(c, "password_changed")
            c.send_json({"type": "password", "ok": True})

    def _handle(self, c, text):
        try:
            m = json.loads(text)
            kind = m.get("type")
        except (ValueError, AttributeError):
            return

        if kind in ("logout", "change_password"):
            self._spawn(self._account_message(c, kind, m))
            return
        if kind in CONTROL_MESSAGES and not c.control:
            if kind != "ptt_hold":
                self.audit(c, "denied", action=kind)
                c.send_json({"type": "notice", "level": "warn", "code": "ptt_denied" if kind == "ptt" else "denied",
                             "text": "Listen-only account: you can't transmit or change the radio"})
            return

        if kind == "rtc_offer" and self.rtc_ok and isinstance(m.get("sdp"), str):
            self._spawn(self._rtc_offer(c, m["sdp"]))
            return
        if kind == "rtc_close":
            self._spawn(self._rtc_close(c))
            return
        if kind == "selcall":
            if self.sc["enabled"]:
                self._spawn(self._selcall_send(c, m.get("digits")))
            return
        if kind == "voice_fx":
            if self.fx_on:
                self._set_fx(c, m.get("preset"))
            return
        if kind == "record":
            if self.recorder:
                self._record(c, bool(m.get("on")), m.get("mode"))
            return
        if kind in ("clip_play", "clip_stop", "clip_delete", "clip_rename"):
            if self.clips:
                self._clip_message(c, kind, m.get("id"), m.get("name"))
            return
        if isinstance(kind, str) and kind.startswith("bot_"):
            if self.bot and kind in CONTROL_MESSAGES:
                text = self.bot.handle(c, kind, m)
                if text:
                    c.send_json({"type": "notice", "level": "warn", "text": text})
            return
        if kind == "ptt_hold":
            c.last_hold = time.monotonic()
        elif kind == "ptt":
            if m.get("on"):
                self._ptt_on(c)
            else:
                self._spawn(self._ptt_off(c))
        elif kind == "channel":
            action = m.get("action")
            try:
                if action == "up":
                    coro = self.radio.step(1)
                elif action == "down":
                    coro = self.radio.step(-1)
                elif action == "goto":
                    coro = self.radio.goto(int(m["channel"]))
                elif action == "tune" and self.radio.can_tune:
                    coro = self.radio.tune(float(m["freq"]), m.get("tx_freq") or None, m.get("tone") or None)
                elif action == "sync":
                    self.radio.sync(int(m["channel"]))
                    log.info("%s synced channel to %s", c.name, self.radio.channel)
                    self.audit(c, "channel_sync", channel=self.radio.channel)
                    return
                else:
                    return
            except (KeyError, ValueError, TypeError):
                return
            except RadioError as e:
                c.send_json({"type": "notice", "level": "warn", "text": str(e)})
                return
            self._spawn(self._channel(c, coro, action, m))

    async def _channel(self, c, coro, action=None, m=None):
        try:
            await coro
        except RadioError as e:
            c.send_json({"type": "notice", "level": "warn", "text": str(e)})
            return
        if action == "tune":
            info = self.radio.channel_info() or {}
            self.audit(c, "tune", freq=m.get("freq"), tx_freq=m.get("tx_freq"), tone=m.get("tone"), **{
                k: v for k, v in info.items() if k in ("freq",)})
        elif action:
            self.audit(c, "channel", channel=self.radio.channel, name=self.radio.channel_name(self.radio.channel))

    def _voice(self, pcm):
        """An operator's live voice on its way to the radio: their effect, then the tone control."""
        if self.tx_fx:
            pcm = self.tx_fx.pcm(pcm)
        return self.tx_eq.pcm(pcm)

    def _set_fx(self, c, preset):
        if preset is not None and preset not in voicefx.PRESETS:
            return
        c.fx = preset
        if self.radio.ptt_owner == c.id and not (self.selcall_tx or self.clip_tx or self.bot_tx):
            # Switched while talking: takes effect straight away.
            self.tx_fx = voicefx.VoiceFx(preset, int(self.cfg["audio"]["wire_rate"])) if preset else None
        log.info("%s voice effect: %s", c.name, preset or "off")
        self.audit(c, "voice_fx", preset=preset or "off")
        c.send_json(self.state_for(c))

    def _ptt_on(self, c):
        if self.selcall_tx or self.clip_tx or self.bot_tx:
            text = ("Sending a selcall" if self.selcall_tx else "A clip is playing on air" if self.clip_tx
                    else "The AI agent is talking (Stop in its panel cuts it off)")
            c.send_json({"type": "notice", "level": "warn", "code": "ptt_denied", "text": text})
            return
        try:
            if self.radio.ptt_owner != c.id:
                self.audio.clear_tx()
            self.radio.key(c.id)
        except RadioError as e:
            c.send_json({"type": "notice", "level": "warn", "code": "ptt_denied", "text": str(e)})
            return
        self._ptt_gen += 1
        self.tx_eq.reset()
        self.tx_fx = voicefx.VoiceFx(c.fx, int(self.cfg["audio"]["wire_rate"])) if c.fx else None
        c.tx_open = True
        if c.rtc and c.rtc.ready:
            w = self.cfg["webrtc"]
            self.audio.set_tx_buffer(w["tx_prebuffer_ms"], w["tx_max_ms"])
        else:
            self.audio.set_tx_buffer(self.cfg["audio"]["tx_prebuffer_ms"], 600)
        self.audio.drain(False)
        c.last_hold = time.monotonic()

    async def _ptt_off(self, c):
        if self.radio.ptt_owner != c.id or self.selcall_tx or self.clip_tx or self.bot_tx:
            return
        gen = self._ptt_gen
        if c.rtc and c.rtc.ready:
            # WebRTC audio travels separately from this message: let the last packets arrive.
            c.last_hold = time.monotonic()
            await asyncio.sleep(self.cfg["webrtc"]["release_ms"] / 1000)
            if gen != self._ptt_gen or self.radio.ptt_owner != c.id:
                return
            c.tx_open = False  # anything after this is the silence a browser sends between overs
        # Let audio already received finish playing before dropping PTT.
        self.audio.drain(True)
        deadline = time.monotonic() + self.cfg["ptt"]["tail_ms"] / 1000
        while self.audio.tx_buffered_ms() > 0 and time.monotonic() < deadline:
            c.last_hold = time.monotonic()
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.04)  # let the sound card play out its last block
        if gen == self._ptt_gen and self.radio.ptt_owner == c.id:
            self.radio.unkey()
            self.audio.clear_tx()

    # ---- selcall ----------------------------------------------------------

    async def _transmit(self, c, samples, lead_ms):
        """Key the radio for operator c, play float32 samples (wire rate) into its mic input after
        lead_ms of keyed silence, then unkey. Used for selcalls and clips.

        The caller has checked the radio is free and set selcall_tx or clip_tx, which stops PTT
        presses meanwhile. Returns True if everything played, False if it was cut short (stopped,
        the operator left, the watchdog's time limit). Raises RadioError if the radio won't key.
        """
        rate = int(self.cfg["audio"]["wire_rate"])
        lead = np.zeros(int(rate * lead_ms / 1000), np.float32)
        tail = np.zeros(int(rate * 0.05), np.float32)
        samples = np.concatenate([lead, samples, tail])
        self.audio.clear_tx()
        self.radio.key(c.id)
        self._ptt_gen += 1
        gen = self._ptt_gen
        self.broadcast_state()
        total_ms = len(samples) * 1000 / rate
        try:
            # Everything goes in at once: start playing straight away and keep all of it.
            self.audio.set_tx_buffer(0, total_ms + 1000)
            self.audio.drain(False)
            self.audio.push_tx((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            self.audio.drain(True)
            deadline = time.monotonic() + total_ms / 1000 + 2.0
            while (self.audio.tx_buffered_ms() > 0 and time.monotonic() < deadline
                   and gen == self._ptt_gen and self.radio.ptt_owner == c.id):
                c.last_hold = time.monotonic()  # keep the watchdog's keepalive check happy meanwhile
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.04)  # let the sound card play out its last block
            return gen == self._ptt_gen and self.radio.ptt_owner == c.id
        finally:
            if gen == self._ptt_gen and self.radio.ptt_owner == c.id:
                self.radio.unkey()
                self.audio.clear_tx()

    def _on_air(self):
        """Why the radio can't start a selcall or clip now, or None if it can."""
        if self.selcall_tx:
            return "A selcall is being sent"
        if self.clip_tx:
            return "A clip is playing on air"
        if self.bot_tx:
            return "The AI agent is talking"
        if self.radio.ptt_owner is not None:
            return "The radio is transmitting"
        return None

    async def _selcall_send(self, c, digits):
        """Send a selcall: tx_delay_ms of keyed silence for the transmitter to come up, then the
        tones, through the same TX audio path as voice (so audio.tx_gain applies)."""
        def refuse(text):
            c.send_json({"type": "notice", "level": "warn", "text": text})

        try:
            digits = selcall.check_digits(digits if isinstance(digits, str) else "")
        except ValueError as e:
            return refuse(str(e).capitalize())
        busy = self._on_air()
        if busy:
            return refuse(f"Can't send a selcall: {busy[0].lower()}{busy[1:]}")
        rate = int(self.cfg["audio"]["wire_rate"])
        tones = selcall.encode(digits, self.sc["standard"], rate, float(self.sc["level"]), self.sc["tone_ms"])
        self.selcall_tx = digits
        log.info("%s sending selcall %s (%s, %s ms tones)", c.name, digits, self.sc["standard"],
                 self.sc["tone_ms"] or selcall.STANDARDS[self.sc["standard"]]["tone_ms"])
        completed = False
        try:
            completed = await self._transmit(c, tones, int(self.sc["tx_delay_ms"]))
        except RadioError as e:
            return refuse(str(e))
        finally:
            self.selcall_tx = None
            self.broadcast_state()
        if completed:
            self._selcall_event("tx", digits, by=c.name)
        else:
            refuse("Selcall interrupted")

    def _selcall_event(self, direction, digits, by=None):
        """Record a sent or received selcall and tell every operator."""
        ev = {"type": "selcall", "dir": direction, "digits": digits, "by": by,
              "to_me": direction == "rx" and digits == self.sc["my_id"], "t": time.time()}
        self.selcall_log.append(ev)
        if direction == "rx":
            log.info("selcall received: %s%s", digits, " (this station)" if ev["to_me"] else "")
        for c in self._authed():
            c.send_json(ev)

    # ---- audio clips ----------------------------------------------------

    def _clips_changed(self):
        for c in self._authed():
            c.send_json({"type": "clips", "list": self.clips.list()})

    def _clip_message(self, c, kind, clip_id, name=None):
        def refuse(text):
            c.send_json({"type": "notice", "level": "warn", "text": text})

        if kind == "clip_stop":
            # Any operator may stop a clip, like anyone in the room could pull the plug.
            if self.clip_tx and self.radio.ptt_owner is not None:
                log.info("%s stopped clip %r", c.name, self.clip_tx["name"])
                self.audit(c, "clip_stop", clip=self.clip_tx["name"])
                self.clip_tx["stopped_by"] = c.name
                self._ptt_gen += 1  # ends _transmit's wait; it then leaves the unkeying to us
                self.radio.unkey()
                self.audio.clear_tx()
            return
        if not isinstance(clip_id, str) or not self.clips.get(clip_id):
            return refuse("That clip has been deleted")
        if kind == "clip_rename":
            try:
                old, clip = self.clips.rename(clip_id, name)
            except clips.ClipError as e:
                return refuse(str(e))
            if old != clip["name"]:
                if self.clip_tx and self.clip_tx["id"] == clip_id:
                    self.clip_tx["name"] = clip["name"]
                    self.broadcast_state()
                log.info("%s renamed clip %r to %r", c.name, old, clip["name"])
                self.audit(c, "clip_rename", clip=old, to=clip["name"])
                self._clips_changed()
            return
        if kind == "clip_delete":
            if self.clip_tx and self.clip_tx["id"] == clip_id:
                return refuse("Can't delete a clip while it is playing")
            clip = self.clips.delete(clip_id)
            log.info("%s deleted clip %r", c.name, clip["name"])
            self.audit(c, "clip_delete", clip=clip["name"])
            self._clips_changed()
            return
        busy = self._on_air()  # clip_play
        if busy:
            return refuse(f"Can't play a clip: {busy[0].lower()}{busy[1:]}")
        clip = self.clips.get(clip_id)
        if clip["seconds"] > self._max_play_seconds():
            return refuse(f"“{clip['name']}” is too long to transmit "
                          f"(the limit is {self._max_play_seconds():g} s, set by ptt.max_tx_seconds)")
        self.clip_tx = {"id": clip_id, "name": clip["name"]}  # claim the radio before any await
        self._spawn(self._clip_play(c, clip))

    def _max_play_seconds(self):
        """Longest clip that can go on air: it, its lead-in and a margin must fit in the TX limit."""
        return float(self.cfg["ptt"]["max_tx_seconds"]) - int(self.cl["tx_delay_ms"]) / 1000 - 2

    # ---- AI agent (server/bot/) -------------------------------------------

    def bot_channel_state(self):
        """(busy, on_air): whether the channel is in use, and why the radio can't key now, if not."""
        r = self.radio
        why = self._on_air() or ("Channel change in progress" if r.stepping else None) or r.error
        return self._busy(), why

    async def bot_transmit(self, samples, text, lead_ms):
        """Put one of the agent's replies on air, like a clip. Returns True if it all went out."""
        why = self.bot_channel_state()[1]
        if why:
            raise RadioError(why)
        op = self.bot.operator
        self.bot_tx = {"text": text}  # claim the radio before any await
        log.info("AI agent on air: %r", text)
        # The tone control, then back to the agent's own peak level (bot.level).
        x = self.tx_eq.whole(samples)
        peak, want = float(np.max(np.abs(x))) if len(x) else 0.0, float(np.max(np.abs(samples))) if len(samples) else 0.0
        if peak:
            x = x * (want / peak)
        try:
            return await self._transmit(op, x, lead_ms)
        finally:
            self.bot_tx = None
            self.broadcast_state()

    def bot_stop(self, c):
        """Any control operator can cut the agent off mid-sentence."""
        if self.bot_tx and self.radio.ptt_owner == self.bot.operator.id:
            log.info("%s stopped the AI agent", c.name)
            self.audit(c, "bot_stop", text=self.bot_tx["text"][:200])
            self._ptt_gen += 1  # ends _transmit's wait; it then leaves the unkeying to us
            self.radio.unkey("stopped by an operator")
            self.audio.clear_tx()

    def bot_broadcast(self, obj):
        for c in self._authed():
            c.send_json(obj)

    def bot_audit(self, c, event, **detail):
        self.audit(c, event, **detail)

    # ---- recording ------------------------------------------------------

    def _record(self, c, on, mode=None):
        try:
            if on:
                if not self.recorder.active:
                    self.audit(c, "record_start", mode=mode or self.cfg["record"]["mode"])
                self.recorder.start(c.name, mode)
            else:
                if self.recorder.active:
                    log.info("%s stopped recording", c.name)
                    self.audit(c, "record_stop")
                self.recorder.stop()
        except OSError as e:
            c.send_json({"type": "notice", "level": "warn", "text": f"Can't record: {e}"})

    def _record_changed(self, error=None):
        """From the recorder: started, stopped, a part was finished, or writing failed."""
        self.broadcast_state()
        self._clips_changed()
        if error:
            for o in self._authed():
                o.send_json({"type": "notice", "level": "error", "text": error})

    async def _clip_play(self, c, clip):
        job = self.clip_tx
        try:
            samples = self.clips.load(clip["id"], float(self.cl["level"]), self.tx_eq.whole)
            log.info("%s playing clip %r (%.1f s)", c.name, clip["name"], clip["seconds"])
            completed = await self._transmit(c, samples, int(self.cl["tx_delay_ms"]))
        except (OSError, RadioError, EOFError) as e:
            completed = None
            c.send_json({"type": "notice", "level": "warn", "text": f"Can't play the clip: {e}"})
        finally:
            self.clip_tx = None
            self.broadcast_state()
        if completed is False:
            who = job.get("stopped_by")
            text = f"Clip stopped by {who}" if who else "Clip interrupted"
            for o in self._authed():
                if who or o is c:
                    o.send_json({"type": "notice", "level": "warn", "text": text})

    async def clip_file(self, request):
        """GET /clips/<id>.wav?token=...  the clip's audio, to listen to or save. The token is in
        the query because an <audio> element or a link can't send headers."""
        token = request.query.get("token", "")
        if not self.clips or not any(hmac.compare_digest(x.token, token) for x in self._authed()):
            raise web.HTTPForbidden(text="Not logged in (reload the page)")
        clip = self.clips.get(request.match_info["id"])
        if not clip:
            raise web.HTTPNotFound(text="That clip has been deleted")
        name = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in clip["name"]).strip() or "clip"
        return web.FileResponse(self.clips.path(clip["id"]), headers={
            "Content-Type": "audio/wav", "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="{name}.wav"'})

    async def clip_upload(self, request):
        """POST /clips?name=...  body: mono 16-bit WAV at the wire rate, as made by the page.
        Authorised by the X-Radio-Token header (the token each operator gets when they log in)."""
        token = request.headers.get("X-Radio-Token", "")
        c = next((x for x in self._authed() if hmac.compare_digest(x.token, token)), None)
        if c is None:
            return web.json_response({"error": "Not logged in (reload the page)"}, status=403)
        if not c.control:
            self.audit(c, "denied", action="clip_upload")
            return web.json_response({"error": "Listen-only account: you can't add clips"}, status=403)
        if not self.clips:
            return web.json_response({"error": "Clips are turned off on this radio"}, status=404)
        if request.content_length and request.content_length > self.clips.max_bytes:
            return web.json_response({"error": f"Clip is longer than {self.cl['max_seconds']:g} s"}, status=413)
        try:
            data = bytearray()
            while chunk := await request.content.read(64 * 1024):
                data += chunk
                if len(data) > self.clips.max_bytes:
                    raise clips.ClipError(f"Clip is longer than {self.cl['max_seconds']:g} s")
            clip = self.clips.add(request.query.get("name", ""), bytes(data), c.name)
        except clips.ClipError as e:
            return web.json_response({"error": str(e)}, status=400)
        self.audit(c, "clip_upload", clip=clip["name"], seconds=clip["seconds"])
        self._clips_changed()
        return web.json_response(clip)

    # ---- audit: transmissions, account changes ----------------------------

    def _track_tx(self):
        """One audit entry per transmission, whoever or whatever unkeys it (release, time-out,
        lost connection, end of a selcall or clip): who, what, how long, which channel."""
        owner = self.radio.ptt_owner
        cur = self._tx
        if cur and cur["owner"] != owner:
            try:
                self.db.log("tx", cur["user"], cur["ip"], kind=cur["kind"],
                            seconds=round(time.monotonic() - cur["t0"], 1), channel=cur["channel"],
                            **({"what": cur["what"]} if cur["what"] else {}))
            except Exception as e:
                log.error("audit log: %s", e)
            self._tx = cur = None
        if owner is not None and not cur:
            c = self._client(owner)
            kind, what = "voice", None
            if self.selcall_tx:
                kind, what = "selcall", self.selcall_tx
            elif self.clip_tx:
                kind, what = "clip", self.clip_tx["name"]
            elif self.bot_tx:
                kind, what = "bot", self.bot_tx["text"][:200]
            elif c and c.fx:
                what = f"voice effect {c.fx}"
            self._tx = {"owner": owner, "user": c.username if c else None, "ip": c.remote if c else None,
                        "kind": kind, "what": what, "t0": time.monotonic(), "channel": self.radio.channel}

    def _recheck_accounts(self):
        """Apply radpi-users changes to connected operators: disabled or deleted accounts and
        ended sessions are disconnected; role changes take effect at once."""
        for c in list(self._authed()):
            try:
                u = self.db.get(user_id=c.user["id"])
                gone = not u or not u["active"] or not self.db.session_valid(c.session)
            except Exception as e:
                log.error("checking accounts: %s", e)
                return
            if gone:
                log.info("%s: account disabled or logged out elsewhere: disconnecting", c.name)
                c.send_json({"type": "notice", "level": "error", "code": "logged_out",
                             "text": "You have been logged out"})
                if self.radio.ptt_owner == c.id:
                    self.radio.unkey("account disabled")
                    self.audio.clear_tx()
                self._spawn(c.ws.close())
            elif u["role"] != c.user["role"] or u["display"] != c.user["display"]:
                if u["role"] != "control" and self.radio.ptt_owner == c.id:
                    self.radio.unkey("account changed to listen-only")
                    self.audio.clear_tx()
                c.user, c.name = u, u["display"]
                c.send_json({"type": "account", "role": u["role"], "display": u["display"]})
                self.broadcast_state()

    # ---- watchdog / meters ----------------------------------------------

    async def watchdog(self):
        p = self.cfg["ptt"]
        hold_timeout = p["hold_timeout_ms"] / 1000
        tick = 0
        while True:
            await asyncio.sleep(0.1)
            tick += 1
            r = self.radio
            if r.ptt_owner is not None:
                now = time.monotonic()
                c = self._client(r.ptt_owner)
                reason = None
                if c is None:
                    reason = "operator gone"
                elif now - c.last_hold > hold_timeout:
                    reason = "lost contact with operator"
                elif now - r.ptt_since > p["max_tx_seconds"]:
                    reason = f"transmit time limit ({p['max_tx_seconds']} s)"
                if reason:
                    r.unkey(reason)
                    self.audio.clear_tx()
                    if c:
                        c.send_json({"type": "notice", "level": "error", "code": "ptt_dropped",
                                     "text": f"PTT released: {reason}"})
            self._track_tx()
            if tick % (AUTH_RECHECK_S * 10) == 0:
                self._recheck_accounts()
            if tick % 36000 == 1:  # hourly
                try:
                    self.db.prune(self.cfg["server"]["audit_days"])
                except Exception as e:
                    log.error("pruning the audit log: %s", e)
            if tick % 2 == 0:  # 5 Hz meters
                meter = {"type": "meter", "rx_dbfs": round(self.audio.rx_dbfs, 1), "busy": self._busy()}
                if self.recorder and self.recorder.active:
                    meter["rec_captured"] = round(self.recorder.active["frames"] / self.recorder.rate, 1)
                rssi = self.radio.rssi
                if rssi is not None:
                    meter["rssi"] = rssi
                    off = self.cfg["sa818"]["rssi_offset_db"]
                    if off is not None:
                        meter["rssi_dbm"] = rssi + float(off)
                for c in self._authed():
                    c.send_json(meter)

    # ---- lifecycle -----------------------------------------------------

    async def on_startup(self, app):
        self.loop = asyncio.get_running_loop()
        self.audio.start()
        if self.recorder and self.recorder.was_recording:
            log.info("recording was on when the server stopped: carrying on")
            try:
                self.recorder.start(*self.recorder.resume_info())
            except OSError as e:
                log.error("can't resume recording: %s", e)
        app["watchdog"] = asyncio.create_task(self.watchdog())
        app["radio"] = asyncio.create_task(self.radio.run())
        app["bot"] = asyncio.create_task(self.bot.run()) if self.bot else None

    async def on_shutdown(self, app):
        self.radio.unkey("shutdown")
        for c in list(self.clients):
            if c.rtc:
                await self._rtc_close(c)
        for c in list(self.clients):
            await c.ws.close(code=1001, message=b"server shutdown")

    async def on_cleanup(self, app):
        for key in ("watchdog", "radio", "bot"):
            if app[key] is None:
                continue
            app[key].cancel()
            try:
                await app[key]
            except (asyncio.CancelledError, Exception):
                pass
        self.radio.close()
        self.audio.stop()
        if self.recorder:
            self.recorder.close()


async def _index(request):
    return web.FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@web.middleware
async def security_headers(request, handler):
    """Headers on every response: a strict content policy (only this server's own scripts and
    styles, nothing inline), no framing by other sites (stops clickjacking the PTT button), no
    content sniffing, no referrer, and the microphone allowed for this page only."""
    try:
        resp = await handler(request)
    except web.HTTPException as e:
        resp = e
    host = request.host
    resp.headers.setdefault("Content-Security-Policy", "; ".join((
        "default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self' data: blob:",
        "media-src 'self' blob:", f"connect-src 'self' ws://{host} wss://{host}", "font-src 'self'",
        "object-src 'none'", "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'")))
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.path.startswith("/static/"):
        # Check with us each time (a cheap 304 when unchanged). Without this, browsers guess a
        # freshness from the file's age and can keep last week's app.js after an update, which
        # then doesn't match the new page.
        resp.headers.setdefault("Cache-Control", "no-cache")
    if isinstance(resp, web.HTTPException):
        raise resp
    return resp


def build_app(cfg):
    head = RemoteHead(cfg)
    app = web.Application(middlewares=[security_headers])
    app.router.add_get("/", _index)
    app.router.add_get("/ws", head.ws_handler)
    app.router.add_post("/clips", head.clip_upload)
    app.router.add_get(r"/clips/{id:[0-9a-f]{12}}.wav", head.clip_file)
    app.router.add_static("/static", WEB_DIR)
    app.on_startup.append(head.on_startup)
    app.on_shutdown.append(head.on_shutdown)
    app.on_cleanup.append(head.on_cleanup)
    return app


def main():
    ap = argparse.ArgumentParser(description="RadPi: radio remote head server")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    cfg = load(args.config)
    s = cfg["server"]
    if not (s["tls_cert"] and s["tls_key"]) and s["host"] not in ("127.0.0.1", "localhost", "::1"):
        log.warning("no TLS: passwords and audio cross the network unencrypted unless a proxy such as "
                    "'tailscale serve' adds HTTPS in front of this server")
    ssl_ctx = None
    if s["tls_cert"] and s["tls_key"]:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(s["tls_cert"], s["tls_key"])

    web.run_app(build_app(cfg), host=s["host"], port=int(s["port"]), ssl_context=ssl_ctx,
                print=lambda *_: log.info("listening on %s://%s:%s",
                                          "https" if ssl_ctx else "http", s["host"], s["port"]))
