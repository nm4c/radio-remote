"""Recording what happens on the channel into the clip list.

Two modes, chosen when recording starts (record.mode is the default):
  continuous  everything, silence included.
  vox         only while the channel is active: the busy line (or the RX level, without one), or
              while we transmit. Each over keeps record.vox_preroll_ms from before it started,
              so its first syllable isn't cut, and recording runs on for record.vox_hang_ms
              after it, so short pauses don't chop it up. Overs follow each other with a short
              gap (vox_gap_ms) instead of the silence between them.

With record.include_tx (the default), while the radio is keyed the recording takes what we
send (voice after any effect, selcalls, clips) from the sound card output, since the radio's
receiver is muted then. So both sides of a conversation end up in the recording, in order.

Files: audio is written to a WAV part in the clips folder. When a part reaches
record.max_minutes it is closed, added to the clip list as a recording, and the next part starts
with no gap; stopping closes the current part the same way. In vox mode a part is also closed
after record.vox_close_idle_minutes with nothing to record, so a quiet evening's overs show up
in the list without waiting for 15 minutes of talk. Writes are batched to about once a second;
Python's wave module rewrites the header on every write, so a part cut off by a power failure
is still readable, and the clip store adopts it on the next start (ClipStore._adopt_orphans).

Storage: 16 kHz 16-bit mono is about 1.9 MB a minute (115 MB an hour). The oldest recordings are
deleted once recordings together pass record.keep_mb, and recording stops if the disk gets down
to record.min_free_mb. Uploaded clips are never deleted automatically.
"""

import json
import logging
import shutil
import time
import wave

log = logging.getLogger("recorder")
FLUSH_BYTES = 32000  # about 1 s at 16 kHz
MODES = ("continuous", "vox")


