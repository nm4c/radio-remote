"""WebRTC audio for an operator: Opus over UDP, negotiated over the existing WebSocket.

The WebSocket still carries login, PTT, channels and meters. Only audio moves here:
  radio RX -> RxTrack (one per operator)  -> browser
  browser mic -> remote track -> TX buffer -> radio  (only while that operator holds PTT)

Latency choices:
  - RX frames are queued at most `MAX_QUEUE` deep; older ones are dropped rather than delayed.
  - aiortc's audio jitter buffer holds 4 packets (80 ms) before releasing one and stalls for up to
    16 packets on a loss. It's replaced with a small one (webrtc.rx_jitter_packets): ~20 ms normally,
    bounded on loss.
  - Signalling is non-trickle: each side sends a complete SDP once ICE gathering is done. On a LAN
    or tailnet that takes a few ms.
"""

import asyncio
import fractions
import logging

import numpy as np

log = logging.getLogger("rtc")

try:
    import av
    from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
    from aiortc import rtcrtpreceiver
    from aiortc.jitterbuffer import JitterBuffer
    from aiortc.mediastreams import MediaStreamError, MediaStreamTrack

    AVAILABLE = True
except ImportError:  # aiortc not installed: operators fall back to WebSocket audio
    AVAILABLE = False

MAX_QUEUE = 3  # RX frames (60 ms) waiting for the encoder before old ones are dropped


def setup(cfg):
    """Apply global aiortc tweaks once. Returns False if WebRTC can't be used."""
    if not AVAILABLE or not cfg["webrtc"]["enabled"]:
        return False
    packets = int(cfg["webrtc"]["rx_jitter_packets"])
    if packets & (packets - 1) or not 2 <= packets <= 16:
        raise SystemExit("config: webrtc.rx_jitter_packets must be 2, 4, 8 or 16")

    def jitter_buffer(capacity, prefetch=0, is_video=False):
        if is_video:
            return JitterBuffer(capacity, prefetch, is_video)
        return JitterBuffer(capacity=packets, prefetch=1)

    rtcrtpreceiver.JitterBuffer = jitter_buffer
    return True


if AVAILABLE:

    class RxTrack(MediaStreamTrack):
        """Radio receive audio for one operator. Paced by the sound card: one frame per frame_ms."""

        kind = "audio"

        def __init__(self, rate):
            super().__init__()
            self.rate = rate
            self.time_base = fractions.Fraction(1, rate)
            self.pts = 0
            self.queue = asyncio.Queue()

        def feed(self, pcm):
            while self.queue.qsize() >= MAX_QUEUE:
                self.queue.get_nowait()  # behind: drop the oldest frame, keep latency down
            self.queue.put_nowait(pcm)

        async def recv(self):
            if self.readyState != "live":
                raise MediaStreamError
            pcm = await self.queue.get()
            samples = np.frombuffer(pcm, "<i2").reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate = self.rate
            frame.time_base = self.time_base
            frame.pts = self.pts
            self.pts += samples.shape[1]
            return frame


class RtcPeer:
    """One operator's WebRTC connection."""

    def __init__(self, cfg, on_tx_audio, on_state):
        w = cfg["webrtc"]
        servers = [RTCIceServer(**s) if isinstance(s, dict) else RTCIceServer(urls=s) for s in w["ice_servers"]]
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
        self.rate = int(cfg["audio"]["wire_rate"])
        self.rx = RxTrack(self.rate)
        self.pc.addTrack(self.rx)
        self.ready = False
        self._on_tx_audio = on_tx_audio
        self._on_state = on_state
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=self.rate)
        self._tasks = set()

        @self.pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                t = asyncio.create_task(self._consume(track))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)

        @self.pc.on("connectionstatechange")
        async def on_state():
            state = self.pc.connectionState
            log.info("WebRTC %s", state)
            self.ready = state == "connected"
            self._on_state(state)

    async def answer(self, sdp, kind="offer"):
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=kind))
        await self.pc.setLocalDescription(await self.pc.createAnswer())  # waits for ICE gathering
        return self.pc.localDescription.sdp

    def feed(self, pcm):
        if self.ready:
            self.rx.feed(pcm)

    async def _consume(self, track):
        """Browser mic: decode (aiortc gives 48 kHz stereo), convert to the wire format, hand on."""
        try:
            while True:
                frame = await track.recv()
                for out in self._resampler.resample(frame):
                    self._on_tx_audio(out.to_ndarray().astype("<i2").tobytes())
        except MediaStreamError:
            pass

    async def close(self):
        self.ready = False
        self.rx.stop()
        await self.pc.close()
