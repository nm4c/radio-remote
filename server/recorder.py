"""Continuous recording of received audio into the clip list.

While recording is on, every frame of received audio (after audio.rx_gain) goes into a WAV file
in the clips folder. When a file reaches record.max_minutes it is closed, added to the clip list
as a recording, and the next part starts at once, so nothing is lost at the joins. Stopping
closes the current part the same way.

Writes are batched to about once a second. Python's wave module rewrites the header's lengths on
every write, so a file cut off by a power failure is still readable; on the next start the
clip store adopts it (see ClipStore._adopt_orphans) and recording carries on if it was on.

Storage: 16 kHz 16-bit mono is about 1.9 MB a minute (115 MB an hour). The oldest recordings are
deleted once recordings together pass record.keep_mb, and recording stops if the disk gets down
to record.min_free_mb. Uploaded clips are never deleted automatically.
"""

import logging
import shutil
import time
import wave

log = logging.getLogger("recorder")
FLUSH_BYTES = 32000  # about 1 s at 16 kHz


class Recorder:
    def __init__(self, store, rate, cfg, on_change):
        """store: the ClipStore; cfg: the record: config section; on_change(): called (in the
        event loop) when recording starts or stops or a part is added to the list."""
        self.store = store
        self.rate = rate
        self.max_frames = int(float(cfg["max_minutes"]) * 60 * rate)
        self.keep_bytes = int(float(cfg["keep_mb"]) * 1e6)
        self.min_free = int(float(cfg["min_free_mb"]) * 1e6)
        self.on_change = on_change
        self.marker = store.dir / "recording.on"  # present while recording, so it resumes after a restart
        self.active = None   # {"by", "since"} while recording
        self.part = None     # current file: {"id", "wave", "frames", "started"}
        self.pending = bytearray()

    @property
    def was_recording(self):
        return self.marker.exists()

    def status(self):
        if not self.active:
            return None
        return {"by": self.active["by"], "seconds": round(time.time() - self.active["since"], 1),
                "part_seconds": round(self.part["frames"] / self.rate, 1) if self.part else 0}

    def start(self, by):
        if self.active:
            return
        self._check_space()
        self.active = {"by": by, "since": time.time()}
        self.marker.write_text(by)
        self._open_part()
        log.info("%s started recording", by)
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

    def feed(self, pcm):
        """Received audio, int16 LE mono at the wire rate. Called in the event loop."""
        if not self.active:
            return
        self.pending += pcm
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
