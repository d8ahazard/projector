"""How well a clip suits window projection: black backgrounds read as transparent."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def analyze(path: Path, samples: int = 24) -> dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {}
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    idxs = np.linspace(0, max(total - 1, 0), num=min(samples, max(total, 1))).astype(int) if total else range(samples)
    black, luma, motion, edges = [], [], [], []
    prev = None
    for i in idxs:
        if total:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, frame = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        black.append(float((g < 24).mean()))
        luma.append(float(g.mean() / 255.0))
        border = np.concatenate((g[:4].flatten(), g[-4:].flatten(), g[:, :4].flatten(), g[:, -4:].flatten()))
        edges.append(float((border > 48).mean()))
        if prev is not None:
            motion.append(float(np.abs(g - prev).mean() / 255.0))
        prev = g
    cap.release()
    if not black:
        return {}
    b = float(np.mean(black))
    m = float(np.mean(motion)) if motion else 0.0
    lum = float(np.mean(luma))
    score = 0.7 * b + 0.3 * min(1.0, m * 8)
    edge_ratio = float(np.mean(edges))
    return {"black_ratio": round(b, 3), "mean_luma": round(lum, 3), "motion": round(m, 4), "score": round(score, 3),
            "edge_brightness": round(edge_ratio, 3), "framing_warning": edge_ratio > 0.15}
