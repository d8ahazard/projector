"""Local, explainable movement analysis for projection choreography.

Sources remain read-only. The analyzer combines shot boundaries with foreground
visibility so continuous performances can split at clean background gaps.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ANALYZER_VERSION = 2
SAMPLE_FPS = 4.0
MIN_SECONDS = 4.0
MAX_SECONDS = 30.0


def _rotate(frame: np.ndarray, angle: int) -> np.ndarray:
    if angle == 90: return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180: return cv2.rotate(frame, cv2.ROTATE_180)
    if angle == 270: return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def _edge(x: float, y: float, margin: float = 0.24) -> str:
    name, distance = min({"left": x, "right": 1-x, "top": y, "bottom": 1-y}.items(), key=lambda p: p[1])
    return name if distance <= margin else "none"


def _hard_cuts(path: Path) -> list[float]:
    """Use PySceneDetect, with the installed FFmpeg scdet filter as fallback."""
    try:
        from scenedetect import AdaptiveDetector, SceneManager, open_video
        video = open_video(str(path))
        manager = SceneManager(); manager.auto_downscale = True
        manager.add_detector(AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=8))
        manager.detect_scenes(video=video, frame_skip=2, show_progress=False)
        scenes = manager.get_scene_list(start_in_scene=True)
        return sorted({round(scene[1].get_seconds(), 3) for scene in scenes[:-1]})
    except Exception:
        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path), "-vf",
                 "scdet=threshold=10,metadata=print", "-an", "-f", "null", "-"],
                capture_output=True, text=True, timeout=1800,
            )
            return sorted({float(x) for x in re.findall(r"lavfi\.scd\.time=([0-9.]+)", proc.stderr)})
        except Exception:
            return []


@dataclass
class Sample:
    t: float
    area: float
    x: float | None
    y: float | None


def _decode_samples(path: Path, rotation: int) -> tuple[list[Sample], float, float, list[float]]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened(): raise ValueError(f"Could not open video: {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    if total < 2 or fps <= 0:
        raise ValueError("Video has too few frames for movement analysis")
    duration = total / fps
    if rotation in (90, 270): width, height = height, width
    scale = min(1.0, 320/max(width, height))
    out_w, out_h = max(2, round(width*scale)), max(2, round(height*scale))
    rotate_filter = {90:"transpose=1,", 180:"hflip,vflip,", 270:"transpose=2,"}.get(rotation, "")
    vf = f"{rotate_filter}fps={SAMPLE_FPS},scale={out_w}:{out_h},format=gray"
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-noautorotate", "-i", str(path), "-an", "-vf", vf,
                             "-f", "rawvideo", "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    grays: list[np.ndarray] = []
    times: list[float] = []
    frame_bytes = out_w*out_h
    assert proc.stdout is not None
    while True:
        data = proc.stdout.read(frame_bytes)
        if len(data) != frame_bytes: break
        grays.append(np.frombuffer(data, np.uint8).reshape(out_h, out_w).copy())
        times.append((len(grays)-1)/SAMPLE_FPS)
    stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0: raise ValueError(f"FFmpeg sampling failed: {stderr[-500:]}")
    if len(grays) < 8: raise ValueError("Could not decode enough frames for movement analysis")

    background = np.percentile(np.stack(grays).astype(np.float32), 35, axis=0).astype(np.uint8)
    background_luma = float(np.median(background))
    kernel = np.ones((3, 3), np.uint8)
    raw: list[tuple[float, float | None, float | None]] = []
    for gray in grays:
        diff = cv2.absdiff(gray, background)
        mask = diff > max(12.0, float(np.percentile(diff, 84)))
        if background_luma < 38: mask |= gray > max(28.0, float(np.percentile(gray, 74)))
        binary = (mask.astype(np.uint8)*255)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        area = float(np.count_nonzero(binary))/binary.size
        moments = cv2.moments(binary)
        raw.append((area, None, None) if area < .0025 or moments["m00"] <= 0 else
                   (area, moments["m10"]/moments["m00"]/binary.shape[1], moments["m01"]/moments["m00"]/binary.shape[0]))
    areas = np.array([x[0] for x in raw])
    smooth = np.array([np.median(areas[max(0, i-1):i+2]) for i in range(len(areas))])
    # The low percentile estimates compression/background noise. Using a mid
    # percentile would classify a performer visible for most of the clip as
    # the background and erase exactly the crossings we want to find.
    active_threshold = max(.004, float(np.percentile(smooth, 10))*1.6)
    coarse_cuts = []
    for i in range(1, len(grays)):
        # Large full-frame change at the reduced sampling cadence is a cheap
        # candidate detector. PySceneDetect refines only these files, avoiding
        # a second full decode for the many continuous AtmosFX performances.
        change = float(cv2.absdiff(grays[i], grays[i-1]).mean())/255
        if change >= .19: coarse_cuts.append(times[i])
    return [Sample(t, float(smooth[i]), raw[i][1] if smooth[i] >= active_threshold else None,
                   raw[i][2] if smooth[i] >= active_threshold else None) for i, t in enumerate(times)], fps, duration, coarse_cuts


def _runs(samples: list[Sample], cuts: list[float]) -> list[tuple[int, int]]:
    active = [s.x is not None for s in samples]
    for i in range(1, len(active)-1):
        if not active[i] and active[i-1] and active[i+1]: active[i] = True
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, on in enumerate(active+[False]):
        if on and start is None: start = i
        if not on and start is not None:
            if samples[i-1].t-samples[start].t >= 1: runs.append((start, i-1))
            start = None
    split: list[tuple[int, int]] = []
    for a, b in runs:
        while samples[b].t-samples[a].t > MAX_SECONDS:
            target = samples[a].t+MAX_SECONDS
            percentile = np.percentile([s.area for s in samples[a:b+1]], 25)
            legal = [i for i in range(a+4, b-4) if MIN_SECONDS <= samples[i].t-samples[a].t <= MAX_SECONDS and
                     (any(abs(samples[i].t-c) <= .5 for c in cuts) or samples[i].area <= percentile)]
            cut = min(legal, key=lambda i: abs(samples[i].t-target)) if legal else min(b-1, a+round(MAX_SECONDS*SAMPLE_FPS))
            split.append((a, cut)); a = cut+1
        split.append((a, b))
    return split


def _segment(samples: list[Sample], a: int, b: int, duration: float, cuts: list[float], index: int) -> dict:
    points = [(s.x, s.y) for s in samples[a:b+1] if s.x is not None and s.y is not None]
    if not points: raise ValueError("segment has no foreground points")
    n = max(1, min(4, len(points)//3 or 1))
    first = (float(np.median([p[0] for p in points[:n]])), float(np.median([p[1] for p in points[:n]])))
    last = (float(np.median([p[0] for p in points[-n:]])), float(np.median([p[1] for p in points[-n:]])))
    dx, dy = last[0]-first[0], last[1]-first[1]
    enter, exit = _edge(*first), _edge(*last)
    horizontal = abs(dx) >= .16 and abs(dx) >= abs(dy)*1.25
    if not horizontal:
        enter = enter if enter in ("left", "right") and a > 0 else "none"
        exit = exit if exit in ("left", "right") and b < len(samples)-1 else "none"
    active_start, active_end = samples[a].t, min(duration, samples[b].t+1/SAMPLE_FPS)
    start = max(0, active_start-min(1.5, active_start-(samples[a-1].t if a else 0)))
    end = min(duration, active_end+min(1.5, (samples[b+1].t if b+1 < len(samples) else duration)-active_end))
    displacement = float(np.hypot(dx, dy))
    edge_score = 1.0 if enter in ("left", "right") or exit in ("left", "right") else .3
    confidence = min(1.0, .42*min(1.0, displacement/.35)+.38*edge_score+.2*min(1.0, len(points)/20))
    confidence *= 1 if horizontal else .72
    return {"index": index, "start_ms": round(start*1000), "end_ms": round(end*1000),
            "active_start_ms": round(active_start*1000), "active_end_ms": round(active_end*1000),
            "lead_blank_ms": round((active_start-start)*1000), "tail_blank_ms": round((end-active_end)*1000),
            "enter_edge": enter, "exit_edge": exit, "dx": round(dx, 4), "dy": round(dy, 4),
            "confidence": round(confidence, 3), "approval": "auto" if confidence >= .78 and horizontal else "review",
            "mirror_safe": True, "hard_cuts_ms": [round(c*1000) for c in cuts if start <= c <= end], "samples": b-a+1}


def analyze_video(path: Path, content_rotation: int = 0, samples: int | None = None) -> dict:
    del samples
    frames, fps, duration, coarse_cuts = _decode_samples(path, content_rotation)
    # Long compilation reels can contain hundreds of visual effects and are
    # already bounded accurately by the activity pass. Run the heavier
    # frame-accurate detector only on short ambiguous clips; retain coarse cut
    # candidates for longer material and review them in the timeline UI.
    cuts = (_hard_cuts(path) or coarse_cuts) if coarse_cuts and duration <= 15 else coarse_cuts
    segments = [_segment(frames, a, b, duration, cuts, i) for i, (a, b) in enumerate(_runs(frames, cuts))]
    approved = [s for s in segments if s["approval"] == "auto"]
    best = max(segments, key=lambda s: s["confidence"], default=None)
    travel = "stationary"
    if best:
        if abs(best["dx"]) > .12 and abs(best["dx"]) >= abs(best["dy"])*1.25:
            travel = "left-right" if best["dx"] > 0 else "right-left"
        elif abs(best["dy"]) > .12 and abs(best["dy"]) > abs(best["dx"])*1.25:
            travel = "top-bottom" if best["dy"] > 0 else "bottom-top"
    return {"version": ANALYZER_VERSION, "status": "ready" if best and best["confidence"] >= .55 else "review", "source_fps": round(fps, 3),
            "duration": round(duration, 3), "content_rotation": content_rotation,
            "hard_cuts_ms": [round(c*1000) for c in cuts], "segments": segments,
            "segment_count": len(segments), "auto_approved": len(approved),
            "enter_edge": best["enter_edge"] if best else "none", "exit_edge": best["exit_edge"] if best else "none",
            "travel": travel,
            "confidence": best["confidence"] if best else 0.0}
