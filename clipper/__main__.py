"""CLI: python -m clipper {setup,run,replay,auth,check,status}"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
from pathlib import Path

from .config import env, load_config
from .state import State


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_check(cfg, state: State) -> int:
    ok = True

    def line(good: bool, what: str, hint: str = "") -> None:
        nonlocal ok
        ok &= good or hint.startswith("optional")
        print(f"  [{'ok' if good else '--'}] {what}" + (f"   ({hint})" if not good and hint else ""))

    print("tools")
    line(bool(shutil.which("ffmpeg")), "ffmpeg", "install ffmpeg >= 5")
    line(bool(shutil.which("streamlink")), "streamlink", "pip install streamlink")
    try:
        import faster_whisper  # noqa: F401
        line(True, "faster-whisper")
    except ImportError:
        line(False, "faster-whisper", "optional: pip install faster-whisper (captions)")
    import subprocess
    fonts = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout if shutil.which("fc-list") else ""
    from .editor import BUNDLED_FONTS
    bundled = any(BUNDLED_FONTS.glob("*.[ot]tf"))
    line(cfg.edit.caption_font.split()[0] in fonts or bool(cfg.edit.fonts_dir) or bundled, f"caption font '{cfg.edit.caption_font}'",
         "optional: install it or set edit.fonts_dir; libass falls back to another font")
    print("credentials")
    line(bool(env("TWITCH_CLIENT_ID") and env("TWITCH_CLIENT_SECRET")), "Twitch app (TWITCH_CLIENT_ID/SECRET)", "required for live mode")
    line(bool(env("ANTHROPIC_API_KEY") or env("ANTHROPIC_AUTH_TOKEN")), "Claude (ANTHROPIC_API_KEY)", "optional: template titles + no quality gate without it")
    from .publishers import InstagramPublisher, TikTokPublisher, YouTubePublisher
    for cls, pc in ((YouTubePublisher, cfg.publish.youtube), (TikTokPublisher, cfg.publish.tiktok), (InstagramPublisher, cfg.publish.instagram)):
        if pc.enabled:
            p = cls(state, pc)
            line(p.configured(), f"{p.name} upload", f"optional: python -m clipper auth {p.name}")
    print(f"\npublishing is {'OFF (publish.dry_run: true)' if cfg.publish.dry_run else 'ON'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clipper", description="Live GTA 6 Twitch clipper")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="watch live streams, clip and post")
    r.add_argument("--dry-run", action="store_true", help="render but never post")
    r.add_argument("--post", action="store_true", help="post even if publish.dry_run is true in config")
    r.add_argument("--channels", nargs="*", help="override twitch.channels (disables auto-discovery)")
    r.add_argument("--any-category", action="store_true",
                   help="with --channels: watch them whatever category they're streaming in")
    r.add_argument("--game", help='Twitch category to watch instead of twitch.game_name, e.g. "Grand Theft Auto V"')

    rp = sub.add_parser("replay", help="backtest on a VOD + chat log")
    rp.add_argument("--video", required=True, type=Path)
    rp.add_argument("--chat", required=True, type=Path, help="TwitchDownloader JSON, chat-downloader JSON, or JSONL {t,user,text}")
    rp.add_argument("--channel", default="replay")
    rp.add_argument("--name", default=None, help="streamer display name")
    rp.add_argument("--post", action="store_true", help="actually publish the clips")
    rp.add_argument("--limit", type=int, default=None, help="only the N strongest spikes")

    a = sub.add_parser("auth", help="connect an upload account")
    a.add_argument("platform", choices=["youtube", "tiktok", "instagram"])
    a.add_argument("--token", help="instagram: short-lived user token from Graph API Explorer")

    sub.add_parser("setup", help="paste in your keys (checks they work, saves them to .env)")
    sub.add_parser("check", help="verify tools and credentials")
    sub.add_parser("status", help="recent clips")

    args = ap.parse_args(argv)
    _setup_logging(args.verbose)
    if args.cmd == "setup":
        from .setup_wizard import run as setup_run
        return setup_run()
    cfg = load_config(args.config)
    state = State(cfg.data_dir)

    if args.cmd == "check":
        return cmd_check(cfg, state)
    if args.cmd == "status":
        for row in state.recent_clips(30):
            print(" | ".join(str(x) for x in row))
        return 0
    if args.cmd == "auth":
        from . import auth
        if args.platform == "youtube":
            auth.youtube(state)
        elif args.platform == "tiktok":
            auth.tiktok(state)
        else:
            if not args.token:
                raise SystemExit("pass --token <short-lived user token>")
            auth.instagram(state, args.token, cfg.publish.instagram.api_version)
        return 0
    if args.cmd == "replay":
        from .pipeline import replay
        res = asyncio.run(replay(cfg, state, args.video, args.chat, args.channel.lower(), args.name or args.channel,
                                 publish=args.post, limit=args.limit))
        print(json.dumps([{k: r[k] for k in ("clip_id", "video", "duration", "title", "hook", "quality_score", "posts")} for r in res], indent=2))
        return 0
    if args.cmd == "run":
        if args.dry_run:
            cfg.publish.dry_run = True
        if args.post:
            cfg.publish.dry_run = False
        if args.game:
            cfg.twitch.game_name = args.game
        if args.any_category:
            cfg.twitch.require_category = False
        if args.channels:
            cfg.twitch.channels = [c.lower() for c in args.channels]
            cfg.twitch.auto_discover = False
        from .pipeline import LiveClipper
        try:
            asyncio.run(LiveClipper(cfg, state).run())
        except KeyboardInterrupt:
            pass
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
