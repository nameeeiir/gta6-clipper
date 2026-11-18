"""Render a ClipPlan into a 9:16 short with one ffmpeg call.

Retention edits: dead-air jump cuts, slight speed-up, big gameplay, hook text, word-by-word captions,
punch-in zoom + camera shake + flash on the payoff, a synthesized sub-boom and echo tail on the
impact, bass hit, voice presence EQ + compression, -14 LUFS loudness, top progress bar.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import media
from .captions import Layout, TimeMap, build_ass
from .moment import ClipPlan

log = logging.getLogger(__name__)

# Caption font shipped with the app (Inter Black, SIL OFL 1.1) so captions look the same on
# Windows/Mac/Linux even when the font isn't installed.
BUNDLED_FONTS = Path(__file__).parent / "fonts"


@dataclass
class RenderResult:
    path: Path
    duration: float
    cover_ms: int
    layout: str


def _even(x: float) -> int:
    return int(round(x / 2)) * 2


def _fmt(x: float) -> str:
    return f"{x:.3f}"


class VideoEditor:
    def __init__(self, ecfg, streamer_cfg_lookup):
        self.e = ecfg
        self.streamer_cfg = streamer_cfg_lookup

    def choose_layout(self, channel: str) -> tuple[str, list[float] | None]:
        sc = self.streamer_cfg(channel)
        layout = sc.layout or self.e.layout
        if layout == "auto":
            layout = "split" if sc.facecam else "blur"
        if layout == "split" and not sc.facecam:
            layout = "blur"
        return layout, sc.facecam

    # ------------------------------------------------------------------------------------------
    def _layout_graph(self, layout: str, facecam, src_w: int, src_h: int) -> tuple[list[str], Layout]:
        W, H = self.e.width, self.e.height
        g: list[str] = []
        if layout == "split":
            fx, fy, fw, fh = facecam
            cam_h = _even(H * 0.36)
            game_h = H - cam_h
            cw, ch = _even(src_w * fw), _even(src_h * fh)
            cx, cy = _even(src_w * fx), _even(src_h * fy)
            g.append("[vsp]split=2[cam_src][game_src]")
            g.append(f"[cam_src]crop={cw}:{ch}:{cx}:{cy},scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[cam]")
            g.append(f"[game_src]crop='min(iw,ih*{W}/{game_h})':ih,scale={W}:{game_h}[game]")
            g.append("[cam][game]vstack=inputs=2,setsar=1[base]")
            return g, Layout(caption_y=cam_h, hook_y=cam_h + 230, credit_y=60)
        if layout == "crop":
            g.append(f"[vsp]crop='min(iw,ih*{W}/{H})':ih,scale={W}:{H},setsar=1[base]")
            return g, Layout(caption_y=int(H * 0.66), hook_y=330, credit_y=150)
        # blur (default): blurred fill + big gameplay
        fg_w = _even(W * self.e.gameplay_zoom)
        fg_h = min(_even(fg_w * src_h / src_w), _even(H * 0.62))
        crop_w = min(W, fg_w)
        y = _even((H - fg_h) / 2 - 80)
        g.append("[vsp]split=2[bg_src][fg_src]")
        g.append(
            f"[bg_src]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,"
            f"scale={W}:{H},eq=brightness=-0.10:saturation=1.15[bg]"
        )
        g.append(f"[fg_src]scale={fg_w}:-2,crop={crop_w}:'min(ih,{fg_h})'[fg]")
        g.append(f"[bg][fg]overlay=(W-w)/2:{y},setsar=1[base]")
        # TikTok/Reels/Shorts cover the bottom ~22% (username, description) and the top ~10%
        # (tabs) with UI, so captions sit over the lower part of the gameplay, the hook above it.
        return g, Layout(caption_y=min(int(H * 0.66), y + fg_h - 70), hook_y=max(270, y - 140),
                         credit_y=min(int(H * 0.74), y + fg_h + 45))

    def _encoder_args(self) -> list[str]:
        e = self.e
        fps = str(e.fps)
        common = ["-r", fps, "-g", str(e.fps * 2), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                  "-c:a", "aac", "-b:a", e.audio_bitrate, "-ar", "48000", "-ac", "2"]
        if e.encoder == "h264_nvenc":
            v = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(e.crf + 2), "-b:v", "0"]
        elif e.encoder == "h264_qsv":
            v = ["-c:v", "h264_qsv", "-global_quality", str(e.crf), "-preset", "medium"]
        elif e.encoder == "h264_videotoolbox":
            v = ["-c:v", "h264_videotoolbox", "-b:v", "12M"]
        elif e.encoder == "h264_vaapi":
            v = ["-c:v", "h264_vaapi", "-qp", str(e.crf)]
        else:
            v = ["-c:v", "libx264", "-preset", e.preset, "-crf", str(e.crf), "-profile:v", "high"]
        return v + common

    # ------------------------------------------------------------------------------------------
    def build_command(self, plan: ClipPlan, probe: media.ProbeInfo, job_dir: Path, out_path: Path) -> tuple[list[str], float, float, str]:
        e = self.e
        W, H, fps, speed = e.width, e.height, e.fps, max(0.5, min(e.speed, 2.0))
        tmap = TimeMap(plan.keep, speed)
        D = tmap.duration
        A = tmap.snap(plan.anchor) or min(D * 0.6, D - 1.5)
        A = max(0.0, min(A, D - 0.5))

        layout_name, facecam = self.choose_layout(plan.channel)
        layout_g, layout = self._layout_graph(layout_name, facecam, probe.width, probe.height)

        ass_text = build_ass(
            plan.sentences, tmap, layout, e, width=W, height=H, hook=plan.meta.hook_text,
            credit=f"twitch.tv/{plan.channel}",
        )
        (job_dir / "captions.ass").write_text(ass_text, encoding="utf-8")
        fontsdir = ""
        if e.fonts_dir:
            fontsdir = f":fontsdir={e.fonts_dir}"
        elif BUNDLED_FONTS.is_dir():
            # relative path (ffmpeg runs in job_dir): absolute Windows paths ("C:\...") break filter syntax
            fdir = job_dir / "fonts"
            fdir.mkdir(exist_ok=True)
            for f in BUNDLED_FONTS.glob("*.[ot]tf"):
                shutil.copyfile(f, fdir / f.name)
            fontsdir = ":fontsdir=fonts"

        seek = max(0.0, plan.keep[0][0] - 1.0)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", _fmt(seek), "-i", str(plan.src.resolve())]
        sfx_idx = None
        if e.impact_sfx:
            sfx_idx = 1
            cmd += ["-f", "lavfi", "-i",
                    "aevalsrc='0.9*sin(2*PI*t*(46+80*exp(-9*t)))*exp(-3.0*t)':s=48000:d=1.6"]
        anull_idx = None
        if not probe.has_audio:
            anull_idx = 2 if sfx_idx else 1
            cmd += ["-f", "lavfi", "-t", _fmt(sum(b - a for a, b in plan.keep) + 1), "-i", "anullsrc=r=48000:cl=stereo"]

        g: list[str] = []
        k = len(plan.keep)
        # 1. jump cuts
        if k == 1:
            a, b = plan.keep[0]
            g.append(f"[0:v]trim=start={_fmt(a - seek)}:end={_fmt(b - seek)},setpts=PTS-STARTPTS[vcat]")
            if anull_idx is None:
                g.append(f"[0:a]atrim=start={_fmt(a - seek)}:end={_fmt(b - seek)},asetpts=PTS-STARTPTS[acat]")
        else:
            g.append(f"[0:v]split={k}" + "".join(f"[vs{i}]" for i in range(k)))
            if anull_idx is None:
                g.append(f"[0:a]asplit={k}" + "".join(f"[as{i}]" for i in range(k)))
            pairs = []
            for i, (a, b) in enumerate(plan.keep):
                g.append(f"[vs{i}]trim=start={_fmt(a - seek)}:end={_fmt(b - seek)},setpts=PTS-STARTPTS[vk{i}]")
                if anull_idx is None:
                    g.append(f"[as{i}]atrim=start={_fmt(a - seek)}:end={_fmt(b - seek)},asetpts=PTS-STARTPTS,"
                             f"afade=t=in:d=0.02,afade=t=out:st={_fmt(max(0, b - a - 0.02))}:d=0.02[ak{i}]")
                    pairs.append(f"[vk{i}][ak{i}]")
                else:
                    pairs.append(f"[vk{i}]")
            if anull_idx is None:
                g.append("".join(pairs) + f"concat=n={k}:v=1:a=1[vcat][acat]")
            else:
                g.append("".join(pairs) + f"concat=n={k}:v=1:a=0[vcat]")
        if anull_idx is not None:
            g.append(f"[{anull_idx}:a]atrim=duration={_fmt(D * speed)}[acat]")

        # 2. pacing
        g.append(f"[vcat]setpts=PTS/{speed},fps={fps}[vsp]")
        g.append(f"[acat]atempo={speed},aformat=sample_rates=48000:channel_layouts=stereo[asp]")

        # 3. layout
        g += layout_g

        # 4. payoff: punch-in zoom + shake + flash
        v = "base"
        if e.punch_zoom and e.punch_zoom > 1.0:
            zw, zh = _even(W * e.punch_zoom), _even(H * e.punch_zoom)
            S = e.shake_px
            shake = f"between(t,{_fmt(A)},{_fmt(A + 0.5)})"
            g.append("[base]split=2[b0][b1]")
            g.append(
                f"[b1]scale={zw}:{zh},crop={W}:{H}:x='(in_w-out_w)/2+{S}*sin(t*53)*{shake}':"
                f"y='(in_h-out_h)/2+{S}*cos(t*41)*{shake}'[bz]"
            )
            g.append(f"[b0][bz]overlay=0:0:enable='between(t,{_fmt(A)},{_fmt(A + e.punch_duration_s)})'[vz]")
            v = "vz"
        if e.flash:
            g.append(f"[{v}]drawbox=x=0:y=0:w=iw:h=ih:color=white@0.45:t=fill:enable='between(t,{_fmt(A)},{_fmt(A + 0.07)})'[vf]")
            v = "vf"

        # 5. captions / hook / credit
        g.append(f"[{v}]ass=filename=captions.ass{fontsdir}[vsub]")
        v = "vsub"

        # 6. progress bar
        if e.progress_bar:
            g.append(f"color=c=0x{e.progress_color}:s={W}x12:r={fps}[pb]")
            g.append(f"[{v}][pb]overlay=x='-w+w*t/{_fmt(D)}':y=0:shortest=1[vout]")
            v = "vout"

        # 7. audio: voice clarity + punch, impact hit, loudness
        a = "asp"
        if e.voice_enhance:
            g.append("[asp]highpass=f=60,equalizer=f=3200:t=q:w=1.0:g=3,equalizer=f=250:t=q:w=1.0:g=-2,"
                     "acompressor=threshold=0.125:ratio=3:attack=8:release=180:makeup=1.6[av]")
            a = "av"
        if e.impact_bass_db:
            g.append(f"[{a}]bass=g={e.impact_bass_db}:f=110:w=0.7:enable='between(t,{_fmt(A - 0.05)},{_fmt(A + 1.2)})'[ab]")
            a = "ab"
        mix = [f"[{a}]"]
        if e.impact_echo:
            g.append(f"[{a}]asplit=2[adry][aesrc]")
            mix = ["[adry]"]
            g.append(
                f"[aesrc]atrim=start={_fmt(A)}:end={_fmt(min(D, A + 0.9))},asetpts=PTS-STARTPTS,apad=pad_dur=1.2,"
                f"aecho=0.8:0.7:180|360:0.35|0.2,afade=t=out:st=1.2:d=0.9,"
                f"adelay=delays={int(A * 1000)}:all=1,volume=0.45[aecho]"
            )
            mix.append("[aecho]")
        if sfx_idx is not None:
            g.append(f"[{sfx_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"adelay=delays={int(A * 1000)}:all=1,volume=0.8[aboom]")
            mix.append("[aboom]")
        if len(mix) > 1:
            g.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first[amixed]")
            a = "amixed"
        else:
            a = mix[0][1:-1]
        g.append(f"[{a}]loudnorm=I={e.loudness_lufs}:TP=-1.5:LRA=11,aresample=48000,"
                 f"aformat=sample_rates=48000:channel_layouts=stereo[aout]")

        cmd += ["-filter_complex", ";\n".join(g), "-map", f"[{v}]", "-map", "[aout]", "-t", _fmt(D)]
        cmd += self._encoder_args() + [str(out_path.resolve())]
        return cmd, D, A, layout_name

    async def render(self, plan: ClipPlan, job_dir: Path) -> RenderResult:
        job_dir.mkdir(parents=True, exist_ok=True)
        probe = await media.probe(plan.src)
        out = job_dir / f"{plan.clip_id}.mp4"
        cmd, D, A, layout = self.build_command(plan, probe, job_dir, out)
        (job_dir / "ffmpeg_cmd.txt").write_text(" ".join(f"'{c}'" if " " in c or ";" in c else c for c in cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(job_dir), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        shutil.rmtree(job_dir / "fonts", ignore_errors=True)
        if proc.returncode != 0:
            raise RuntimeError(f"render failed: {err.decode(errors='replace')[-2000:]}")
        if not self.e.keep_raw:
            try:
                if plan.src.parent == job_dir:
                    plan.src.unlink(missing_ok=True)
            except OSError:
                pass
        return RenderResult(path=out, duration=D, cover_ms=int(A * 1000), layout=layout)


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