class Recorder:
    def __init__(self, store, rate, cfg, on_change):
        """store: the ClipStore; cfg: the record: config section; on_change(error=None): called
        (in the event loop) when recording starts or stops, a part is added, or writing fails."""
        self.store = store
        self.rate = rate
        self.max_frames = int(float(cfg["max_minutes"]) * 60 * rate)
        self.keep_bytes = int(float(cfg["keep_mb"]) * 1e6)
        self.min_free = int(float(cfg["min_free_mb"]) * 1e6)
        self.default_mode = cfg["mode"]
        self.include_tx = bool(cfg["include_tx"])
        ms = lambda key: int(rate * float(cfg[key]) / 1000) * 2  # noqa: E731  (bytes of int16 audio)
        self.hang_bytes = ms("vox_hang_ms")
        self.preroll_bytes = ms("vox_preroll_ms")
        self.gap = bytes(ms("vox_gap_ms"))
        self.close_idle_bytes = int(float(cfg["vox_close_idle_minutes"]) * 60 * rate) * 2
        self.on_change = on_change
        self.marker = store.dir / "recording.on"  # present while recording, so it resumes after a restart
        self.active = None   # {"by", "since", "mode", "frames"} while recording
        self.part = None     # current file: {"id", "wave", "frames", "started"}
        self.pending = bytearray()
        # vox state
        self.preroll = bytearray()   # the most recent quiet audio, kept to start the next over with
        self.hang_left = 0           # bytes still to record after activity stopped
        self.idle_bytes = None       # quiet audio since the last over ended (None: no over yet)

    # ---- control -----------------------------------------------------------

    @property
    def was_recording(self):
        return self.marker.exists()

    def resume_info(self):
        """(by, mode) from the marker a running recording leaves behind."""
        text = self.marker.read_text().strip()
        try:
            info = json.loads(text)
            return info.get("by") or "restart", info.get("mode") or self.default_mode
        except ValueError:          # written by an older version: just the name
            return text or "restart", self.default_mode

    def status(self):
        if not self.active:
            return None
        return {"by": self.active["by"], "mode": self.active["mode"],
                "seconds": round(time.time() - self.active["since"], 1),
                "captured": round(self.active["frames"] / self.rate, 1)}

    def start(self, by, mode=None):
        if self.active:
            return
        mode = mode if mode in MODES else self.default_mode
        self._check_space()
        self.active = {"by": by, "since": time.time(), "mode": mode, "frames": 0}
        self.marker.write_text(json.dumps({"by": by, "mode": mode}))
        self.preroll.clear()
        self.hang_left, self.idle_bytes = 0, None
        log.info("%s started recording (%s)", by, mode)
        self.on_change()

    def stop(self, why=None):
        if not self.active:
            return
        self._close_part()
        self.active = None
        self.marker.unlink(missing_ok=True)
        log.info("recording stopped%s", f" ({why})" if why else "")
        self.on_change()

    def close(self):
        """Server shutdown: finish the file but leave the marker, so recording resumes."""
        if self.active:
            self._close_part()
            self.active = None

    # ---- audio -------------------------------------------------------------

    def feed(self, pcm, active=True):
        """One frame of channel audio (int16 LE mono at the wire rate), from the event loop.
        active: something is happening on the channel (busy, or we're transmitting)."""
        if not self.active:
            return
        if self.active["mode"] == "continuous":
            return self._write(pcm)
        if active:
            if not self.hang_left:                       # an over starts
                if self.part and self.part["frames"]:
                    self._write(self.gap)
                self._write(bytes(self.preroll))
                self.preroll.clear()
            self._write(pcm)
            self.hang_left = self.hang_bytes
        elif self.hang_left:                             # running on through a short pause
            self._write(pcm)
            self.hang_left = max(0, self.hang_left - len(pcm))
            if not self.hang_left:
                self.idle_bytes = 0
                self._flush()
        else:                                            # quiet: just remember the last moment
            self.preroll += pcm
            if len(self.preroll) > self.preroll_bytes:
                del self.preroll[:len(self.preroll) - self.preroll_bytes]
            if self.idle_bytes is not None:
                self.idle_bytes += len(pcm)              # counted in audio, not wall-clock time
                if self.part and self.idle_bytes >= self.close_idle_bytes:
                    self._close_part()                   # long quiet spell: list what we have
                    self.idle_bytes = None

    def _write(self, data):
        if not data or not self.active:   # nothing to write, or recording stopped on an error
            return
        if not self.part:
            try:
                self._check_space()
                self._open_part()
            except OSError as e:
                return self._fail(e)
        self.pending += data
        self.active["frames"] += len(data) // 2
        if len(self.pending) >= FLUSH_BYTES:
            self._flush()

    # ---- files -------------------------------------------------------------

    def _check_space(self):
        free = shutil.disk_usage(self.store.dir).free
        if free < self.min_free:
            raise OSError(f"only {free / 1e6:.0f} MB free on the Pi's disk")

    def _open_part(self):
        clip_id = self.store.new_id()
        w = wave.open(str(self.store.path(clip_id)), "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(self.rate)
        self.part = {"id": clip_id, "wave": w, "frames": 0, "started": time.time()}

    def _close_part(self):
        self._flush(final=True)
        part, self.part = self.part, None
        if not part:
            return
        part["wave"].close()
        if part["frames"] < self.rate:  # under a second: not worth a list entry
            self.store.path(part["id"]).unlink(missing_ok=True)
            return
        self.store.add_recording(part["id"], part["started"], part["frames"] / self.rate, self.active["by"])
        for gone in self.store.prune_recordings(self.keep_bytes):
            log.info("deleted old recording %r to stay under record.keep_mb", gone["name"])
        self.on_change()

    def _flush(self, final=False):
        if not self.part or not self.pending:
            return
        data = bytes(self.pending)
        self.pending.clear()
        try:
            while data:
                room = (self.max_frames - self.part["frames"]) * 2
                chunk, data = data[:room], data[room:]
                self.part["wave"].writeframes(chunk)
                self.part["frames"] += len(chunk) // 2
                if self.part["frames"] >= self.max_frames:
                    self._close_part()  # pending is empty, so this doesn't recurse into the data
                    self._check_space()
                    self._open_part()
            if not final:
                self._check_space()
        except OSError as e:
            self._fail(e)

    def _fail(self, e):
        log.error("recording failed: %s", e)
        self.pending.clear()
        if self.part:
            try:
                self._close_part()
            except OSError:
                pass
        self.active = None
        self.marker.unlink(missing_ok=True)
        self.on_change(error=f"Recording stopped: {e}")
