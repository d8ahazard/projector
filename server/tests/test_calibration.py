import cv2
import numpy as np
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.calibration import patterns, solve
from app.api import calibration, devices
from app.models import Device

W, H = 1280, 720


def _camera_view(board: np.ndarray, h: np.ndarray, size=(1600, 1200)) -> np.ndarray:
    return cv2.warpPerspective(board, h, size, borderValue=(20, 20, 20))


def _known_h() -> np.ndarray:
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[210, 180], [1390, 240], [1350, 980], [250, 940]])
    return cv2.getPerspectiveTransform(src, dst)


def test_homography_recovered_from_aruco_board():
    board, known = patterns.aruco_board(W, H)
    h_true = _known_h()
    cam = _camera_view(board, h_true)
    found = solve.detect_markers(cam)
    assert len(found) >= 12
    h, info = solve.projector_homography(found, known)
    assert h is not None
    assert info["reprojection_px"] < 2.0
    pts = np.float32([[100, 100], [640, 360], [1200, 650]]).reshape(-1, 1, 2)
    a = cv2.perspectiveTransform(pts, h)
    b = cv2.perspectiveTransform(pts, h_true)
    assert np.abs(a - b).max() < 2.0


def test_camera_to_output_round_trip():
    h = _known_h()
    out_pts = [[0.1, 0.2], [0.9, 0.15], [0.85, 0.9], [0.12, 0.8]]
    cam = cv2.perspectiveTransform(np.float32([[x * W, y * H] for x, y in out_pts]).reshape(-1, 1, 2), h).reshape(-1, 2).tolist()
    back = solve.camera_to_output(cam, h, W, H)
    assert np.allclose(back, out_pts, atol=1e-4)


def test_detect_window_finds_bright_quad():
    img = np.full((900, 1200, 3), 15, np.uint8)
    quad = np.int32([[300, 200], [900, 230], [880, 700], [320, 680]])
    cv2.fillPoly(img, [quad], (230, 230, 230))
    corners = solve.detect_window(img, None, W, H)
    assert corners is not None
    assert np.abs(np.float32(corners) - quad).max() < 12


def test_color_board_measure_and_solve():
    board, layout = patterns.color_board(W, H)
    h = _known_h()
    lay = {"markers": {str(k): v for k, v in layout["markers"].items()}, "patches": layout["patches"]}
    bright = solve.measure_color_board(_camera_view(board, h), lay)
    assert bright is not None
    dim = solve.measure_color_board(_camera_view((board * 0.7).astype(np.uint8), h), lay)
    assert dim is not None
    result = solve.solve_color({"a": bright, "b": dim})
    assert result["a"]["gain_r"] < 1.0
    assert result["b"]["gain_r"] == 1.0


def test_named_calibration_profiles_preserve_and_restore_placements(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Device(id="stick", name="P1")); db.commit()
        app = FastAPI(); app.include_router(calibration.router); app.include_router(devices.router)
        app.dependency_overrides[calibration.get_session] = lambda: db
        app.dependency_overrides[devices.get_session] = lambda: db
        monkeypatch.setattr(calibration.hub, "changed", AsyncMock())
        monkeypatch.setattr(calibration.hub, "push_config", AsyncMock())
        monkeypatch.setattr(calibration.hub, "mark_devices_dirty", lambda: None)
        monkeypatch.setattr(devices.hub, "push_config", AsyncMock())
        monkeypatch.setattr(devices.hub, "mark_devices_dirty", lambda: None)
        with TestClient(app) as client:
            wall = client.post("/api/calibration/profiles", json={"device_id": "stick", "name": "Wall test"})
            assert wall.status_code == 200 and wall.json()["active"] is True
            skewed = [[0.1, 0.2], [0.9, 0.1], [0.85, 0.9], [0.15, 0.85]]
            assert client.patch("/api/devices/stick", json={"corners": skewed}).status_code == 200
            profiles = client.get("/api/calibration/profiles?device_id=stick").json()
            assert profiles[0]["active"] is False
            window = client.post("/api/calibration/profiles", json={"device_id": "stick", "name": "Front window"})
            assert window.status_code == 200 and window.json()["config"]["corners"] == skewed
            restored = client.post(f"/api/calibration/profiles/{wall.json()['id']}/activate")
            assert restored.status_code == 200
            db.refresh(db.get(Device, "stick"))
            assert db.get(Device, "stick").corners == [[0, 0], [1, 0], [1, 1], [0, 1]]
    engine.dispose()
