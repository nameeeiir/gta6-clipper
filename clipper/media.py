"""Small ffmpeg/ffprobe helpers and audio analysis (loudness envelope)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 16000
FRAME_S = 0.05


async def run(cmd: list[str], *, input_bytes: bytes | None = None) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE if input_bytes else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(input_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({proc.returncode}): {err.decode(errors='replace')[-1500:]}")
    return out


@dataclass
class ProbeInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


async def probe(path: Path) -> ProbeInfo:
    out = await run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    j = json.loads(out)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise RuntimeError(f"no video stream in {path}")
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    dur = float(j["format"].get("duration") or v.get("duration") or 0)
    return ProbeInfo(
        duration=dur, width=int(v["width"]), height=int(v["height"]), fps=fps or 30.0,
        has_audio=any(s["codec_type"] == "audio" for s in j["streams"]),
    )


async def load_audio(path: Path, start: float = 0.0, duration: float | None = None) -> np.ndarray:
    """Mono float32 @16 kHz (what both the envelope and whisper want)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(path)]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"]
    try:
        out = await run(cmd)
    except RuntimeError:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(out, dtype=np.float32).copy()


def envelope_db(audio: np.ndarray) -> np.ndarray:
    """RMS loudness in dBFS per 50 ms frame."""
    n = int(SR * FRAME_S)
    frames = len(audio) // n
    if frames == 0:
        return np.full(1, -90.0)
    x = audio[: frames * n].reshape(frames, n)
    rms = np.sqrt(np.mean(x * x, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def smooth(x: np.ndarray, frames: int) -> np.ndarray:
    if frames <= 1 or len(x) < frames:
        return x
    k = np.ones(frames) / frames
    return np.convolve(x, k, mode="same")


def find_impact(env: np.ndarray, t_lo: float, t_hi: float, prefer: float | None = None,
                prefer_db_per_s: float = 0.35) -> tuple[float, float]:
    """Find the most 'impactful' instant in [t_lo, t_hi] (seconds into env):
    a loud moment that is much louder than the second and a half before it
    (screams, explosions, crashes). With `prefer`, candidates lose `prefer_db_per_s` per second
    of distance from that time, so a wide window still favours the expected moment.
    Returns (time, strength_db) where strength excludes the distance penalty."""
    if len(env) < 10:
        return (t_lo + t_hi) / 2, 0.0
    lo = max(0, int(t_lo / FRAME_S))
    hi = min(len(env) - 1, int(t_hi / FRAME_S))
    if hi <= lo:
        return (t_lo + t_hi) / 2, 0.0
    short = smooth(env, 4)  # 200 ms
    back = int(1.5 / FRAME_S)
    vals = np.full(hi + 1, -1e9)
    best_i, best_score = lo, -1e9
    for i in range(lo, hi + 1):
        prev = env[max(0, i - back) : i]
        base = float(np.median(prev)) if len(prev) else float(short[i])
        jump = float(short[i]) - base
        vals[i] = jump + 0.35 * (float(short[i]) + 30)  # favour loud AND sudden
        score = vals[i] - (prefer_db_per_s * abs(i * FRAME_S - prefer) if prefer is not None else 0.0)
        if score > best_score:
            best_score, best_i = score, i
    # The preference can drag the pick toward the end of a sustained scream; snap back to where
    # this loud moment starts (earliest frame within 0.75s that is nearly as strong).
    j = best_i
    while j - 1 >= max(lo, best_i - int(0.75 / FRAME_S)) and vals[j - 1] >= vals[best_i] - 1.0:
        j -= 1
    return j * FRAME_S, float(vals[best_i])


def quiet_point(env: np.ndarray, t: float, radius: float = 0.6) -> float:
    """Snap a cut point to the quietest frame nearby so we never cut mid-word."""
    if len(env) < 3:
        return t
    lo = max(0, int((t - radius) / FRAME_S))
    hi = min(len(env) - 1, int((t + radius) / FRAME_S))
    if hi <= lo:
        return t
    s = smooth(env, 3)
    i = lo + int(np.argmin(s[lo : hi + 1]))
    return i * FRAME_S


def silent_spans(env: np.ndarray, start: float, end: float, min_len: float) -> list[tuple[float, float]]:
    """Spans inside [start, end] that are dead air relative to this clip's own loudness."""
    lo, hi = int(start / FRAME_S), min(len(env), int(end / FRAME_S))
    if hi - lo < 4:
        return []
    seg = env[lo:hi]
    loud = float(np.percentile(seg, 80))
    thr = min(loud - 18.0, -38.0)
    quiet = smooth(seg, 3) < thr
    spans: list[tuple[float, float]] = []
    i = 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            a, b = (lo + i) * FRAME_S, (lo + j) * FRAME_S
            if b - a >= min_len:
                spans.append((a, b))
            i = j
        else:
            i += 1
    return spans
