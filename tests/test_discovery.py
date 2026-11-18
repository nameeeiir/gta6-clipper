import asyncio

import pytest

from clipper.config import Config
from clipper.pipeline import LiveClipper
from clipper.state import State
from clipper.twitch_api import LiveStream

GTA6 = "123"


class FakeHelix:
    async def game_id(self, name):
        return GTA6

    async def streams_by_login(self, logins):
        # Speed is live, but in "Just Chatting" (game_id 509658), not GTA VI.
        return [LiveStream("ishowspeed", "IShowSpeed", "NZ GTA 6", 90000, "509658", "Just Chatting", "en")]

    async def top_streams(self, gid, n, langs):
        return [LiveStream("kaicenat", "KaiCenat", "GTA 6 MARATHON", 50000, GTA6, "Grand Theft Auto VI", "en")]


def _discover_once(tmp_path, monkeypatch, require_category):
    cfg = Config(data_dir=str(tmp_path))
    cfg.twitch.channels, cfg.twitch.auto_discover = ["ishowspeed"], False
    cfg.twitch.require_category = require_category
    lc = LiveClipper(cfg, State(cfg.data_dir))
    started = []

    async def fake_start(ch, display, title):
        started.append(ch)
        lc.sessions[ch] = object()

    async def stop_after_one(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(lc, "_start_session", fake_start)
    monkeypatch.setattr("clipper.pipeline.asyncio.sleep", stop_after_one)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(lc._discovery(FakeHelix()))
    return started


def test_named_streamer_in_other_category_is_skipped_by_default(tmp_path, monkeypatch):
    assert _discover_once(tmp_path, monkeypatch, require_category=True) == []


def test_any_category_watches_named_streamer(tmp_path, monkeypatch):
    assert _discover_once(tmp_path, monkeypatch, require_category=False) == ["ishowspeed"]
