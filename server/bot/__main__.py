"""The AI agent without the radio: try it on recordings, and enrol voices from files.

  python -m server.bot replay -c config.yaml overs/*.wav     each file is one over
  python -m server.bot replay -c config.yaml --split long.wav cut a recording into overs by level
  python -m server.bot enrol  -c config.yaml --name Dave dave1.wav dave2.wav
  python -m server.bot people -c config.yaml                 who it knows

replay runs every over through the same pipeline as on air (speech to text, speaker ID, the
LLM, the voice) using the bot: section of the config (bot.enabled needn't be true), and writes
each reply it would have transmitted to --out as a WAV. It uses and updates the same people,
notes and logs as the live agent unless you give it another --dir, so you can test voices and
banter offline before the hardware arrives. Any WAV works: it's converted to 16 kHz mono. The
radio's own recordings (the clips folder) are a good source.
"""

import argparse
import asyncio
import logging
import time
from pathlib import Path

import numpy as np

from ..config import _check_bot, load
from .agent import Agent
from .capture import OverCapture, read_wav, to_pcm, wav_bytes


class OfflineHead:
    """What the agent needs from the server, without a radio: replies are saved, not sent."""

    def __init__(self, out, rate, quiet):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.rate = rate
        self.quiet = quiet
        self.n = 0

    def bot_channel_state(self):
        return False, None

    async def bot_transmit(self, samples, text, lead_ms):
        self.n += 1
        f = self.out / f"reply-{self.n:03d}.wav"
        f.write_bytes(wav_bytes(to_pcm(samples), self.rate))
        print(f"  -> reply ({len(samples) / self.rate:.1f} s, {f}): {text}")
        return True

    def bot_stop(self, c):
        pass

    def bot_audit(self, c, event, **detail):
        pass

    def bot_broadcast(self, obj):
        if self.quiet:
            return
        for it in obj.get("items") or []:
            if it["kind"] == "over" and it.get("text") is not None:
                who = it.get("who") or "unidentified"
                score = f" {it['score']:.2f}" if it.get("score") is not None else ""
                print(f"  [{it['secs']:4.1f} s] {who}{score}: {it['text']}")
            elif it["kind"] == "reply" and it.get("status") in ("dropped", "discarded"):
                print(f"  -> reply {it['status']} ({it.get('reason')}): {it['text']}")


def _split(samples, rate, args):
    """Cut a long recording into overs where the level shows activity."""
    overs = []
    cap = OverCapture(rate, 300, args.hang_ms, args.min_s, 60, lambda pcm, t, s: overs.append(pcm))
    frame = rate // 50
    thresh = 10 ** (args.level_dbfs / 20)
    for i in range(0, len(samples) - frame + 1, frame):
        x = samples[i:i + frame]
        busy = float(np.sqrt(np.mean(x * x))) > thresh
        cap.feed(to_pcm(x), busy, False)
    cap.feed(to_pcm(np.zeros(int(rate * args.hang_ms / 1000) + frame, np.float32)), False, False)
    return overs


async def replay(cfg, args):
    rate = int(cfg["audio"]["wire_rate"])
    head = OfflineHead(args.out, rate, quiet=False)
    agent = Agent(cfg, head)
    agent.mode = "listen" if args.listen else "auto"
    runner = asyncio.create_task(agent.run())
    while any(v == "loading" for v in agent.status.values()):
        await asyncio.sleep(0.2)
    print("backends:", ", ".join(f"{k} {v}" for k, v in agent.status.items()))
    for path in args.files:
        samples = read_wav(path, rate)
        overs = _split(samples, rate, args) if args.split else [to_pcm(samples)]
        print(f"{path}: {len(overs)} over{'s' * (len(overs) != 1)}")
        for pcm in overs:
            agent.seq += 1
            await agent._process(pcm, time.time(), len(pcm) / 2 / rate, agent.seq)
            while agent.pending:  # let its reply "go out" before the next over
                await asyncio.sleep(0.05)
    runner.cancel()


async def enrol(cfg, args):
    rate = int(cfg["audio"]["wire_rate"])
    agent = Agent(cfg, OfflineHead(args.out, rate, quiet=True))
    from .speakers import create_embedder
    emb = create_embedder(cfg["bot"]["speaker"], rate, agent.dir)
    person = agent.people.find_name(args.name) or agent.people.create(args.name, "known")
    for path in args.files:
        x = read_wav(path, rate)
        agent.people.add_sample(person["id"], emb.embed(x))
        print(f"{args.name}: added {path} ({len(x) / rate:.1f} s)")
    if args.note:
        agent.people.add_note(person["id"], args.note)


def people(cfg):
    from .speakers import People
    b = cfg["bot"]
    ppl = People(Path(b["dir"]) / "people", b["speaker"], b["speaker"]["backend"])
    for p in sorted(ppl.list(), key=lambda p: (p["kind"], p["name"])):
        seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(p.get("last_seen", 0)))
        s = ppl.samples.get(p["id"])
        print(f"{p['name']:<20} {p['kind']:<11} {p.get('overs', 0):>5} overs  {0 if s is None else len(s):>2} samples"
              f"  last {seen}  ({p['id']})")


def main():
    ap = argparse.ArgumentParser(prog="python -m server.bot", description="RadPi AI agent, offline")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--dir", help="use this folder for people, notes and logs instead of bot.dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="run recordings through the agent")
    r.add_argument("files", nargs="+")
    r.add_argument("--out", default="bot-replies", help="where reply WAVs go")
    r.add_argument("--listen", action="store_true", help="transcribe and identify only (no LLM, no replies)")
    r.add_argument("--split", action="store_true", help="cut each file into overs by level")
    r.add_argument("--level-dbfs", type=float, default=-40, help="--split: level that counts as someone talking")
    r.add_argument("--hang-ms", type=int, default=700)
    r.add_argument("--min-s", type=float, default=0.8)
    e = sub.add_parser("enrol", help="add a known person's voice from recordings of them")
    e.add_argument("--name", required=True)
    e.add_argument("--note", help="something to put in their notes")
    e.add_argument("--out", default="bot-replies", help=argparse.SUPPRESS)
    e.add_argument("files", nargs="+")
    sub.add_parser("people", help="list the people it knows")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

    cfg = load(args.config)
    cfg["bot"]["enabled"] = True  # check the bot: section even if it's off on air
    _check_bot(cfg["bot"], cfg["ptt"])
    if args.dir:
        cfg["bot"]["dir"] = str(Path(args.dir).resolve())
    if args.cmd == "replay":
        asyncio.run(replay(cfg, args))
    elif args.cmd == "enrol":
        asyncio.run(enrol(cfg, args))
    else:
        people(cfg)


if __name__ == "__main__":
    main()
