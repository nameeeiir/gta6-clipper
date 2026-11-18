import json
from types import SimpleNamespace

import numpy as np

from clipper import media
from clipper.captions import Layout, TimeMap, ass_color, build_ass
from clipper.chat import parse_irc
from clipper.config import EditCfg
from clipper.pipeline import load_chat_log
from clipper.publishers.tiktok import MB, chunk_plan
from clipper.transcribe import Sentence, Word


def test_parse_irc_privmsg_with_tags():
    line = ("@badge-info=;display-name=CoolGuy;emotes=25:0-4,6-10;emote-only=1;id=abc "
            ":coolguy!coolguy@coolguy.tmi.twitch.tv PRIVMSG #KaiCenat :Kappa Kappa")
    m = parse_irc(line, now=12.5)
    assert m.channel == "kaicenat" and m.user == "coolguy" and m.text == "Kappa Kappa"
    assert m.emote_only and m.emote_chars == 10 and m.t == 12.5
    assert parse_irc(":tmi.twitch.tv PONG tmi.twitch.tv") is None


def test_timemap_with_cuts_and_speed():
    tm = TimeMap([(10, 15), (17, 20)], speed=1.0)
    assert tm(10) == 0 and tm(15) == 5 and tm(17) == 5 and tm(20) == 8
    assert tm(16) is None and tm.snap(16) == 5
    fast = TimeMap([(0, 10)], speed=2.0)
    assert fast(4) == 2 and fast.duration == 5


def test_ass_karaoke_groups_and_escaping():
    words = [Word(0.0 + i * 0.4, 0.3 + i * 0.4, w) for i, w in enumerate(["no", "way", "{bro}", "what.", "next"])]
    ass = build_ass([Sentence(0, 2, "", words)], TimeMap([(0, 3)], 1.0), Layout(1400, 300, 400), EditCfg(),
                    width=1080, height=1920, hook="wait for it", credit="twitch.tv/x")
    events = [l for l in ass.splitlines() if l.startswith("Dialogue: 0")]
    assert len(events) == 5  # one per word
    assert any("(BRO)" in e for e in events)  # braces in speech can't inject ASS tags; uppercased
    assert "WAIT FOR IT" in ass and "twitch.tv/x" in ass
    assert ass_color("FFE600") == "&H0000E6FF"


def test_tiktok_chunk_rules():
    assert chunk_plan(3 * MB) == (3 * MB, 1)
    assert chunk_plan(40 * MB) == (40 * MB, 1)
    size = 150 * MB + 123
    chunk, count = chunk_plan(size)
    assert 5 * MB <= chunk <= 64 * MB and count == size // chunk
    assert size - (count - 1) * chunk <= 128 * MB


def test_impact_and_silence_detection():
    sr = media.SR
    t = np.arange(0, 20, 1 / sr)
    audio = 0.05 * np.sin(2 * np.pi * 200 * t)
    audio[(t > 5) & (t < 7)] = 0  # dead air
    boom = (t >= 12) & (t < 13)
    audio[boom] += 0.9 * np.sin(2 * np.pi * 90 * t[boom]) * np.exp(-2 * (t[boom] - 12))
    env = media.envelope_db(audio.astype(np.float32))
    at, strength = media.find_impact(env, 8, 16)
    assert abs(at - 12.0) < 0.3 and strength > 10
    spans = media.silent_spans(env, 0, 20, 0.8)
    assert len(spans) == 1 and abs(spans[0][0] - 5) < 0.2 and abs(spans[0][1] - 7) < 0.2


def test_chat_log_formats(tmp_path):
    td = tmp_path / "td.json"
    td.write_text(json.dumps({"comments": [{"content_offset_seconds": 3.5, "commenter": {"name": "A"}, "message": {"body": "KEKW"}}]}))
    jl = tmp_path / "c.jsonl"
    jl.write_text('{"t": 2, "user": "b", "text": "hi"}\n{"t": 1, "user": "c", "text": "yo"}\n')
    assert load_chat_log(td) == [(3.5, "a", "KEKW")]
    assert [m[0] for m in load_chat_log(jl)] == [1.0, 2.0]


def test_find_impact_wide_window_prefers_expected_moment():
    fr = media.FRAME_S
    env = np.full(int(40 / fr), -40.0)
    for t in (10.0, 20.0):  # two equally loud bangs: the one nearer the expected moment wins
        env[int(t / fr):int((t + 0.6) / fr)] = -5.0
    t, strength = media.find_impact(env, 5.0, 30.0, prefer=19.0)
    assert abs(t - 20.0) < 0.2 and strength > 6.0


def test_impact_found_when_chat_lags_more_than_expected():
    # Bang at 10s; viewers are ~8s further behind than we are, so chat only reacts at 22s and the
    # detector's estimate (onset - 4s lag) is 18s. The old window [13, 21] missed the bang.
    fr = media.FRAME_S
    env = np.full(int(40 / fr), -40.0)
    env[int(10.0 / fr):int(10.6 / fr)] = -5.0
    onset, ev = 22.0, 18.0
    t, strength = media.find_impact(env, ev - 9.0, onset - 0.3, prefer=ev)
    assert abs(t - 10.0) < 0.2 and strength > 6.0
    old_t, old_strength = media.find_impact(env, ev - 5.0, min(onset, ev + 3.0))
    assert abs(old_t - 10.0) > 2.0
