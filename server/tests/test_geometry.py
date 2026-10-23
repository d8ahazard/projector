import math

import numpy as np
import pytest

from app import geometry as geo
from app.geometry import Rect


def test_homography_identity():
    h = geo.homography(geo.UNIT_SQUARE, geo.UNIT_SQUARE)
    assert np.allclose(h, np.eye(3), atol=1e-9)


def test_corner_pin_maps_corners_and_center():
    corners = [[0.1, 0.2], [0.9, 0.1], [0.8, 0.9], [0.2, 0.8]]
    h = geo.corner_pin_homography(corners)
    mapped = geo.apply_h(h, geo.UNIT_SQUARE)
    for (x, y), (u, v) in zip(mapped, corners):
        assert x == pytest.approx(u, abs=1e-9)
        assert y == pytest.approx(v, abs=1e-9)
    inv = np.linalg.inv(h)
    back = geo.apply_h(inv, corners)
    for (x, y), (u, v) in zip(back, geo.UNIT_SQUARE):
        assert x == pytest.approx(u, abs=1e-9)
        assert y == pytest.approx(v, abs=1e-9)


def test_order_corners():
    pts = [[10, 10], [0, 10], [0, 0], [10, 0]]
    assert geo.order_corners(pts) == [[0, 0], [10, 0], [10, 10], [0, 10]]


def test_gap_distance():
    a = Rect(0, 0, 48, 72)
    b = Rect(60, 0, 48, 72)
    assert geo.gap_distance(a, b) == pytest.approx(12)
    assert geo.gap_distance(b, a) == pytest.approx(12)
    c = Rect(60, 80, 10, 10)
    assert geo.gap_distance(a, c) == pytest.approx(math.hypot(12, 8))
    assert geo.gap_distance(a, Rect(10, 10, 5, 5)) == 0


def test_fit_rect_contain_and_cover():
    portrait = Rect(0, 0, 48, 72)
    x, y, w, h = geo.fit_rect(16 / 9, portrait, "contain")
    assert w == pytest.approx(1.0)
    assert h == pytest.approx((48 / 72) / (16 / 9))
    assert y == pytest.approx((1 - h) / 2)
    x, y, w, h = geo.fit_rect(16 / 9, portrait, "cover")
    assert h == pytest.approx(1.0)
    assert w > 1.0
    assert geo.fit_rect(16 / 9, portrait, "stretch") == (0, 0, 1, 1)


def test_world_to_surface_rect():
    s = Rect(100, 0, 50, 100)
    assert geo.world_to_surface_rect(Rect(100, 0, 50, 100), s) == (0, 0, 1, 1)
    assert geo.world_to_surface_rect(Rect(0, 0, 200, 100), s) == (-2, 0, 4, 1)


def test_spatial_delays_wipe_and_ripple():
    rects = {"a": Rect(0, 0, 10, 10), "b": Rect(50, 0, 10, 10), "c": Rect(100, 0, 10, 10)}
    d = geo.spatial_delays(rects, "wipe", "right", 2.0)
    assert d == {"a": 0.0, "b": pytest.approx(1.0), "c": pytest.approx(2.0)}
    d = geo.spatial_delays(rects, "wipe", "left", 2.0)
    assert d["c"] == 0.0 and d["a"] == pytest.approx(2.0)
    d = geo.spatial_delays(rects, "ripple-out", None, 1.0)
    assert d["b"] == 0.0 and d["a"] == pytest.approx(1.0) and d["c"] == pytest.approx(1.0)
    d = geo.spatial_delays(rects, "ripple-in", None, 1.0)
    assert d["b"] == pytest.approx(1.0) and d["a"] == 0.0
    assert geo.spatial_delays(rects, "dip", None, 1.0) == {"a": 0.0, "b": 0.0, "c": 0.0}


def test_order_by_direction():
    items = [Rect(100, 0, 1, 1), Rect(0, 0, 1, 1), Rect(50, 0, 1, 1)]
    out = geo.order_by_direction(items, lambda r: r, "left-right")
    assert [r.x for r in out] == [0, 50, 100]
    out = geo.order_by_direction(items, lambda r: r, "right-left")
    assert [r.x for r in out] == [100, 50, 0]
    assert geo.order_by_direction(items, lambda r: r, "list") == items


def test_rasterize_mask_feathers_edges():
    img = geo.rasterize_mask([], 0.05, size=128)
    assert img.shape == (128, 128)
    assert img[64, 64] == 255
    assert img[0, 64] < 40
    tri = geo.rasterize_mask([[0, 0], [1, 0], [0, 1]], 0.0, size=64)
    assert tri[5, 5] == 255 and tri[60, 60] == 0
