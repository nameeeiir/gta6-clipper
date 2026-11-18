"""Turn a chat spike into an exact cut: payoff anchor, start/end, dead-air removal, metadata."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import media
from .detector import Spike
from .llm import Editor, Refinement, template_refinement
from .transcribe import Sentence, Transcriber

log = logging.getLogger(__name__)


@dataclass
class ClipPlan:
    clip_id: str
    channel: str
    display_name: str
    src: Path
    src_wall0: float
    start: float  # seconds in src
    end: float
    anchor: float  # payoff moment (seconds in src)
    keep: list[tuple[float, float]]  # src ranges kept after dead-air removal
    sentences: list[Sentence]
    mood: str
    meta: Refinement
    spike: Spike
    info: dict = field(default_factory=dict)

    @property
    def kept_duration(self) -> float:
        return sum(b - a for a, b in self.keep)


class MomentPicker:
    def __init__(self, mcfg, ecfg, transcriber: Transcriber, editor: Editor, default_tags: list[str]):
        self.m = mcfg
        self.e = ecfg
        self.transcriber = transcriber
        self.editor = editor
        self.default_tags = default_tags

    async def plan(self, clip_id: str, spike: Spike, src: Path, src_wall0: float, display_name: str,
                   stream_title: str = "") -> ClipPlan:
        m = self.m
        info = await media.probe(src)
        dur = info.duration
        audio = await media.load_audio(src)
        env = media.envelope_db(audio)
        ev = spike.event - src_wall0
        onset = spike.onset - src_wall0

        # 1. payoff anchor: loudest sudden moment before chat reacted. The window is wide because
        # viewers' stream delay differs from ours by several seconds either way; `prefer` keeps
        # the search centred on the expected moment.
        lo = max(0.0, ev - m.impact_search_before_s)
        hi = max(lo + 0.5, onset - 0.3)
        anchor, strength = media.find_impact(env, lo, hi, prefer=ev)
        if strength < 6.0:
            anchor = ev
        anchor = float(np.clip(anchor, m.min_pre_s, max(m.min_pre_s, dur - m.min_post_s)))

        # 2. transcript (word timestamps) for sentence-aligned cuts + captions
        sentences = await self.transcriber.transcribe(audio)

        start, end = self._heuristic_cut(env, sentences, anchor, dur)

        # 3. Claude: quality gate + final cut + metadata (template fallback)
        bounds = (
            max(0.0, anchor - m.max_pre_s), max(0.0, anchor - 1.0),
            min(dur, anchor + 1.5), min(dur, anchor + m.max_post_s + 6.0),
        )
        transcript = [(s.start, s.end, s.text) for s in sentences if s.end > bounds[0] - 5 and s.start < bounds[3] + 2]
        chat = [(t - src_wall0, u, txt) for t, u, txt in spike.top_messages]
        meta = await self.editor.refine(
            name=display_name, stream_title=stream_title, mood=spike.mood, transcript=transcript, chat=chat,
            anchor=anchor, start=start, end=end, bounds=bounds,
        )
        if meta is not None:
            s2 = float(np.clip(meta.start_s, bounds[0], bounds[1]))
            e2 = float(np.clip(meta.end_s, bounds[2], bounds[3]))
            if m.min_len_s * 0.7 <= e2 - s2 <= m.max_len_s:
                start, end = s2, e2
            meta.start_s, meta.end_s = start, end
            if not meta.hashtags:
                meta.hashtags = list(self.default_tags)
        else:
            meta = template_refinement(display_name, spike.mood, start, end, self.default_tags)

        keep = self._keep_ranges(env, sentences, start, end, anchor)
        log.info("[%s] plan %s: %.1f-%.1fs anchor %.1fs (impact %.1f dB), %d cuts, quality %s via %s",
                 spike.channel, clip_id, start, end, anchor, strength, len(keep) - 1, meta.quality_score, meta.source)
        return ClipPlan(
            clip_id=clip_id, channel=spike.channel, display_name=display_name, src=src, src_wall0=src_wall0,
            start=start, end=end, anchor=anchor, keep=keep, sentences=sentences, mood=spike.mood, meta=meta,
            spike=spike, info={"impact_db": round(strength, 1), "src_duration": dur},
        )

    def _heuristic_cut(self, env: np.ndarray, sentences: list[Sentence], anchor: float, dur: float) -> tuple[float, float]:
        m = self.m
        # start: a sentence start near (anchor - target_pre) so the clip opens on speech, not mid-word
        target = anchor - m.target_pre_s
        lo, hi = anchor - m.max_pre_s, anchor - m.min_pre_s
        starts = [s.start for s in sentences if lo <= s.start <= hi]
        if starts:
            start = min(starts, key=lambda s: abs(s - target)) - 0.1
        else:
            start = media.quiet_point(env, max(0.0, target))
        start = max(0.0, start)

        # end: finish the sentence that is running when the reaction has had time to land
        min_end = anchor + m.min_post_s
        max_end = min(dur, anchor + m.max_post_s)
        ends = [s.end for s in sentences if s.end >= min_end]
        if ends and ends[0] <= max_end:
            end = ends[0] + 0.35
            # keep going while the streamer is still talking with barely a pause (reaction rant)
            for s in sentences:
                if s.start > end - 0.35 and s.start - end < 0.6 and s.end <= max_end:
                    end = s.end + 0.35
        else:
            end = media.quiet_point(env, min(max_end, anchor + max(m.min_post_s + 2.0, 6.0)), radius=0.8)
        end = min(end, dur)

        # length limits
        if end - start < m.min_len_s:
            start = max(0.0, max(anchor - m.max_pre_s, end - m.min_len_s))
            if end - start < m.min_len_s:
                end = min(dur, start + m.min_len_s)
        if end - start > m.max_len_s:
            start = end - m.max_len_s
        return round(start, 2), round(end, 2)

    def _keep_ranges(self, env: np.ndarray, sentences: list[Sentence], start: float, end: float,
                     anchor: float) -> list[tuple[float, float]]:
        if not self.e.cut_dead_air:
            return [(start, end)]
        word_spans = [(w.start - 0.08, w.end + 0.08) for s in sentences for w in s.words]
        protect = (anchor - 1.5, anchor + 2.5)
        keep_pad = self.e.dead_air_keep_s / 2
        cuts = []
        for a, b in media.silent_spans(env, start, end, self.e.dead_air_min_s):
            if a < protect[1] and b > protect[0]:
                continue
            if any(wa < b and wb > a for wa, wb in word_spans):
                continue
            ca, cb = a + keep_pad, b - keep_pad
            if cb - ca > 0.25:
                cuts.append((ca, cb))
        keep, cur = [], start
        for a, b in cuts:
            if a > cur:
                keep.append((round(cur, 3), round(a, 3)))
            cur = max(cur, b)
        if end > cur:
            keep.append((round(cur, 3), round(end, 3)))
        return keep or [(start, end)]
