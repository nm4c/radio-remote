"""Remote head server: WebSocket control + audio, and the web UI."""

import argparse
import asyncio
import hmac
import itertools
import json
import logging
import ssl
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from .audio import create_audio
from .config import load
from . import rtc
from .radio import RadioError, create_radio

log = logging.getLogger("server")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
AUDIO_QUEUE_LIMIT = 25  # frames (~0.5 s) before we start dropping RX audio for a slow client


class Client:
    _ids = itertools.count(1)

    def __init__(self, ws, remote):
        self.id = next(Client._ids)
        self.ws = ws
        self.remote = remote
        self.name = f"Operator {self.id}"
        self.authed = False
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
        c.send_json({"type": "auth", "ok": True, "name": c.name})
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
        if self.radio.ptt_owner != c.id:
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
