"""ffmpeg/ffprobe wrappers and the processing preset for weak Android TV sticks."""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

DEFAULT_PRESET = {
    "max_height": 720,
    "fps": 0,  # zero preserves source cadence
    "black_crush": 12,
    "loudnorm": True,
    "mirror": False,
    "loop_crossfade": 0.0,
    "trim_start": 0.0,
    "trim_end": 0.0,
    "content_rotation": 0,
    "safe_margin": 0.0,
    "fade_in": 0.0,
    "fade_out": 0.0,
    "profile": "source",
    "source_duration": 0.0,
    "travel_direction": "",
    "travel_phase": "",
    "travel_seconds": 2.5,
}

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 3600) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise FFmpegError(tail or f"{cmd[0]} exited {proc.returncode}")
    return proc.stdout


def probe(path: Path) -> dict:
    out = _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 120)
    info = json.loads(out)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration") or (v or a or {}).get("duration") or 0)
    width = int(v.get("width", 0)) if v else 0
    height = int(v.get("height", 0)) if v else 0
    rotation = 0
    if v:
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rotation = int(sd["rotation"])
        rotation = int((v.get("tags") or {}).get("rotate", rotation))
    if abs(rotation) in (90, 270):
        width, height = height, width
    nb_frames = int(v.get("nb_frames", 0) or 0) if v else 0
    is_image = bool(v) and (duration == 0 or nb_frames == 1) and not a and v.get("codec_name") in ("png", "mjpeg", "webp", "bmp", "gif")
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "encoded_width": int(v.get("width", 0)) if v else 0,
        "encoded_height": int(v.get("height", 0)) if v else 0,
        "has_video": v is not None,
        "has_audio": a is not None,
        "is_image": is_image,
        "fps": float(Fraction(v.get("avg_frame_rate") or "0/1")) if v and v.get("avg_frame_rate") not in (None, "0/0") else 0,
        "frame_rate": v.get("avg_frame_rate", "0/1") if v else "0/1",
        "encoded_rotation": rotation,
        "codec": v.get("codec_name", "") if v else "",
    }


