"""World / surface / projector geometry.

Coordinate systems:
- World: inches, x right, y down. Surfaces are axis-aligned rects in world space.
- Surface-normalized: 0..1 across a surface (0,0 = top-left as seen by the viewer).
- Output-normalized: 0..1 across the projector's output frame.

A device's corner pin lists the output-normalized positions of the surface's
TL, TR, BR, BL corners. The player inverts that homography per pixel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


UNIT_SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def homography(src: Sequence[Sequence[float]], dst: Sequence[Sequence[float]]) -> np.ndarray:
    """Solve the 3x3 homography mapping src points to dst points (>= 4 pairs, DLT)."""
    if len(src) != len(dst) or len(src) < 4:
        raise ValueError("need at least 4 point pairs")
    rows = []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([-x, -y, -1, 0, 0, 0, x * u, y * u, u])
        rows.append([0, 0, 0, -x, -y, -1, x * v, y * v, v])
    _, _, vt = np.linalg.svd(np.asarray(rows, dtype=float))
    h = vt[-1].reshape(3, 3)
    return h / h[2, 2]


def apply_h(h: np.ndarray, pts: Iterable[Sequence[float]]) -> list[tuple[float, float]]:
    out = []
    for x, y in pts:
        p = h @ np.array([x, y, 1.0])
        out.append((float(p[0] / p[2]), float(p[1] / p[2])))
    return out


def corner_pin_homography(corners: Sequence[Sequence[float]]) -> np.ndarray:
    """Surface-normalized -> output-normalized."""
    return homography(UNIT_SQUARE, corners)


def order_corners(pts: Sequence[Sequence[float]]) -> list[list[float]]:
    """Sort 4 arbitrary points into TL, TR, BR, BL (image coords, y down)."""
    p = np.asarray(pts, dtype=float)
    s = p.sum(axis=1)
    d = p[:, 0] - p[:, 1]
    tl = p[np.argmin(s)]
    br = p[np.argmax(s)]
    tr = p[np.argmax(d)]
    bl = p[np.argmin(d)]
    return [tl.tolist(), tr.tolist(), br.tolist(), bl.tolist()]


def bounds(rects: Iterable[Rect]) -> Rect:
    rs = list(rects)
    if not rs:
        return Rect(0, 0, 1, 1)
    x0 = min(r.x for r in rs)
    y0 = min(r.y for r in rs)
    x1 = max(r.right for r in rs)
    y1 = max(r.bottom for r in rs)
    return Rect(x0, y0, max(x1 - x0, 1e-6), max(y1 - y0, 1e-6))


def gap_distance(a: Rect, b: Rect) -> float:
    """Shortest distance between two rects' edges in inches (0 if touching/overlapping)."""
    dx = max(b.x - a.right, a.x - b.right, 0.0)
    dy = max(b.y - a.bottom, a.y - b.bottom, 0.0)
    return math.hypot(dx, dy)


DIRECTION_KEYS = {
    "left-right": lambda r: (r.cx, r.cy),
    "right-left": lambda r: (-r.cx, r.cy),
    "top-bottom": lambda r: (r.cy, r.cx),
    "bottom-top": lambda r: (-r.cy, r.cx),
}


def order_by_direction(items: list, rect_of, direction: str | None) -> list:
    if not direction or direction == "list" or direction not in DIRECTION_KEYS:
        return list(items)
    key = DIRECTION_KEYS[direction]
    return sorted(items, key=lambda it: key(rect_of(it)))


def fit_rect(video_aspect: float, surface: Rect, fit: str) -> tuple[float, float, float, float]:
    """Where a video sits inside a surface, in surface-normalized coords."""
    if fit == "stretch" or video_aspect <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    surf_aspect = surface.w / surface.h
    if (fit == "contain") == (video_aspect > surf_aspect):
        w, h = 1.0, surf_aspect / video_aspect
    else:
        w, h = video_aspect / surf_aspect, 1.0
    return ((1 - w) / 2, (1 - h) / 2, w, h)


def world_to_surface_rect(r: Rect, surface: Rect) -> tuple[float, float, float, float]:
    return (
        (r.x - surface.x) / surface.w,
        (r.y - surface.y) / surface.h,
        r.w / surface.w,
        r.h / surface.h,
    )


def fit_world_rect(video_aspect: float, area: Rect, fit: str) -> Rect:
    nx, ny, nw, nh = fit_rect(video_aspect, area, fit)
    return Rect(area.x + nx * area.w, area.y + ny * area.h, nw * area.w, nh * area.h)


def _unit_dir(direction: str | None) -> tuple[float, float]:
    return {"left": (-1.0, 0.0), "right": (1.0, 0.0), "up": (0.0, -1.0), "down": (0.0, 1.0)}.get(
        direction or "right", (1.0, 0.0)
    )


def spatial_delays(rects: dict[str, Rect], ttype: str, direction: str | None, spread: float) -> dict[str, float]:
    """Per-surface delay (seconds) for spatial transitions; 0..spread across the layout."""
    if not rects or ttype not in ("wipe", "cascade", "ripple-out", "ripple-in"):
        return {k: 0.0 for k in rects}
    world = bounds(rects.values())
    if ttype in ("wipe", "cascade"):
        ux, uy = _unit_dir(direction)
        proj = {k: r.cx * ux + r.cy * uy for k, r in rects.items()}
    else:
        cx, cy = world.cx, world.cy
        dist = {k: math.hypot(r.cx - cx, r.cy - cy) for k, r in rects.items()}
        maxd = max(dist.values()) or 1.0
        proj = {k: (d if ttype == "ripple-out" else maxd - d) for k, d in dist.items()}
    lo, hi = min(proj.values()), max(proj.values())
    span = hi - lo
    if span < 1e-9:
        return {k: 0.0 for k in rects}
    return {k: (v - lo) / span * spread for k, v in proj.items()}


def rasterize_mask(polygon: Sequence[Sequence[float]], feather: float, size: int = 512):
    """Feathered mask image (uint8, size x size) for a surface-normalized polygon.

    Empty polygon = full surface with feathered edges.
    """
    import cv2

    f = max(0.0, feather)
    k = max(1, int(f * size)) if f > 0 else 0
    pad = k * 2
    img = np.zeros((size + 2 * pad, size + 2 * pad), dtype=np.uint8)
    pts = polygon if len(polygon) >= 3 else UNIT_SQUARE
    poly = np.array([[pad + p[0] * (size - 1), pad + p[1] * (size - 1)] for p in pts], dtype=np.int32)
    cv2.fillPoly(img, [poly], 255)
    if k:
        # Erode by the feather width so the blur fades to 0 at the polygon edge.
        # The blur (sigma k/3) reaches ~3 sigma = k, matching the erosion of k per side.
        img = cv2.erode(img, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
        img = cv2.GaussianBlur(img, (0, 0), sigmaX=k / 3.0)
    return img[pad : pad + size, pad : pad + size]
