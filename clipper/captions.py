"""Word-by-word 'karaoke' captions, hook text and streamer credit as an ASS subtitle file."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .transcribe import Sentence


class TimeMap:
    """Source time -> output time after dead-air cuts and speed-up."""

    def __init__(self, keep: list[tuple[float, float]], speed: float):
        self.keep = keep
        self.speed = speed
        self._cum = []
        acc = 0.0
        for a, b in keep:
            self._cum.append(acc)
            acc += b - a
        self.duration = acc / speed

    def __call__(self, t: float) -> float | None:
        for (a, b), c in zip(self.keep, self._cum):
            if a <= t <= b:
                return (c + t - a) / self.speed
        return None

    def snap(self, t: float) -> float | None:
        """Like __call__, but a time inside a cut snaps to the next kept moment."""
        v = self(t)
        if v is not None:
            return v
        for (a, _b), c in zip(self.keep, self._cum):
            if a > t:
                return c / self.speed
        return None


@dataclass
class Layout:
    caption_y: int
    hook_y: int
    credit_y: int


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(s: str) -> str:
    return s.replace("\\", "/").replace("{", "(").replace("}", ")")


def _censor(word: str, banned: set[str]) -> str:
    core = re.sub(r"[^\w']", "", word).lower()
    if core and any(core.startswith(b) for b in banned):
        return word[0] + "*" * (len(word) - 1)
    return word


def build_ass(sentences: list[Sentence], tmap: TimeMap, layout: Layout, cfg, *, width: int, height: int,
              hook: str = "", credit: str = "") -> str:
    font = cfg.caption_font
    base = ass_color(cfg.caption_color)
    hl = ass_color(cfg.caption_highlight)
    accent = ass_color(cfg.progress_color)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Cap,{font},{cfg.caption_size},{base},{base},&H00000000,&H90000000,-1,0,0,0,100,100,1,0,1,7,4,5,70,70,0,1",
        f"Style: Hook,{font},{int(cfg.caption_size * 0.82)},&H00000000,&H00000000,{accent},{accent},-1,0,0,0,100,100,1,0,3,20,0,5,90,90,0,1",
        f"Style: Credit,{font},{int(cfg.caption_size * 0.42)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,3,0,5,40,40,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    cx = width // 2
    if hook and cfg.hook_text:
        hook_end = min(cfg.hook_seconds, tmap.duration)
        lines.append(
            f"Dialogue: 2,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,"
            f"{{\\pos({cx},{layout.hook_y})\\fscx70\\fscy70\\t(0,140,\\fscx100\\fscy100)\\fad(0,180)}}{_esc(hook.upper())}"
        )
    if credit and cfg.credit_text:
        lines.append(
            f"Dialogue: 1,{ass_time(0)},{ass_time(tmap.duration)},Credit,,0,0,0,,"
            f"{{\\pos({cx},{layout.credit_y})\\alpha&H30&}}{_esc(credit)}"
        )
    if cfg.captions:
        lines += _caption_events(sentences, tmap, layout, cfg, cx, hl)
    return "\n".join(lines) + "\n"


def _caption_events(sentences, tmap: TimeMap, layout: Layout, cfg, cx: int, hl: str) -> list[str]:
    banned = {w.lower() for w in cfg.censor_words}
    words = []
    for s in sentences:
        for w in s.words:
            a, b = tmap(w.start), tmap(w.end)
            if a is None and b is None:
                continue
            a = a if a is not None else tmap.snap(w.start)
            b = b if b is not None else (a or 0) + (w.end - w.start) / tmap.speed
            if a is None:
                continue
            text = w.text.strip().strip(".,;:\"")
            if not text:
                continue
            text = _censor(text, banned)
            words.append((a, max(b, a + 0.06), text.upper() if cfg.uppercase else text, w.text.rstrip()[-1:] in ".?!"))

    groups: list[list[tuple[float, float, str, bool]]] = []
    cur: list = []
    for w in words:
        chars = sum(len(x[2]) + 1 for x in cur) + len(w[2])
        if cur and (len(cur) >= cfg.words_per_caption or chars > cfg.caption_max_chars or w[0] - cur[-1][1] > 0.45 or cur[-1][3]):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    out = []
    for gi, g in enumerate(groups):
        next_start = groups[gi + 1][0][0] if gi + 1 < len(groups) else tmap.duration
        for i, (a, b, _t, _p) in enumerate(g):
            end = g[i + 1][0] if i + 1 < len(g) else min(b + 0.35, next_start)
            if end - a < 0.04:
                continue
            parts = []
            for j, (_a, _b, t, _p2) in enumerate(g):
                t = _esc(t)
                parts.append(f"{{\\c{hl}\\fscx112\\fscy112}}{t}{{\\r}}" if j == i else t)
            pop = "\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)" if i == 0 else ""
            out.append(
                f"Dialogue: 0,{ass_time(a)},{ass_time(end)},Cap,,0,0,0,,{{\\pos({cx},{layout.caption_y}){pop}}}" + " ".join(parts)
            )
    return out