def _even_scale(max_h: int) -> str:
    max_w = int(round(max_h * 16 / 9))
    return (
        f"scale='min({max_w},iw)':'min({max_h},ih)':force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )


def build_video_cmd(src: Path, dst: Path, preset: dict, info: dict) -> list[str]:
    p = {**DEFAULT_PRESET, **(preset or {})}
    trim_start = max(0.0, float(p["trim_start"]))
    trim_end = max(0.0, float(p["trim_end"]))
    dur = max(0.1, float(p.get("source_duration") or info["duration"]) - trim_start - trim_end)
    xf = max(0.0, float(p["loop_crossfade"]))
    if xf * 2 >= dur:
        xf = 0.0

    fps = float(p.get("fps") or info.get("fps") or 30)
    if not 1 <= fps <= 120:
        raise FFmpegError("Frame rate must be between 1 and 120")
    rotation = {90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}.get(int(p.get("content_rotation", 0)), [])
    margin = max(0.0, min(0.2, float(p.get("safe_margin", 0))))
    framing = [f"pad=ceil(iw/{1-2*margin:.6f}/2)*2:ceil(ih/{1-2*margin:.6f}/2)*2:(ow-iw)/2:(oh-ih)/2:black"] if margin else []
    vf = rotation + framing + [_even_scale(int(p["max_height"])), f"fps={fps:.6f}", "format=yuv420p"]
    if p.get("source_duration"):
        # RIFE retains the final endpoint but has no next frame to interpolate.
        # Hold that endpoint to match the original audio/video duration.
        vf.append("tpad=stop_mode=clone:stop_duration=0.1")
    if p["mirror"]:
        vf.append("hflip")
    crush = float(p["black_crush"])
    if crush > 0:
        lvl = min(crush, 80) / 255.0
        vf.insert(2, f"colorlevels=rimin={lvl:.4f}:gimin={lvl:.4f}:bimin={lvl:.4f}")
    direction, phase = p.get("travel_direction"), p.get("travel_phase")
    if direction or phase:
        if direction not in ("left", "right") or phase not in ("enter", "exit"):
            raise FFmpegError("Travel requires a left/right direction and enter/exit phase")
        seconds = max(0.1, min(float(p["travel_seconds"]), dur))
        start = max(0, dur-seconds) if phase == "exit" else 0
        progress = f"clip((t-{start:.6f})/{seconds:.6f},0,1)"
        amount = progress if phase == "exit" else f"({progress}-1)"
        sign = 1 if direction == "right" else -1
        # Translate the black-backed take after interpolation. The viewport stays
        # stationary; only the character/effects cross its boundary at delivery FPS.
        vf += ["pad=iw*3:ih:iw:0:black", f"crop=iw/3:ih:x='iw/3-({sign})*iw/3*{amount}':y=0:exact=1"]
    af = ["loudnorm=I=-16:TP=-1.5:LRA=11"] if p["loudnorm"] else []
    if p.get("profile") == "generated60":
        # Sharp generated transients can overshoot after AAC decoding. Reserve
        # additional delivery headroom without changing their timing.
        af.append("volume=-2dB")
    for name in ("fade_in", "fade_out"):
        seconds = min(max(0, float(p.get(name, 0))), dur / 2)
        if seconds:
            direction = "in" if name == "fade_in" else "out"
            start = 0 if direction == "in" else dur - seconds
            vf.append(f"fade=t={direction}:st={start:.6f}:d={seconds:.6f}")
            af.append(f"afade=t={direction}:st={start:.6f}:d={seconds:.6f}")
    has_audio = info["has_audio"]

    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    if trim_start:
        cmd += ["-ss", f"{trim_start:.3f}"]
    cmd += ["-i", str(src), "-t", f"{dur:.3f}"]

    graph = [f"[0:v]{','.join(vf)}[vp]"]
    if xf > 0:
        graph += [
            "[vp]split[v1][v2]",
            f"[v1]trim=start={xf:.3f},setpts=PTS-STARTPTS[body]",
            f"[v2]trim=0:{xf:.3f},setpts=PTS-STARTPTS[head]",
            f"[body][head]xfade=transition=fade:duration={xf:.3f}:offset={dur - 2 * xf:.3f}[vout]",
        ]
    else:
        graph.append("[vp]null[vout]")
    if has_audio:
        a_chain = ",".join(af + ["aresample=48000"])
        graph.append(f"[0:a]{a_chain}[ap]")
        if xf > 0:
            graph += [
                "[ap]asplit[a1][a2]",
                f"[a1]atrim=start={xf:.3f},asetpts=PTS-STARTPTS[abody]",
                f"[a2]atrim=0:{xf:.3f},asetpts=PTS-STARTPTS[ahead]",
                f"[abody][ahead]acrossfade=d={xf:.3f}[aout]",
            ]
        else:
            graph.append("[ap]anull[aout]")

    cmd += ["-filter_complex", ";".join(graph), "-map", "[vout]"]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-preset", "medium", "-crf", "20",
        "-g", str(round(fps)), "-keyint_min", str(round(fps)), "-sc_threshold", "0",
        "-fps_mode", "cfr", "-movflags", "+faststart", str(dst),
    ]
    return cmd


def build_audio_cmd(src: Path, dst: Path, preset: dict) -> list[str]:
    p = {**DEFAULT_PRESET, **(preset or {})}
    af = ["loudnorm=I=-16:TP=-1.5:LRA=11"] if p["loudnorm"] else []
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn"]
    if af:
        cmd += ["-af", ",".join(af)]
    return cmd + ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(dst)]


def transcode(cmd: list[str]) -> None:
    _run(cmd)


def thumbnail(src: Path, dst: Path, at: float) -> None:
    _run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src),
          "-frames:v", "1", "-vf", "scale=480:-2", str(dst)], 120)


def preview_gif(src: Path, dst: Path, start: float, seconds: float = 5.0) -> None:
    vf = "fps=10,scale=280:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer"
    _run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}",
          "-i", str(src), "-filter_complex", vf, "-loop", "0", str(dst)], 300)


def waveform(src: Path, dst: Path) -> None:
    _run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-filter_complex",
          "showwavespic=s=480x270:colors=0xff7a1a", "-frames:v", "1", str(dst)], 120)


def image_thumb(src: Path, dst: Path) -> None:
    _run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", "scale=480:-2", str(dst)], 60)
