"""Calibration images shown full-frame by a player (in physical output pixels)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

DICT = cv2.aruco.DICT_4X4_50


def _dictionary():
    return cv2.aruco.getPredefinedDictionary(DICT)


def aruco_board(width: int, height: int, cols: int = 6, rows: int = 4) -> tuple[np.ndarray, dict[int, list[list[float]]]]:
    """White frame with a grid of markers. Returns (BGR image, {id: 4 corners in output px})."""
    img = np.full((height, width, 3), 255, np.uint8)
    d = _dictionary()
    cell_w = width / cols
    cell_h = height / rows
    size = int(min(cell_w, cell_h) * 0.62)
    known: dict[int, list[list[float]]] = {}
    mid = 0
    for r in range(rows):
        for c in range(cols):
            marker = cv2.aruco.generateImageMarker(d, mid, size)
            x = int(c * cell_w + (cell_w - size) / 2)
            y = int(r * cell_h + (cell_h - size) / 2)
            img[y : y + size, x : x + size] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
            known[mid] = [[x, y], [x + size, y], [x + size, y + size], [x, y + size]]
            mid += 1
    return img, known


GRAY_LEVELS = [0, 32, 64, 96, 128, 160, 192, 224, 255]
COLOR_PATCHES = {"red": (0, 0, 255), "green": (0, 255, 0), "blue": (255, 0, 0), "white": (255, 255, 255)}


def color_board(width: int, height: int) -> tuple[np.ndarray, dict]:
    """Gray ramp + RGBW patches with 4 corner markers (ids 40-43) to locate the board."""
    img = np.zeros((height, width, 3), np.uint8)
    d = _dictionary()
    msize = int(min(width, height) * 0.16)
    margin = int(msize * 0.25)
    markers = {}
    positions = [(margin, margin), (width - margin - msize, margin), (width - margin - msize, height - margin - msize), (margin, height - margin - msize)]
    for i, (x, y) in enumerate(positions):
        mid = 40 + i
        # Markers need a white quiet zone on the black background.
        q = int(msize * 0.12)
        img[y - q : y + msize + q, x - q : x + msize + q] = 255
        img[y : y + msize, x : x + msize] = cv2.cvtColor(cv2.aruco.generateImageMarker(d, mid, msize), cv2.COLOR_GRAY2BGR)
        markers[mid] = [[x, y], [x + msize, y], [x + msize, y + msize], [x, y + msize]]

    left = margin * 2 + msize
    right = width - left
    top = int(height * 0.12)
    ramp_h = int(height * 0.36)
    patches = {}
    n = len(GRAY_LEVELS)
    pw = (right - left) / n
    for i, v in enumerate(GRAY_LEVELS):
        x0, x1 = int(left + i * pw), int(left + (i + 1) * pw)
        img[top : top + ramp_h, x0:x1] = v
        patches[f"gray{v}"] = [x0, top, x1, top + ramp_h]
    ctop = top + ramp_h + int(height * 0.06)
    ch = int(height * 0.3)
    cw = (right - left) / len(COLOR_PATCHES)
    for i, (name, bgr) in enumerate(COLOR_PATCHES.items()):
        x0, x1 = int(left + i * cw), int(left + (i + 1) * cw)
        img[ctop : ctop + ch, x0:x1] = bgr
        patches[name] = [x0, ctop, x1, ctop + ch]
    return img, {"markers": markers, "patches": patches}


def save(img: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
