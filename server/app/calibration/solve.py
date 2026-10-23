"""Camera-based calibration: projector->camera homography, window detection, color matching."""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from ..geometry import order_corners
from .patterns import GRAY_LEVELS, _dictionary


def _detector():
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.adaptiveThreshWinSizeMax = 53
    return cv2.aruco.ArucoDetector(_dictionary(), params)


def decode(data: bytes, max_side: int = 2400) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode image")
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    if s < 1:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img


def detect_markers(img: np.ndarray) -> dict[int, np.ndarray]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = _detector().detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2) for i, c in zip(ids.flatten(), corners)}


def projector_homography(found: dict[int, np.ndarray], known: dict[int, list]) -> tuple[Optional[np.ndarray], dict]:
    """Output px -> camera px. Needs >= 2 markers (8 points)."""
    src, dst = [], []
    for mid, cam in found.items():
        if mid in known:
            src.extend(known[mid])
            dst.extend(cam.tolist())
    info = {"markers_found": len([m for m in found if m in known]), "markers_total": len(known)}
    if len(src) < 8:
        return None, info
    h, mask = cv2.findHomography(np.float32(src), np.float32(dst), cv2.RANSAC, 4.0)
    if h is None:
        return None, info
    proj = cv2.perspectiveTransform(np.float32(src).reshape(-1, 1, 2), h).reshape(-1, 2)
    err = float(np.sqrt(((proj - np.float32(dst)) ** 2).sum(axis=1)).mean())
    info.update(inliers=int(mask.sum()), reprojection_px=round(err, 2))
    return h, info


def detect_window(img: np.ndarray, h_p2c: Optional[np.ndarray], out_w: int, out_h: int) -> Optional[list[list[float]]]:
    """Find the lit screen fabric (largest bright quad) in camera px."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = max(5, int(min(img.shape[:2]) * 0.03)) | 1
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    if h_p2c is not None:
        frame = cv2.perspectiveTransform(np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]]).reshape(-1, 1, 2), h_p2c)
        lim = np.zeros_like(th)
        cv2.fillPoly(lim, [frame.reshape(-1, 2).astype(np.int32)], 255)
        th = cv2.bitwise_and(th, lim)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < img.shape[0] * img.shape[1] * 0.01:
        return None
    peri = cv2.arcLength(c, True)
    for eps in (0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(c, eps * peri, True)
        if len(approx) == 4:
            return order_corners(approx.reshape(4, 2).tolist())
    box = cv2.boxPoints(cv2.minAreaRect(c))
    return order_corners(box.tolist())


def camera_to_output(corners_cam: list[list[float]], h_p2c: np.ndarray, out_w: int, out_h: int) -> list[list[float]]:
    inv = np.linalg.inv(h_p2c)
    pts = cv2.perspectiveTransform(np.float32(corners_cam).reshape(-1, 1, 2), inv).reshape(-1, 2)
    return [[float(x) / out_w, float(y) / out_h] for x, y in pts]


def overlay(img: np.ndarray, found: dict[int, np.ndarray], h_p2c: Optional[np.ndarray], out_w: int, out_h: int, window: Optional[list]) -> np.ndarray:
    vis = img.copy()
    for c in found.values():
        cv2.polylines(vis, [c.astype(np.int32)], True, (255, 215, 122), 2)
    if h_p2c is not None:
        frame = cv2.perspectiveTransform(np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]]).reshape(-1, 1, 2), h_p2c)
        cv2.polylines(vis, [frame.reshape(-1, 2).astype(np.int32)], True, (255, 200, 0), 2)
    if window:
        cv2.polylines(vis, [np.int32(window)], True, (26, 122, 255), 4)
        for i, (x, y) in enumerate(window):
            cv2.circle(vis, (int(x), int(y)), 10, (26, 122, 255), -1)
            cv2.putText(vis, ["TL", "TR", "BR", "BL"][i], (int(x) + 12, int(y) - 8), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (26, 122, 255), 2)
    return vis


# ---------------------------------------------------------------- color


def _linear(v: np.ndarray) -> np.ndarray:
    return np.power(np.clip(v / 255.0, 0, 1), 2.2)


def measure_color_board(img: np.ndarray, layout: dict) -> Optional[dict]:
    """Samples each patch (median of its inner region). Returns linear RGB per patch."""
    found = detect_markers(img)
    src, dst = [], []
    for mid_s, pts in layout["markers"].items():
        mid = int(mid_s)
        if mid in found:
            src.extend(pts)
            dst.extend(found[mid].tolist())
    if len(src) < 12:
        return None
    h, _ = cv2.findHomography(np.float32(src), np.float32(dst), cv2.RANSAC, 4.0)
    if h is None:
        return None
    out = {}
    for name, (x0, y0, x1, y1) in layout["patches"].items():
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hw, hh = (x1 - x0) * 0.25, (y1 - y0) * 0.25
        quad = np.float32([[cx - hw, cy - hh], [cx + hw, cy - hh], [cx + hw, cy + hh], [cx - hw, cy + hh]])
        cam = cv2.perspectiveTransform(quad.reshape(-1, 1, 2), h).reshape(-1, 2)
        mask = np.zeros(img.shape[:2], np.uint8)
        cv2.fillPoly(mask, [cam.astype(np.int32)], 255)
        px = img[mask > 0]
        if len(px) == 0:
            return None
        bgr = np.median(px, axis=0)
        out[name] = _linear(bgr[::-1]).tolist()  # RGB
    return out


def fit_gamma(measured: dict) -> float:
    """Exponent of the projector's response from the gray ramp (relative to its white)."""
    white = np.mean(measured["gray255"])
    xs, ys = [], []
    for v in GRAY_LEVELS:
        if v < 64 or v == 255:
            continue
        y = np.mean(measured[f"gray{v}"]) - np.mean(measured["gray0"])
        if y <= 0:
            continue
        xs.append(np.log(v / 255.0))
        ys.append(np.log(y / max(white - np.mean(measured["gray0"]), 1e-6)))
    if len(xs) < 2:
        return 2.2
    slope = float(np.polyfit(xs, ys, 1)[0])
    return float(np.clip(slope, 1.0, 3.5))


def solve_color(measurements: dict[str, dict]) -> dict[str, dict]:
    """Per-device gains/gamma so all projectors match the dimmest white and the average gamma."""
    whites = {d: np.array(m["white"]) for d, m in measurements.items()}
    target_white = np.min(np.stack(list(whites.values())), axis=0)
    gammas = {d: fit_gamma(m) for d, m in measurements.items()}
    target_gamma = float(np.mean(list(gammas.values())))
    out = {}
    for d, w in whites.items():
        # Shader output is (c*gain)^g and the projector adds its own exponent, so after
        # correction the end-to-end exponent is target_gamma.
        ratio = np.clip(target_white / np.maximum(w, 1e-6), 0.05, 1.0)
        gain = np.power(ratio, 1.0 / target_gamma)
        out[d] = {
            "gain_r": round(float(gain[0]), 3),
            "gain_g": round(float(gain[1]), 3),
            "gain_b": round(float(gain[2]), 3),
            "gamma": round(float(np.clip(target_gamma / gammas[d], 0.5, 2.0)), 3),
            "brightness": 1.0,
            "measured_gamma": round(gammas[d], 3),
        }
    return out
