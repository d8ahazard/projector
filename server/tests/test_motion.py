import cv2
import numpy as np

from app.media.motion import analyze_video


def _moving_box(path, reverse=False):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 15, (320, 180))
    assert writer.isOpened()
    for i in range(45):
        frame = np.zeros((180, 320, 3), np.uint8)
        x = round(-35 + i * 390 / 44)
        if reverse: x = 285 - (x + 35)
        cv2.rectangle(frame, (x, 50), (x + 70, 145), (230, 230, 230), -1)
        writer.write(frame)
    writer.release()


def test_motion_analyzer_finds_horizontal_crossing(tmp_path):
    path = tmp_path / "cross.mp4"
    _moving_box(path)
    result = analyze_video(path)
    assert result["status"] == "ready"
    assert result["travel"] == "left-right"
    assert result["enter_edge"] == "left"
    assert result["exit_edge"] == "right"
    assert result["confidence"] >= 0.55


def test_motion_analyzer_respects_content_rotation(tmp_path):
    path = tmp_path / "cross.mp4"
    _moving_box(path)
    result = analyze_video(path, 90)
    assert result["travel"] == "top-bottom"


def test_motion_analyzer_splits_appearances_at_clean_background_gap(tmp_path):
    path = tmp_path / "two-appearances.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 15, (320, 180))
    assert writer.isOpened()
    for i in range(150):
        frame = np.zeros((180, 320, 3), np.uint8)
        if 15 <= i < 60:
            x = round(-30 + (i-15) * 380 / 44)
            cv2.rectangle(frame, (x, 45), (x+60, 145), (230,230,230), -1)
        if 90 <= i < 135:
            x = round(290 - (i-90) * 380 / 44)
            cv2.rectangle(frame, (x, 45), (x+60, 145), (230,230,230), -1)
        writer.write(frame)
    writer.release()
    result = analyze_video(path)
    assert len(result["segments"]) == 2
    assert result["segments"][0]["dx"] > .4
    assert result["segments"][1]["dx"] < -.4
    assert result["segments"][0]["end_ms"] < result["segments"][1]["start_ms"]
