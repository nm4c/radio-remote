"""Who is talking: voiceprints, and the people the agent knows.

Each over is turned into an embedding (a vector that describes the voice, not the words) and
compared with every person's voiceprint, the average of their recent embeddings. The best
match above `speaker.match` is that person; above `speaker.update` the over is also added to
their voiceprint, so it follows their voice over different radios and conditions.

People come in three kinds:
  known       enrolled by an operator (named in the web page or with  python -m server.bot enrol)
  acquainted  was anonymous, then said a name the LLM picked up
  anonymous   an unfamiliar voice, kept under a pseudonym ("Caller 7") when bot.learn_anonymous
              is on and the over was long enough (speaker.min_enrol_s) to start a voiceprint

Files, in <bot.dir>/people/:
  people.json      [{"id", "name", "kind", "created", "last_seen", "overs"}]
  <id>.npy         that person's voiceprint samples (one embedding per row)
  <id>.md          notes: written by an operator or by the agent itself; plain text, editable

Embedding backends (bot.speaker.backend):
  ecapa   SpeechBrain's ECAPA-TDNN speaker model (pip install speechbrain torch torchaudio). Good.
  basic   statistics of the log-mel spectrum, numpy only. Rough: fine for testing the pipeline
          with a couple of very different voices, not for real use.
"""

import json
import logging
import secrets
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("bot.speakers")

DEFAULT_THRESHOLDS = {"ecapa": (0.60, 0.72), "basic": (0.96, 0.98)}  # (match, update)
NOTES_MAX = 4000  # characters of notes kept per person (older lines are trimmed off the top)


# ---- embeddings -------------------------------------------------------------------

class EcapaEmbedder:
    def __init__(self, model_dir):
        from speechbrain.inference.speaker import EncoderClassifier  # optional dependency
        import torch
        self.torch = torch
        log.info("loading the ECAPA speaker model (first run downloads about 80 MB)")
        self.model = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                                    savedir=str(model_dir), run_opts={"device": "cpu"})

    def embed(self, samples):
        with self.torch.no_grad():
            e = self.model.encode_batch(self.torch.from_numpy(samples)[None])
        return e.squeeze().cpu().numpy().astype(np.float32)


