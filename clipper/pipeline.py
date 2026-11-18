"""Orchestration.

  chat (1 conn) ──► per-channel detector ──► spike ──► wait for post-roll ──► lossless extract
                                                                               │
  capture (per channel, -c copy ring buffer) ─────────────────────────────────┘
                                                                               ▼
          render queue (priority = spike strength, N workers) ─► plan (audio + whisper + Claude)
                                                                 ─► render (ffmpeg)
                                                                 ─► upload queue ─► YT | TikTok | IG in parallel
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .capture import StreamCapture, extract
from .chat import ChatMessage, TwitchChat
from .config import Config, env
from .detector import ChannelDetector, Spike
from .editor import VideoEditor
from .llm import Editor as LLMEditor
from .moment import ClipPlan, MomentPicker
from .publishers import InstagramPublisher, PostMeta, TikTokPublisher, YouTubePublisher
from .state import State
from .transcribe import Transcriber

log = logging.getLogger("clipper")


@dataclass(order=True)
class ClipJob:
    priority: float
    seq: int
    clip_id: str = field(compare=False)
    spike: Spike = field(compare=False)
    src: Path = field(compare=False)
    src_wall0: float = field(compare=False)
    display_name: str = field(compare=False)
    stream_title: str = field(compare=False, default="")
    job_dir: Path = field(compare=False, default=Path("."))


def clip_id_for(spike: Spike) -> str:
    return f"{spike.channel}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(spike.event))}"


class ClipFactory:
    """plan -> render -> publish, shared by live and replay modes."""

    def __init__(self, cfg: Config, state: State, publish: bool | None = None):
        self.cfg = cfg
        self.state = state
        self.publish_enabled = (not cfg.publish.dry_run) if publish is None else publish
        self.transcriber = Transcriber(cfg.transcribe)
        self.picker = MomentPicker(cfg.moment, cfg.edit, self.transcriber, LLMEditor(cfg.moment), cfg.publish.default_hashtags)
        self.editor = VideoEditor(cfg.edit, cfg.streamer)
        self.publishers = []
        p = cfg.publish
        for cls, pc in ((YouTubePublisher, p.youtube), (TikTokPublisher, p.tiktok), (InstagramPublisher, p.instagram)):
            if pc.enabled:
                pub = cls(state, pc)
                if pub.configured():
                    self.publishers.append(pub)
                elif self.publish_enabled:
                    log.warning("%s enabled but not configured (run: python -m clipper auth %s)", pub.name, pub.name)
        self.render_q: asyncio.PriorityQueue[ClipJob] = asyncio.PriorityQueue()
        self.upload_q: asyncio.Queue = asyncio.Queue()
        self._seq = itertools.count()
        self._workers: list[asyncio.Task] = []
        self.stats = {"spikes": 0, "rendered": 0, "skipped": 0, "posted": 0, "failed": 0}
        self.results: list[dict] = []

    def start(self) -> None:
        self.transcriber.warmup()
        self._workers += [asyncio.create_task(self._render_worker(i)) for i in range(max(1, self.cfg.edit.render_workers))]
        self._workers += [asyncio.create_task(self._upload_worker(i)) for i in range(max(1, self.cfg.publish.upload_workers))]

    async def submit(self, job: ClipJob) -> None:
        await self.render_q.put(job)

    def make_job(self, spike: Spike, src: Path, src_wall0: float, display_name: str, stream_title: str, job_dir: Path) -> ClipJob:
        return ClipJob(-spike.strength, next(self._seq), clip_id_for(spike), spike, src, src_wall0, display_name, stream_title, job_dir)

    async def drain(self) -> None:
        await self.render_q.join()
        await self.upload_q.join()

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        for p in self.publishers:
            await p.close()

    async def _render_worker(self, i: int) -> None:
        while True:
            job = await self.render_q.get()
            try:
                await self._process(job)
            except Exception:
                log.exception("[%s] clip %s failed", job.spike.channel, job.clip_id)
                self.stats["failed"] += 1
                self.state.update_clip(job.clip_id, status="failed")
            finally:
                self.render_q.task_done()

    async def _process(self, job: ClipJob) -> None:
        t0 = time.time()
        plan = await self.picker.plan(job.clip_id, job.spike, job.src, job.src_wall0, job.display_name, job.stream_title)
        meta = plan.meta
        postable = meta.worth_posting and meta.quality_score >= self.cfg.moment.min_quality_score
        if not meta.worth_posting:
            log.info("[%s] %s rejected by quality gate (score %s): %s", job.spike.channel, job.clip_id, meta.quality_score, meta.title)
            self.stats["skipped"] += 1
            self.state.update_clip(job.clip_id, status="rejected", title=meta.title)
            self._write_meta(job, plan, None, [])
            return
        res = await self.editor.render(plan, job.job_dir)
        self.stats["rendered"] += 1
        log.info("[%s] rendered %s (%.1fs, %s layout) in %.0fs: %s", job.spike.channel, res.path.name, res.duration,
                 res.layout, time.time() - t0, meta.title)
        self.state.update_clip(job.clip_id, status="rendered", title=meta.title, path=str(res.path))
        self._write_meta(job, plan, res, [])
        self._publish_ready_copy(job, res.path, meta.title)
        if self.publish_enabled and postable and self.publishers:
            await self.upload_q.put((job, plan, res))
        elif self.publish_enabled and not postable:
            log.info("[%s] %s below quality bar (%s) - kept locally, not posted", job.spike.channel, job.clip_id, meta.quality_score)

    async def _upload_worker(self, i: int) -> None:
        while True:
            job, plan, res = await self.upload_q.get()
            try:
                meta = PostMeta(plan.meta.title, plan.meta.description, plan.meta.hashtags, res.cover_ms)
                results = await asyncio.gather(*(p.publish(res.path, meta) for p in self.publishers))
                for r in results:
                    self.state.add_post(job.clip_id, r.platform, r.ok, r.remote_id, r.url, r.error)
                    if r.ok:
                        self.stats["posted"] += 1
                        log.info("[%s] posted to %s %s %s", job.spike.channel, r.platform, r.url, r.error)
                    else:
                        log.error("[%s] %s upload failed: %s", job.spike.channel, r.platform, r.error)
                self.state.update_clip(job.clip_id, status="posted" if any(r.ok for r in results) else "post_failed")
                self._write_meta(job, plan, res, [asdict(r) for r in results])
            except Exception:
                log.exception("upload of %s failed", job.clip_id)
            finally:
                self.upload_q.task_done()

    def _publish_ready_copy(self, job: ClipJob, video: Path, title: str) -> None:
        """Put the finished video in one easy-to-find folder: <data_dir>/READY TO POST/."""
        try:
            ready = Path(self.cfg.data_dir) / "READY TO POST"
            ready.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", f"{job.display_name} - {title}").strip(" .")[:90] or job.clip_id
            stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(job.spike.event if job.spike.event > 1e9 else time.time()))
            dest = ready / f"{stamp} {safe}.mp4"
            if dest.exists():
                dest = ready / f"{stamp} {safe} ({job.clip_id[-6:]}).mp4"
            try:
                os.link(video, dest)  # no extra disk space
            except OSError:
                shutil.copy2(video, dest)
        except OSError:
            log.warning("could not copy %s to READY TO POST", video, exc_info=True)

    def _write_meta(self, job: ClipJob, plan: ClipPlan, res, posts: list[dict]) -> None:
        job.job_dir.mkdir(parents=True, exist_ok=True)
        m = plan.meta
        rec = {
            "clip_id": job.clip_id, "channel": job.spike.channel, "video": str(res.path) if res else None,
            "duration": round(res.duration, 2) if res else None,
            "payoff_at_s": round(res.cover_ms / 1000, 2) if res else None, "title": m.title, "hook": m.hook_text,
            "description": m.description, "hashtags": m.hashtags, "quality_score": m.quality_score,
            "worth_posting": m.worth_posting, "metadata_source": m.source, "mood": plan.mood,
            "cut": {"start": plan.start, "end": plan.end, "anchor": plan.anchor, "keep": plan.keep},
            "spike": {k: v for k, v in asdict(job.spike).items() if k != "top_messages"},
            "chat_sample": [m_[2] for m_ in job.spike.top_messages[:12]], "posts": posts,
            "src_wall0": plan.src_wall0, "anchor_wall": round(plan.src_wall0 + plan.anchor, 2), **plan.info,
        }
        (job.job_dir / "meta.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False))
        self.results.append(rec)


# ------------------------------------------------------------------------------------------------
@dataclass
class ChannelSession:
    channel: str
    display_name: str
    title: str
    capture: StreamCapture
    detector: ChannelDetector
    task: asyncio.Task | None = None


class LiveClipper:
    def __init__(self, cfg: Config, state: State):
        self.cfg = cfg
        self.state = state
        self.factory = ClipFactory(cfg, state)
        self.sessions: dict[str, ChannelSession] = {}
        self.chat = TwitchChat(self._on_chat)
        self.buffers = Path(cfg.capture.work_dir)
        self.clips_dir = Path(cfg.edit.output_dir)

    def _on_chat(self, msg: ChatMessage) -> None:
        s = self.sessions.get(msg.channel)
        if s:
            s.detector.add(msg)

    async def run(self) -> None:
        from .twitch_api import Helix

        helix = Helix(env("TWITCH_CLIENT_ID"), env("TWITCH_CLIENT_SECRET"))
        self.factory.start()
        if not self.factory.publish_enabled:
            log.info("dry run: clips are rendered to %s but not posted (publish.dry_run: true)", self.clips_dir)
        tasks = [
            asyncio.create_task(self.chat.run()), asyncio.create_task(self._discovery(helix)),
            asyncio.create_task(self._ticker()), asyncio.create_task(self._status()),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            for ch in list(self.sessions):
                await self._stop_session(ch)
            await self.factory.stop()
            await helix.close()

    async def _discovery(self, helix) -> None:
        tc = self.cfg.twitch
        while True:
            try:
                gid = await helix.game_id(tc.game_name)
                want: dict[str, object] = {}
                if tc.channels:
                    for s in await helix.streams_by_login(tc.channels):
                        if not tc.require_category or s.game_id == gid:
                            want[s.login] = s
                if tc.auto_discover:
                    for s in await helix.top_streams(gid, tc.discover_top_n + len(tc.exclude_channels), tc.languages):
                        if s.viewers >= tc.min_viewers and s.login not in tc.exclude_channels and len(want) < tc.discover_top_n + len(tc.channels):
                            want.setdefault(s.login, s)
                for ch in list(self.sessions):
                    if ch not in want:
                        log.info("[%s] offline / left category - stopping", ch)
                        await self._stop_session(ch)
                for ch, s in want.items():
                    if ch not in self.sessions:
                        await self._start_session(ch, s.display_name, s.title)
                        log.info("[%s] watching (%d viewers): %s", ch, s.viewers, s.title[:80])
                    else:
                        self.sessions[ch].title = s.title
            except Exception:
                log.exception("discovery failed")
            await asyncio.sleep(tc.discover_interval_s)

    async def _start_session(self, ch: str, display: str, title: str) -> None:
        cap = StreamCapture(ch, self.cfg.capture, self.buffers)
        det = ChannelDetector(ch, self.cfg.detector, time.time())
        sess = ChannelSession(ch, self.cfg.streamer(ch).display_name or display, title, cap, det)
        sess.task = asyncio.create_task(cap.run())
        self.sessions[ch] = sess
        await self.chat.join(ch)

    async def _stop_session(self, ch: str) -> None:
        sess = self.sessions.pop(ch, None)
        if not sess:
            return
        await self.chat.part(ch)
        await sess.capture.stop()
        if sess.task:
            sess.task.cancel()
        sess.capture.cleanup()

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(1.0 - (time.time() % 1.0) + 0.05)
            now = time.time()
            for sess in list(self.sessions.values()):
                for spike in sess.detector.tick(now):
                    self.factory.stats["spikes"] += 1
                    log.info("[%s] SPIKE z=%.1f x%.1f %d chatters mood=%s (%s)", spike.channel, spike.score, spike.ratio,
                             spike.unique_chatters, spike.mood, ", ".join(m[2][:25] for m in spike.top_messages[:4]))
                    asyncio.create_task(self._handle_spike(sess, spike))

    async def _handle_spike(self, sess: ChannelSession, spike: Spike) -> None:
        m = self.cfg.moment
        t0 = spike.event - m.impact_search_before_s - m.max_pre_s - 2
        t1 = spike.event + m.max_post_s + 8
        buf = sess.capture.buffer
        if not buf.segments or buf.earliest_wall > spike.event + 1.0:
            log.warning("[%s] moment happened before capture started - skipped (buffer from %.1f, event %.1f)",
                        spike.channel, buf.earliest_wall, spike.event)
            return
        hold = buf.hold(t0, t1)
        try:
            deadline = time.time() + 90
            while buf.latest_wall < t1 and time.time() < deadline and sess.channel in self.sessions:
                await asyncio.sleep(0.5)
            segs = buf.covering(t0, t1)
            if not segs:
                log.warning("[%s] no video for spike window", spike.channel)
                return
            cid = clip_id_for(spike)
            job_dir = self.clips_dir / cid
            raw = job_dir / "raw.ts"
            wall0 = await extract(segs, raw)
        finally:
            buf.release(hold)
        self.state.add_clip(cid, spike.channel, spike.event, spike.strength, spike.mood)
        await self.factory.submit(self.factory.make_job(spike, raw, wall0, sess.display_name, sess.title, job_dir))

    async def _status(self) -> None:
        while True:
            await asyncio.sleep(60)
            chans = ", ".join(f"{c}:{s.detector.stats()['msgs_per_s']}/s{'' if s.capture.healthy else '(no video)'}"
                              for c, s in self.sessions.items())
            log.info("status | %s | queue render=%d upload=%d | %s", chans or "no channels",
                     self.factory.render_q.qsize(), self.factory.upload_q.qsize(), self.factory.stats)


# ------------------------------------------------------------------------------------------------
def load_chat_log(path: Path) -> list[tuple[float, str, str]]:
    """(offset_seconds, user, text) from TwitchDownloader JSON, chat-downloader JSON, or JSONL {t,user,text}."""
    raw = path.read_text(encoding="utf-8")
    out: list[tuple[float, str, str]] = []
    try:
        j = json.loads(raw)
    except json.JSONDecodeError:
        j = None
    if isinstance(j, dict) and "comments" in j:  # TwitchDownloader
        for c in j["comments"]:
            user = (c.get("commenter") or {}).get("name") or (c.get("commenter") or {}).get("display_name") or "?"
            out.append((float(c["content_offset_seconds"]), user.lower(), (c.get("message") or {}).get("body", "")))
    elif isinstance(j, list):  # chat-downloader
        for c in j:
            t = c.get("time_in_seconds", c.get("t"))
            if t is None:
                continue
            user = (c.get("author") or {}).get("name") if isinstance(c.get("author"), dict) else c.get("user", "?")
            out.append((float(t), str(user).lower(), c.get("message") or c.get("text", "")))
    else:
        for line in raw.splitlines():
            if line.strip():
                d = json.loads(line)
                out.append((float(d["t"]), str(d.get("user", "?")).lower(), d.get("text", "")))
    out.sort(key=lambda x: x[0])
    return out


async def replay(cfg: Config, state: State, video: Path, chat_path: Path, channel: str, display: str,
                 publish: bool, limit: int | None = None) -> list[dict]:
    """Backtest the whole pipeline on a VOD + its chat log (no Twitch connection needed)."""
    from . import media

    factory = ClipFactory(cfg, state, publish=publish)
    factory.start()
    msgs = load_chat_log(chat_path)
    log.info("replay: %d chat messages over %.0f min", len(msgs), (msgs[-1][0] if msgs else 0) / 60)
    det = ChannelDetector(channel, cfg.detector, start_time=0.0)
    spikes: list[Spike] = []
    next_tick = 1.0
    for t, user, text in msgs:
        while t >= next_tick:
            spikes += det.tick(next_tick)
            next_tick += 1.0
        det.add(ChatMessage(channel, user, text, t))
    spikes += det.tick(next_tick + cfg.detector.max_episode_s + 5)
    spikes.sort(key=lambda s: -s.strength)
    if limit:
        spikes = spikes[:limit]
    log.info("replay: %d spikes: %s", len(spikes), [(round(s.event), s.mood, round(s.strength, 1)) for s in spikes])
    info = await media.probe(video)
    for sp in spikes:
        t0 = max(0.0, sp.event - cfg.moment.impact_search_before_s - cfg.moment.max_pre_s - 2)
        t1 = min(info.duration, sp.event + cfg.moment.max_post_s + 8)
        cid = f"{channel}-replay-{int(sp.event)}"
        job_dir = Path(cfg.edit.output_dir) / cid
        job_dir.mkdir(parents=True, exist_ok=True)
        raw = job_dir / "raw.ts"
        # frame-exact excerpt (fast re-encode) so replay timing matches the VOD clock
        await media.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t0:.3f}", "-i", str(video),
                         "-t", f"{t1 - t0:.3f}", "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset",
                         "ultrafast", "-crf", "14", "-c:a", "aac", "-b:a", "256k", str(raw)])
        state.add_clip(cid, channel, sp.event, sp.strength, sp.mood)
        job = factory.make_job(sp, raw, t0, display, "", job_dir)
        job.clip_id = cid
        await factory.submit(job)
    await factory.drain()
    await factory.stop()
    return factory.results
