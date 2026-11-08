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
from . import clips, rtc, selcall
from .radio import RadioError, create_radio

log = logging.getLogger("server")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
AUDIO_QUEUE_LIMIT = 25  # frames (~0.5 s) before we start dropping RX audio for a slow client
SELCALL_LOG = 20        # recent selcalls (sent and received) kept for operators who join later


class Client:
    _ids = itertools.count(1)

    def __init__(self, ws, remote):
        self.id = next(Client._ids)
        self.ws = ws
        self.remote = remote
        self.name = f"Operator {self.id}"
        self.authed = False
        self.token = secrets.token_urlsafe(18)  # authorises this operator's clip uploads (HTTP)
        self.last_hold = 0.0
        self.queue = asyncio.Queue()
        self.audio_queued = 0
        self.rtc = None  # RtcPeer once the browser has set up WebRTC audio
        self.tx_open = False  # accepting this operator's WebRTC mic audio (PTT held, or just released)
        self.sender = asyncio.create_task(self._send_loop())

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
        self.audio = create_audio(cfg, self._on_rx_audio)
        self._tasks = set()
        self._ptt_gen = 0
        self.rtc_ok = rtc.setup(cfg)
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

    # ---- helpers -------------------------------------------------------

    def _authed(self):
        return [c for c in self.clients if c.authed]

    def _client(self, cid):
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
            self.audio.push_tx(pcm)

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

    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=10, max_msg_size=64 * 1024)
        await ws.prepare(request)
        c = Client(ws, request.remote)
        self.clients.add(c)
        try:
            if not await self._authenticate(c):
                return ws
            log.info("%s connected from %s", c.name, c.remote)
            c.send_json({
                "type": "hello",
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
                "clips": {
                    "max_seconds": self.cl["max_seconds"],
                    "max_files": int(self.cl["max_files"]),
                    "list": self.clips.list(),
                } if self.clips else None,
            })
            self.broadcast_state()

            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    if self.radio.ptt_owner == c.id and not (c.rtc and c.rtc.ready):
                        self.audio.push_tx(msg.data)
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
                self.broadcast_state()
        return ws

    async def _authenticate(self, c):
        try:
            msg = await c.ws.receive(timeout=10)
            data = json.loads(msg.data) if msg.type == WSMsgType.TEXT else {}
        except (asyncio.TimeoutError, ValueError):
            data = {}
        password = str(data.get("password", ""))
        expected = str(self.cfg["server"]["password"])
        if data.get("type") != "auth" or not hmac.compare_digest(password.encode(), expected.encode()):
            log.warning("failed login from %s", c.remote)
            await asyncio.sleep(1.0)  # slow down guessing
            c.send_json({"type": "auth", "ok": False, "error": "Wrong password"})
            await asyncio.sleep(0.2)
            await c.ws.close()
            return False
        name = str(data.get("name") or "").strip()[:24]
        if name:
            c.name = name
        c.authed = True
        c.send_json({"type": "auth", "ok": True, "name": c.name, "token": c.token})
        return True

    def _handle(self, c, text):
        try:
            m = json.loads(text)
            kind = m.get("type")
        except (ValueError, AttributeError):
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
        if kind in ("clip_play", "clip_stop", "clip_delete"):
            if self.clips:
                self._clip_message(c, kind, m.get("id"))
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
                    return
                else:
                    return
            except (KeyError, ValueError, TypeError):
                return
            except RadioError as e:
                c.send_json({"type": "notice", "level": "warn", "text": str(e)})
                return
            self._spawn(self._channel(c, coro))

    async def _channel(self, c, coro):
        try:
            await coro
        except RadioError as e:
            c.send_json({"type": "notice", "level": "warn", "text": str(e)})

    def _ptt_on(self, c):
        if self.selcall_tx or self.clip_tx:
            text = "Sending a selcall" if self.selcall_tx else "A clip is playing on air"
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
        c.tx_open = True
        if c.rtc and c.rtc.ready:
            w = self.cfg["webrtc"]
            self.audio.set_tx_buffer(w["tx_prebuffer_ms"], w["tx_max_ms"])
        else:
            self.audio.set_tx_buffer(self.cfg["audio"]["tx_prebuffer_ms"], 600)
        self.audio.drain(False)
        c.last_hold = time.monotonic()

    async def _ptt_off(self, c):
        if self.radio.ptt_owner != c.id or self.selcall_tx or self.clip_tx:
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

    def _clip_message(self, c, kind, clip_id):
        def refuse(text):
            c.send_json({"type": "notice", "level": "warn", "text": text})

        if kind == "clip_stop":
            # Any operator may stop a clip, like anyone in the room could pull the plug.
            if self.clip_tx and self.radio.ptt_owner is not None:
                log.info("%s stopped clip %r", c.name, self.clip_tx["name"])
                self.clip_tx["stopped_by"] = c.name
                self._ptt_gen += 1  # ends _transmit's wait; it then leaves the unkeying to us
                self.radio.unkey()
                self.audio.clear_tx()
            return
        if not isinstance(clip_id, str) or not self.clips.get(clip_id):
            return refuse("That clip has been deleted")
        if kind == "clip_delete":
            if self.clip_tx and self.clip_tx["id"] == clip_id:
                return refuse("Can't delete a clip while it is playing")
            clip = self.clips.delete(clip_id)
            log.info("%s deleted clip %r", c.name, clip["name"])
            self._clips_changed()
            return
        busy = self._on_air()  # clip_play
        if busy:
            return refuse(f"Can't play a clip: {busy[0].lower()}{busy[1:]}")
        clip = self.clips.get(clip_id)
        self.clip_tx = {"id": clip_id, "name": clip["name"]}  # claim the radio before any await
        self._spawn(self._clip_play(c, clip))

    async def _clip_play(self, c, clip):
        job = self.clip_tx
        try:
            samples = self.clips.load(clip["id"], float(self.cl["level"]))
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

    async def clip_upload(self, request):
        """POST /clips?name=...  body: mono 16-bit WAV at the wire rate, as made by the page.
        Authorised by the X-Radio-Token header (the token each operator gets when they log in)."""
        token = request.headers.get("X-Radio-Token", "")
        c = next((x for x in self._authed() if hmac.compare_digest(x.token, token)), None)
        if c is None:
            return web.json_response({"error": "Not logged in (reload the page)"}, status=403)
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
        self._clips_changed()
        return web.json_response(clip)

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
            if tick % 2 == 0:  # 5 Hz meters
                meter = {"type": "meter", "rx_dbfs": round(self.audio.rx_dbfs, 1), "busy": self._busy()}
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
        app["watchdog"] = asyncio.create_task(self.watchdog())
        app["radio"] = asyncio.create_task(self.radio.run())

    async def on_shutdown(self, app):
        self.radio.unkey("shutdown")
        for c in list(self.clients):
            if c.rtc:
                await self._rtc_close(c)
        for c in list(self.clients):
            await c.ws.close(code=1001, message=b"server shutdown")

    async def on_cleanup(self, app):
        for key in ("watchdog", "radio"):
            app[key].cancel()
            try:
                await app[key]
            except (asyncio.CancelledError, Exception):
                pass
        self.radio.close()
        self.audio.stop()


async def _index(request):
    return web.FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


def build_app(cfg):
    head = RemoteHead(cfg)
    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_get("/ws", head.ws_handler)
    app.router.add_post("/clips", head.clip_upload)
    app.router.add_static("/static", WEB_DIR)
    app.on_startup.append(head.on_startup)
    app.on_shutdown.append(head.on_shutdown)
    app.on_cleanup.append(head.on_cleanup)
    return app


def main():
    ap = argparse.ArgumentParser(description="Radio remote head server")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    cfg = load(args.config)
    if cfg["server"]["password"] == "change-me":
        log.warning("*** server.password is still 'change-me' - set a real password in %s ***", args.config)

    s = cfg["server"]
    ssl_ctx = None
    if s["tls_cert"] and s["tls_key"]:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(s["tls_cert"], s["tls_key"])

    web.run_app(build_app(cfg), host=s["host"], port=int(s["port"]), ssl_context=ssl_ctx,
                print=lambda *_: log.info("listening on %s://%s:%s",
                                          "https" if ssl_ctx else "http", s["host"], s["port"]))
