"""End-to-end replay on a synthetic stream: two on-stream 'events', chat reacting a few seconds later.
Checks the clips exist, are 1080x1920, and that the cut is anchored on the real event, not the chat."""

import asyncio
import json
import random
import shutil
import subprocess

import pytest

from clipper import media
from clipper.config import Config
from clipper.pipeline import replay
from clipper.state import State

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
EVENTS = (150.0, 240.0)


@pytest.fixture(scope="module")
def vod(tmp_path_factory):
    d = tmp_path_factory.mktemp("vod")
    expr = "0.08*sin(2*PI*180*t)*(0.5+0.5*sin(2*PI*2*t))" + "".join(
        f"+if(between(t,{e},{e + 1.2}),0.9*sin(2*PI*90*t)*exp(-2*(t-{e})),0)" for e in EVENTS
    )
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=280",
                    "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d=280", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(d / "vod.mp4")], check=True)
    random.seed(3)
    users = [f"v{i}" for i in range(400)]
    lines = []
    for sec in range(280):
        n, txt = random.choice([1, 2, 3]), ["lol", "nice", "gta 6 looks good"]
        for e in EVENTS:  # chat reacts ~4-5s after the event
            if e + 4 <= sec < e + 14:
                n, txt = 22, ["KEKW", "OMEGALUL", "CLIP IT", "💀💀"]
        lines += [json.dumps({"t": sec + random.random(), "user": random.choice(users), "text": random.choice(txt)})
                  for _ in range(n)]
    (d / "chat.jsonl").write_text("\n".join(sorted(lines, key=lambda l: json.loads(l)["t"])))
    return d


def test_replay_end_to_end(vod, tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    cfg.edit.output_dir = str(tmp_path / "clips")
    cfg.transcribe.enabled = False
    cfg.moment.use_llm = False
    res = asyncio.run(replay(cfg, State(tmp_path), vod / "vod.mp4", vod / "chat.jsonl", "tester", "Tester", publish=False))
    assert len(res) == 2
    for r in sorted(res, key=lambda r: r["anchor_wall"]):
        info = asyncio.run(media.probe(__import__("pathlib").Path(r["video"])))
        assert (info.width, info.height) == (1080, 1920) and info.has_audio
        assert cfg.moment.min_len_s / cfg.edit.speed - 1 <= info.duration <= cfg.moment.max_len_s
        # Shorts pacing: the payoff (cover frame = anchor) lands within the first ~7s of the clip
        assert r["payoff_at_s"] <= 7.0, r["payoff_at_s"]
    ready = sorted((tmp_path / "READY TO POST").glob("*.mp4"))
    assert len(ready) == 2 and all(p.name.endswith(".mp4") and "Tester - " in p.name for p in ready), ready
    anchors = sorted(r["anchor_wall"] for r in res)
    for got, want in zip(anchors, EVENTS):
        assert abs(got - want) < 0.5, (got, want)
