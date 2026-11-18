"""Configuration: YAML file for tunables, environment variables (or .env) for secrets."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class TwitchCfg(BaseModel):
    game_name: str = "Grand Theft Auto VI"
    # Channels to always watch (only clipped while they are live in the game category).
    channels: list[str] = Field(default_factory=list)
    # Also auto-watch the biggest live streams in the category.
    auto_discover: bool = True
    discover_top_n: int = 5
    min_viewers: int = 300
    languages: list[str] = Field(default_factory=lambda: ["en"])
    discover_interval_s: int = 120
    require_category: bool = True
    exclude_channels: list[str] = Field(default_factory=list)


class CaptureCfg(BaseModel):
    # streamlink quality list, first available wins. 720p60 keeps bandwidth/CPU low per streamer.
    quality: str = "720p60,720p,936p60,1080p60,best"
    buffer_seconds: int = 240
    segment_seconds: int = 2
    work_dir: str = "data/buffers"
    streamlink_args: list[str] = Field(
        default_factory=lambda: ["--twitch-disable-ads", "--twitch-low-latency", "--hls-live-edge", "2"]
    )
    restart_backoff_s: list[float] = Field(default_factory=lambda: [2, 5, 10, 30, 60])


class DetectorCfg(BaseModel):
    short_window_s: int = 6
    baseline_half_life_s: float = 180.0
    warmup_s: float = 45.0
    z_enter: float = 3.0
    z_exit: float = 1.0
    min_ratio: float = 2.0
    min_unique_chatters: int = 5
    cooldown_s: float = 40.0
    # Seconds between the on-stream event and chat reacting (typing time + player buffering).
    reaction_lag_s: float = 4.0
    max_episode_s: float = 30.0
    per_user_cap: float = 3.0
    max_user_share: float = 0.25  # one chatter can't be more than this share of a spike
    # token (lowercase) -> (weight, mood)
    hype_tokens: dict[str, tuple[float, str]] = Field(
        default_factory=lambda: {
            # funny
            "lul": (1.5, "funny"), "lulw": (1.5, "funny"), "kekw": (1.8, "funny"), "omegalul": (2.0, "funny"),
            "icant": (1.8, "funny"), "lmao": (1.5, "funny"), "lmfao": (1.6, "funny"), "lol": (1.0, "funny"),
            "kek": (1.4, "funny"), "kekl": (1.6, "funny"), "pepelaugh": (1.6, "funny"), "xdd": (1.4, "funny"),
            "haha": (1.0, "funny"), "😂": (1.4, "funny"), "🤣": (1.5, "funny"), "💀": (1.8, "funny"), "😭": (1.4, "funny"),
            # hype
            "pog": (1.6, "hype"), "pogchamp": (1.6, "hype"), "poggers": (1.6, "hype"), "pogu": (1.6, "hype"),
            "w": (1.2, "hype"), "holy": (1.5, "hype"), "goated": (1.5, "hype"), "insane": (1.5, "hype"),
            "letsgo": (1.5, "hype"), "clap": (1.2, "hype"), "ez": (1.1, "hype"), "🔥": (1.3, "hype"),
            # shock / wtf
            "wtf": (1.6, "shock"), "noway": (1.8, "shock"), "what": (1.0, "shock"), "???": (1.6, "shock"),
            "omg": (1.4, "shock"), "d:": (1.4, "shock"), "😳": (1.4, "shock"), "susge": (1.2, "shock"),
            "monkaw": (1.6, "tense"), "monkas": (1.5, "tense"), "monkagiga": (1.7, "tense"), "😰": (1.3, "tense"),
            # fail
            "l": (1.2, "fail"), "rip": (1.3, "fail"), "f": (1.0, "fail"), "sadge": (1.1, "fail"),
            "unlucky": (1.3, "fail"), "aware": (1.0, "fail"),
            # explicit clip requests are the strongest signal
            "clip": (4.0, "clip"), "clipped": (3.0, "clip"), "clipit": (4.0, "clip"),
        }
    )


class MomentCfg(BaseModel):
    # Shorts viewers swipe within seconds: the payoff should land ~5s in, not halfway through.
    target_pre_s: float = 6.0  # context before the payoff
    max_pre_s: float = 15.0
    min_pre_s: float = 2.5
    min_post_s: float = 3.5  # reaction after the payoff
    max_post_s: float = 12.0
    min_len_s: float = 8.0
    max_len_s: float = 35.0
    # How far before the expected moment to look for the scream/crash/explosion.
    impact_search_before_s: float = 9.0
    use_llm: bool = True
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    min_quality_score: int = 5  # LLM quality gate (1-10); clips below are rendered but not posted


class TranscribeCfg(BaseModel):
    enabled: bool = True
    model: str = "small.en"
    device: str = "auto"  # auto|cpu|cuda
    compute_type: str = "auto"
    workers: int = 1


class StreamerCfg(BaseModel):
    # Facecam rectangle as fractions of the source frame: [x, y, w, h]. Enables the split layout.
    facecam: list[float] | None = None
    layout: str | None = None
    display_name: str | None = None


class EditCfg(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    layout: str = "auto"  # auto|blur|split|crop
    gameplay_zoom: float = 1.6  # blur layout: how much bigger than fit-to-width the gameplay is (crops the sides)
    speed: float = 1.07
    cut_dead_air: bool = True
    dead_air_min_s: float = 0.8
    dead_air_keep_s: float = 0.15
    punch_zoom: float = 1.22
    punch_duration_s: float = 1.4
    shake_px: float = 14.0
    flash: bool = True
    impact_sfx: bool = True
    impact_echo: bool = True
    impact_bass_db: float = 7.0
    voice_enhance: bool = True
    loudness_lufs: float = -14.0
    captions: bool = True
    caption_font: str = "Inter Black"
    caption_size: int = 96
    caption_max_chars: int = 14  # per line; longer runs under TikTok/Reels' right-side buttons
    caption_color: str = "FFFFFF"
    caption_highlight: str = "FFE600"
    words_per_caption: int = 3
    uppercase: bool = True
    censor_words: list[str] = Field(default_factory=list)
    hook_text: bool = True
    hook_seconds: float = 2.8
    credit_text: bool = True
    progress_bar: bool = True
    progress_color: str = "FFE600"
    fonts_dir: str | None = None
    encoder: str = "libx264"  # libx264|h264_nvenc|h264_qsv|h264_vaapi|h264_videotoolbox
    preset: str = "veryfast"
    crf: int = 20
    audio_bitrate: str = "192k"
    render_workers: int = 2
    output_dir: str = "data/clips"
    keep_raw: bool = False


class YouTubeCfg(BaseModel):
    enabled: bool = True
    privacy: str = "public"  # public|unlisted|private
    category_id: str = "20"  # Gaming
    max_posts_per_day: int = 50


class TikTokCfg(BaseModel):
    enabled: bool = True
    # Unaudited TikTok apps can only post SELF_ONLY; we fall back automatically.
    privacy_level: str = "PUBLIC_TO_EVERYONE"
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    max_posts_per_day: int = 15


class InstagramCfg(BaseModel):
    enabled: bool = True
    api_version: str = "v24.0"
    max_posts_per_day: int = 50


class PublishCfg(BaseModel):
    dry_run: bool = True  # render clips but do not post until you flip this
    upload_workers: int = 2
    default_hashtags: list[str] = Field(default_factory=lambda: ["#GTA6", "#GTAVI", "#gaming", "#twitch"])
    youtube: YouTubeCfg = Field(default_factory=YouTubeCfg)
    tiktok: TikTokCfg = Field(default_factory=TikTokCfg)
    instagram: InstagramCfg = Field(default_factory=InstagramCfg)


class Config(BaseModel):
    data_dir: str = "data"
    twitch: TwitchCfg = Field(default_factory=TwitchCfg)
    capture: CaptureCfg = Field(default_factory=CaptureCfg)
    detector: DetectorCfg = Field(default_factory=DetectorCfg)
    moment: MomentCfg = Field(default_factory=MomentCfg)
    transcribe: TranscribeCfg = Field(default_factory=TranscribeCfg)
    edit: EditCfg = Field(default_factory=EditCfg)
    publish: PublishCfg = Field(default_factory=PublishCfg)
    streamers: dict[str, StreamerCfg] = Field(default_factory=dict)

    def streamer(self, channel: str) -> StreamerCfg:
        return self.streamers.get(channel.lower(), StreamerCfg())


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines); real environment variables win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: str | Path | None) -> Config:
    load_dotenv()
    if path and Path(path).exists():
        raw = yaml.safe_load(Path(path).read_text()) or {}

        def drop_empty(d):  # `streamers:` with everything commented out parses as None
            return {k: drop_empty(v) if isinstance(v, dict) else v for k, v in d.items() if v is not None}

        cfg = Config.model_validate(drop_empty(raw))
    else:
        cfg = Config()
    cfg.streamers = {k.lower(): v for k, v in cfg.streamers.items()}
    cfg.twitch.channels = [c.lower().lstrip("#") for c in cfg.twitch.channels]
    return cfg


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
