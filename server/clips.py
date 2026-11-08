"""Audio clips: short recordings that operators upload and transmit from the control head.

The browser decodes whatever the operator picked (MP3, M4A, WAV...) with its own audio decoder
and uploads the result as a mono 16-bit WAV at the wire rate, so the Pi needs no codecs. Here
the clips are checked, stored as <id>.wav in clips.dir, and listed in clips.json beside them:

    [{"id": "3f9c1a2b7d4e", "name": "Net opening", "seconds": 12.4, "by": "VK3ABC", "added": 1731...,
      "kind": "upload"}]

kind is "upload" for a file an operator added, or "rec" for a part of a recording of received
audio (see recorder.py). max_files limits uploads only; recordings have their own limit by size.

Clips are stored as uploaded. Their level is evened out when they are played (see load), so a
quiet recording and a loud one go to air at the same peak level.
"""

import io
import json
import logging
import secrets
import time
import unicodedata
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("clips")
NAME_MAX = 40


class ClipError(ValueError):
    """A clip was refused; the message is shown to the operator."""


def clean_name(name):
    """A clip name fit for display: printable characters only, trimmed, NAME_MAX at most."""
    text = "".join(ch for ch in str(name or "") if unicodedata.category(ch)[0] not in "CZ" or ch == " ")
    return " ".join(text.split())[:NAME_MAX] or "Clip"


class ClipStore:
    def __init__(self, directory, rate, max_seconds, max_files):
        self.dir = Path(directory)
        self.rate = rate
        self.max_seconds = max_seconds
        self.max_files = max_files
        self.index_file = self.dir / "clips.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clips = self._load_index()
        self._adopt_orphans()

    @property
    def max_bytes(self):
        """Largest upload accepted: a max_seconds WAV plus room for its header."""
        return int(self.max_seconds * self.rate) * 2 + 1024

    def _load_index(self):
        try:
            data = json.loads(self.index_file.read_text())
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            log.warning("could not read %s (%s); starting with no clips", self.index_file, e)
            return []
        # Keep only entries whose audio is still there.
        clips = [c for c in data if isinstance(c, dict) and self.path(c.get("id")).is_file()]
        for c in clips:
            c.setdefault("kind", "upload")
        return clips

    def _adopt_orphans(self):
        """List WAV files that aren't in the index yet: a recording part that was still being
        written when the Pi lost power or the server was killed."""
        known = {c["id"] for c in self.clips}
        found = False
        for f in sorted(self.dir.glob("*.wav"), key=lambda f: f.stat().st_mtime):
            if f.stem in known:
                continue
            try:
                with wave.open(str(f)) as w:
                    seconds = w.getnframes() / w.getframerate()
            except (wave.Error, EOFError, OSError):
                seconds = 0
            if seconds < 1:
                f.unlink(missing_ok=True)
                continue
            started = f.stat().st_mtime - seconds
            self.clips.append(self._recording_entry(f.stem, started, seconds, None))
            log.info("recovered recording %s (%.0f s)", f.name, seconds)
            found = True
        if found:
            self._save_index()

    def new_id(self):
        return secrets.token_hex(6)

    def path(self, clip_id):
        return self.dir / f"{clip_id}.wav"

    def _save_index(self):
        tmp = self.index_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.clips, indent=1))
        tmp.replace(self.index_file)

    def list(self):
        return [dict(c) for c in self.clips]

    def get(self, clip_id):
        return next((c for c in self.clips if c["id"] == clip_id), None)

    def add(self, name, data, by):
        """Check and store an uploaded WAV. Returns the new clip's entry."""
        if sum(c["kind"] == "upload" for c in self.clips) >= self.max_files:
            raise ClipError(f"There are already {self.max_files} clips. Delete one first.")
        try:
            with wave.open(io.BytesIO(data)) as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != self.rate:
                    raise ClipError(f"Clip must be mono 16-bit WAV at {self.rate} Hz "
                                    "(the page converts files itself; reload it and try again)")
                frames = w.getnframes()
                pcm = w.readframes(frames)
        except (wave.Error, EOFError) as e:
            raise ClipError("Not a usable WAV file" + (f" ({e})" if str(e) else "")) from None
        seconds = frames / self.rate
        if frames == 0 or not np.any(np.frombuffer(pcm, "<i2")):
            raise ClipError("The clip is silent")
        if seconds > self.max_seconds + 0.05:
            raise ClipError(f"Clip is {seconds:.0f} s long; the limit is {self.max_seconds:g} s")
        clip = {"id": self.new_id(), "name": clean_name(name), "seconds": round(seconds, 1),
                "by": by, "added": time.time(), "kind": "upload"}
        path = self.path(clip["id"])
        with wave.open(str(path), "wb") as w:  # rewritten so only clean, plain WAV is kept
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(pcm)
        self.clips.append(clip)
        self._save_index()
        log.info("%s added clip %r (%.1f s)", by, clip["name"], seconds)
        return clip

    @staticmethod
    def _recording_entry(clip_id, started, seconds, by):
        name = time.strftime("Recording %d %b %H:%M:%S", time.localtime(started))
        return {"id": clip_id, "name": name, "seconds": round(seconds, 1), "by": by, "added": started,
                "kind": "rec"}

    def add_recording(self, clip_id, started, seconds, by):
        """List a finished recording part (its file is already in place)."""
        clip = self._recording_entry(clip_id, started, seconds, by)
        self.clips.append(clip)
        self._save_index()
        return clip

    def prune_recordings(self, keep_bytes):
        """Delete the oldest recordings until recordings take at most keep_bytes. Returns them."""
        recs = sorted((c for c in self.clips if c["kind"] == "rec"), key=lambda c: c["added"])
        sizes = {c["id"]: self.path(c["id"]).stat().st_size for c in recs}
        total = sum(sizes.values())
        gone = []
        for c in recs:
            if total <= keep_bytes:
                break
            total -= sizes[c["id"]]
            gone.append(self.delete(c["id"]))
        return gone

    def delete(self, clip_id):
        clip = self.get(clip_id)
        if not clip:
            raise ClipError("That clip has already been deleted")
        self.clips.remove(clip)
        self._save_index()
        self.path(clip_id).unlink(missing_ok=True)
        return clip

    def load(self, clip_id, level):
        """The clip as float32 samples at the wire rate, scaled so its peak is `level` (0-1)."""
        with wave.open(str(self.path(clip_id))) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
        peak = float(np.max(np.abs(x))) if len(x) else 0.0
        return x * (level / peak) if peak else x