class BasicEmbedder:
    """Mean and spread of 40 log-mel bands over the voiced frames. Numpy only."""

    def __init__(self, rate):
        self.rate = rate
        self.n_fft = 512
        self.mel = self._mel_bank(40, self.n_fft, rate, 100, min(4000, rate / 2))

    @staticmethod
    def _mel_bank(n, n_fft, rate, lo, hi):
        mel = lambda f: 2595 * np.log10(1 + f / 700)  # noqa: E731
        inv = lambda m: 700 * (10 ** (m / 2595) - 1)  # noqa: E731
        pts = inv(np.linspace(mel(lo), mel(hi), n + 2))
        bins = np.floor((n_fft + 1) * pts / rate).astype(int)
        bank = np.zeros((n, n_fft // 2 + 1), np.float32)
        for i in range(n):
            a, b, c = bins[i], bins[i + 1], bins[i + 2]
            if b > a:
                bank[i, a:b] = (np.arange(a, b) - a) / (b - a)
            if c > b:
                bank[i, b:c] = (c - np.arange(b, c)) / (c - b)
        return bank

    def embed(self, samples):
        hop = self.n_fft // 2
        if len(samples) < self.n_fft:
            samples = np.pad(samples, (0, self.n_fft - len(samples)))
        frames = np.lib.stride_tricks.sliding_window_view(samples, self.n_fft)[::hop] * np.hanning(self.n_fft)
        spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
        logmel = np.log(spec @ self.mel.T + 1e-8)
        energy = logmel.mean(axis=1)
        voiced = logmel[energy > np.percentile(energy, 40)]
        if len(voiced) < 4:
            voiced = logmel
        voiced = voiced - voiced.mean(axis=1, keepdims=True)  # level-independent
        return np.concatenate([voiced.mean(axis=0), voiced.std(axis=0)]).astype(np.float32)


def create_embedder(cfg, rate, bot_dir):
    if cfg["backend"] == "ecapa":
        return EcapaEmbedder(cfg["model_dir"] or Path(bot_dir) / "models" / "ecapa")
    return BasicEmbedder(rate)


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n else v


# ---- people -----------------------------------------------------------------------

class People:
    """The people the agent knows. Used from the event loop and the worker thread, so locked."""

    def __init__(self, directory, cfg, backend):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = self.dir / "people.json"
        dm, du = DEFAULT_THRESHOLDS[backend]
        self.match = float(cfg["match"]) if cfg["match"] is not None else dm
        self.update = float(cfg["update"]) if cfg["update"] is not None else du
        self.max_samples = int(cfg["max_samples"])
        self.lock = threading.RLock()
        self.people = self._load()
        self.samples = {}
        for p in self.people:
            try:
                self.samples[p["id"]] = np.load(self.dir / f"{p['id']}.npy")
            except (OSError, ValueError):
                self.samples[p["id"]] = None

    def _load(self):
        try:
            data = json.loads(self.index.read_text())
            return [p for p in data if isinstance(p, dict) and p.get("id")]
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            log.warning("could not read %s (%s): starting with nobody", self.index, e)
            return []

    def _save(self):
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.people, indent=1))
        tmp.replace(self.index)

    def _save_samples(self, pid):
        s = self.samples.get(pid)
        if s is not None:
            np.save(self.dir / f"{pid}.npy", s)

    def list(self):
        with self.lock:
            return [dict(p) for p in self.people]

    def get(self, pid):
        with self.lock:
            return next((p for p in self.people if p["id"] == pid), None)

    def find_name(self, name):
        with self.lock:
            key = name.strip().lower()
            return next((p for p in self.people if p["name"].lower() == key and p["kind"] != "anonymous"), None)

    def identify(self, emb):
        """(person, score) of the best match above the threshold, else (None, best score)."""
        e = _unit(emb)
        best, score = None, -1.0
        with self.lock:
            for p in self.people:
                s = self.samples.get(p["id"])
                if s is None or not len(s):
                    continue
                sim = float(np.dot(_unit(s.mean(axis=0)), e))
                if sim > score:
                    best, score = p, sim
        return (best, score) if best is not None and score >= self.match else (None, score)

    def add_sample(self, pid, emb):
        with self.lock:
            s = self.samples.get(pid)
            row = _unit(emb)[None]
            s = row if s is None or not len(s) or s.shape[1] != row.shape[1] else np.vstack([s, row])
            self.samples[pid] = s[-self.max_samples:]
            self._save_samples(pid)

    def seen(self, pid):
        with self.lock:
            p = self.get(pid)
            if p:
                p["last_seen"] = time.time()
                p["overs"] = p.get("overs", 0) + 1
                self._save()

    def create(self, name, kind, emb=None):
        with self.lock:
            if not name:
                n = sum(p["kind"] == "anonymous" for p in self.people) + 1
                taken = {p["name"] for p in self.people}
                while f"Caller {n}" in taken:
                    n += 1
                name = f"Caller {n}"
            p = {"id": secrets.token_hex(4), "name": name[:40], "kind": kind, "created": time.time(),
                 "last_seen": time.time(), "overs": 0}
            self.people.append(p)
            self.samples[p["id"]] = None
            if emb is not None:
                self.add_sample(p["id"], emb)
            self._save()
            return p

    def rename(self, pid, name, kind=None):
        with self.lock:
            p = self.get(pid)
            if not p:
                return None
            p["name"] = name.strip()[:40] or p["name"]
            if kind:
                p["kind"] = kind
            self._save()
            return p

    def merge(self, src_id, dst_id):
        """Fold person src into dst (voiceprint samples, notes, counts), then delete src."""
        with self.lock:
            src, dst = self.get(src_id), self.get(dst_id)
            if not src or not dst or src is dst:
                return dst
            a, b = self.samples.get(dst_id), self.samples.get(src_id)
            if b is not None and len(b):
                merged = b if a is None or not len(a) or a.shape[1] != b.shape[1] else np.vstack([a, b])
                self.samples[dst_id] = merged[-self.max_samples:]
                self._save_samples(dst_id)
            notes = self.notes(src_id)
            if notes:
                self.add_note(dst_id, notes, stamp=False)
            dst["overs"] = dst.get("overs", 0) + src.get("overs", 0)
            dst["last_seen"] = max(dst.get("last_seen", 0), src.get("last_seen", 0))
            self.delete(src_id)
            return dst

    def delete(self, pid):
        with self.lock:
            p = self.get(pid)
            if not p:
                return None
            self.people.remove(p)
            self.samples.pop(pid, None)
            for ext in (".npy", ".md"):
                (self.dir / f"{pid}{ext}").unlink(missing_ok=True)
            self._save()
            return p

    def notes(self, pid):
        try:
            return (self.dir / f"{pid}.md").read_text()
        except FileNotFoundError:
            return ""

    def add_note(self, pid, text, stamp=True):
        with self.lock:
            text = " ".join(str(text).split()) if stamp else str(text).strip()
            if not text:
                return
            line = f"- {time.strftime('%Y-%m-%d')}: {text}\n" if stamp else text + "\n"
            notes = (self.notes(pid) + line)[-NOTES_MAX:]
            (self.dir / f"{pid}.md").write_text(notes)
