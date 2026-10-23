"""Camera-assisted calibration. Flow (geometry):

1. POST /api/calibration/geometry/start {device_id} -> player shows an ArUco board.
2. POST /api/calibration/{sid}/photo (multipart photo) -> markers + auto window corners.
3. POST /api/calibration/{sid}/apply {corners?} -> device corners written, outline guide shown.

Color: /color/start {device_ids} shows a patch board on one device at a time; upload a photo per
device to /color/{sid}/photo, then /color/{sid}/solve writes gains/gamma.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..calibration import patterns, solve
from ..config import CALIBRATION_DIR
from ..db import get_session
from ..models import CalibrationProfile, CalibrationSession, Device, now_ms
from ..sync.hub import hub
from ..sync.runner import runner

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


class GeometryStart(BaseModel):
    device_id: str


class ApplyIn(BaseModel):
    # Normalized photo coords (0..1), TL, TR, BR, BL. Omit to use auto-detected corners.
    corners: Optional[list[list[float]]] = None


class ColorStart(BaseModel):
    device_ids: list[str]


class ProfileIn(BaseModel):
    device_id: str
    name: str


CALIBRATION_FIELDS = (
    "surface_id", "output_width", "output_height", "mirror_h", "mirror_v", "corners", "mask", "feather",
    "brightness", "gamma", "gain_r", "gain_g", "gain_b",
)


def profile_snapshot(d: Device) -> dict:
    return {field: getattr(d, field) for field in CALIBRATION_FIELDS}


def mark_profiles_unsaved(session: Session, device_id: str) -> None:
    for profile in session.exec(select(CalibrationProfile).where(CalibrationProfile.device_id == device_id)).all():
        if profile.active:
            profile.active = False
            profile.updated_at = now_ms()
            session.add(profile)


def _activate_profile(session: Session, profile: CalibrationProfile, d: Device) -> None:
    for other in session.exec(select(CalibrationProfile).where(CalibrationProfile.device_id == d.id)).all():
        other.active = other.id == profile.id
        session.add(other)
    for field in CALIBRATION_FIELDS:
        if field in profile.config:
            setattr(d, field, profile.config[field])
    profile.active = True
    profile.updated_at = now_ms()
    session.add(profile)
    session.add(d)


def _url(path) -> str:
    return "/files/calibration/" + path.relative_to(CALIBRATION_DIR).as_posix()


def _session(session: Session, sid: str, kind: str) -> CalibrationSession:
    cs = session.get(CalibrationSession, sid)
    if not cs or cs.kind != kind:
        raise HTTPException(404, "calibration session not found")
    return cs


def _device(session: Session, device_id: str) -> Device:
    d = session.get(Device, device_id)
    if not d:
        raise HTTPException(404, "device not found")
    return d


# ------------------------------------------------------------- geometry


@router.post("/geometry/start")
async def geometry_start(body: GeometryStart, session: Session = Depends(get_session)) -> dict:
    d = _device(session, body.device_id)
    w, h = d.output_width or 1280, d.output_height or 720
    img, known = patterns.aruco_board(w, h)
    path = CALIBRATION_DIR / f"aruco_{w}x{h}.png"
    if not path.exists():
        patterns.save(img, path)
    cs = CalibrationSession(kind="geometry", device_ids=[d.id], data={"known": {str(k): v for k, v in known.items()}, "w": w, "h": h})
    session.add(cs)
    session.commit()
    if not await hub.guide(d.id, "image", {"url": _url(path)}):
        raise HTTPException(409, "device is offline")
    return {"session": cs.id, "device_id": d.id, "pattern_url": _url(path)}


@router.post("/{sid}/photo")
async def geometry_photo(sid: str, photo: UploadFile = File(...), session: Session = Depends(get_session)) -> dict:
    cs = _session(session, sid, "geometry")
    data = await photo.read()
    w, h = cs.data["w"], cs.data["h"]
    known = {int(k): v for k, v in cs.data["known"].items()}

    def work():
        img = solve.decode(data)
        found = solve.detect_markers(img)
        hmat, info = solve.projector_homography(found, known)
        window = solve.detect_window(img, hmat, w, h)
        vis = solve.overlay(img, found, hmat, w, h, window)
        out_dir = CALIBRATION_DIR / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_dir / "photo.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        cv2.imwrite(str(out_dir / "overlay.jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return img.shape[1], img.shape[0], hmat, info, window, out_dir

    pw, ph, hmat, info, window, out_dir = await asyncio.to_thread(work)
    norm_window = [[x / pw, y / ph] for x, y in window] if window else None
    cs.data = {
        **cs.data,
        "photo_w": pw,
        "photo_h": ph,
        "h_p2c": hmat.tolist() if hmat is not None else None,
        "window": norm_window,
        "info": info,
    }
    session.add(cs)
    session.commit()
    if hmat is None:
        msg = f"Found {info['markers_found']} of {info['markers_total']} markers; need at least 2. Get the whole projection in frame and avoid glare."
    else:
        msg = "Window detected; check the corners and apply." if norm_window else "Markers found, but no window edge detected. Tap the 4 corners."
    return {
        "ok": hmat is not None,
        "message": msg,
        "info": info,
        "photo_url": _url(out_dir / "photo.jpg"),
        "overlay_url": _url(out_dir / "overlay.jpg"),
        "photo_size": [pw, ph],
        "corners": norm_window,
    }


@router.post("/{sid}/apply")
async def geometry_apply(sid: str, body: ApplyIn, session: Session = Depends(get_session)) -> dict:
    cs = _session(session, sid, "geometry")
    if not cs.data.get("h_p2c"):
        raise HTTPException(400, "upload a photo with visible markers first")
    corners = body.corners or cs.data.get("window")
    if not corners or len(corners) != 4:
        raise HTTPException(400, "4 corners required")
    pw, ph = cs.data["photo_w"], cs.data["photo_h"]
    cam = [[x * pw, y * ph] for x, y in corners]
    hmat = np.array(cs.data["h_p2c"], dtype=np.float64)
    out = solve.camera_to_output(cam, hmat, cs.data["w"], cs.data["h"])
    # Corners come out in physical output space, so any rear-projection flip is already baked in.
    out = [[round(min(1.5, max(-0.5, x)), 5), round(min(1.5, max(-0.5, y)), 5)] for x, y in out]
    d = _device(session, cs.device_ids[0])
    d.corners = out
    d.mirror_h = False
    d.mirror_v = False
    mark_profiles_unsaved(session, d.id)
    session.add(d)
    cs.data = {**cs.data, "applied": out}
    session.add(cs)
    session.commit()
    await hub.push_config(d.id)
    hub.mark_devices_dirty()
    await hub.changed("calibration_profiles")
    await hub.guide(d.id, "outline", {})
    await runner.reload()
    return {"ok": True, "corners": out}


@router.post("/{sid}/finish")
async def geometry_finish(sid: str, session: Session = Depends(get_session)) -> dict:
    cs = _session(session, sid, "geometry")
    for dev in cs.device_ids:
        await hub.guide(dev, None)
    return {"ok": True}


# ---------------------------------------------------------------- color


def _show_color(cs: CalibrationSession, current: Optional[str]):
    async def go():
        for dev in cs.device_ids:
            if dev == current:
                await hub.guide(dev, "image", {"url": cs.data["boards"][dev]})
            else:
                await hub.guide(dev, "black", {})

    return go()


@router.post("/color/start")
async def color_start(body: ColorStart, session: Session = Depends(get_session)) -> dict:
    if len(body.device_ids) < 1:
        raise HTTPException(400, "pick at least one device")
    boards, layouts = {}, {}
    for did in body.device_ids:
        d = _device(session, did)
        w, h = d.output_width or 1280, d.output_height or 720
        img, layout = patterns.color_board(w, h)
        path = CALIBRATION_DIR / f"color_{w}x{h}.png"
        if not path.exists():
            patterns.save(img, path)
        boards[did] = _url(path)
        layouts[did] = {"markers": {str(k): v for k, v in layout["markers"].items()}, "patches": layout["patches"]}
    cs = CalibrationSession(kind="color", device_ids=body.device_ids, data={"boards": boards, "layouts": layouts, "measured": {}})
    session.add(cs)
    session.commit()
    await _show_color(cs, body.device_ids[0])
    return {"session": cs.id, "current": body.device_ids[0]}


@router.post("/color/{sid}/show")
async def color_show(sid: str, device_id: str = Form(...), session: Session = Depends(get_session)) -> dict:
    cs = _session(session, sid, "color")
    if device_id not in cs.device_ids:
        raise HTTPException(400, "device not in session")
    await _show_color(cs, device_id)
    return {"ok": True}


@router.post("/color/{sid}/photo")
async def color_photo(sid: str, device_id: str = Form(...), photo: UploadFile = File(...), session: Session = Depends(get_session)) -> dict:
    cs = _session(session, sid, "color")
    if device_id not in cs.device_ids:
        raise HTTPException(400, "device not in session")
    data = await photo.read()
    layout = cs.data["layouts"][device_id]

    def work():
        return solve.measure_color_board(solve.decode(data), layout)

    measured = await asyncio.to_thread(work)
    if measured is None:
        return {"ok": False, "message": "Couldn't find the board's corner markers. Frame the whole window and keep exposure locked."}
    all_measured = {**cs.data["measured"], device_id: measured}
    cs.data = {**cs.data, "measured": all_measured}
    session.add(cs)
    session.commit()
    remaining = [d for d in cs.device_ids if d not in all_measured]
    nxt = remaining[0] if remaining else None
    await _show_color(cs, nxt)
    return {"ok": True, "next": nxt, "white": measured["white"], "measured_gamma": round(solve.fit_gamma(measured), 3)}


@router.post("/color/{sid}/solve")
async def color_solve(sid: str, session: Session = Depends(get_session)) -> dict:
    cs = _session(session, sid, "color")
    measured = cs.data.get("measured", {})
    if not measured:
        raise HTTPException(400, "no photos measured yet")
    result = solve.solve_color(measured)
    for did, vals in result.items():
        d = session.get(Device, did)
        if not d:
            continue
        d.gain_r, d.gain_g, d.gain_b = vals["gain_r"], vals["gain_g"], vals["gain_b"]
        d.gamma = vals["gamma"]
        d.brightness = vals["brightness"]
        mark_profiles_unsaved(session, d.id)
        session.add(d)
    cs.data = {**cs.data, "result": result}
    session.add(cs)
    session.commit()
    for did in cs.device_ids:
        await hub.push_config(did)
        await hub.guide(did, "colorbars", {})
    hub.mark_devices_dirty()
    await hub.changed("calibration_profiles")
    return {"ok": True, "result": result}


@router.post("/color/{sid}/finish")
async def color_finish(sid: str, session: Session = Depends(get_session)) -> dict:
    cs = _session(session, sid, "color")
    for dev in cs.device_ids:
        await hub.guide(dev, None)
    return {"ok": True}


# ------------------------------------------------------------- named profiles


@router.get("/profiles")
def list_profiles(device_id: str, session: Session = Depends(get_session)) -> list[CalibrationProfile]:
    _device(session, device_id)
    return list(session.exec(select(CalibrationProfile).where(CalibrationProfile.device_id == device_id)
                             .order_by(CalibrationProfile.name)).all())


@router.post("/profiles")
async def create_profile(body: ProfileIn, session: Session = Depends(get_session)) -> CalibrationProfile:
    d = _device(session, body.device_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "profile name required")
    if session.exec(select(CalibrationProfile).where(CalibrationProfile.device_id == d.id,
                    CalibrationProfile.name == name)).first():
        raise HTTPException(409, "a profile with that name already exists")
    profile = CalibrationProfile(device_id=d.id, name=name, config=profile_snapshot(d), active=True)
    session.add(profile)
    session.flush()
    _activate_profile(session, profile, d)
    session.commit()
    session.refresh(profile)
    await hub.changed("calibration_profiles")
    return profile


@router.post("/profiles/{profile_id}/save")
async def save_profile(profile_id: str, session: Session = Depends(get_session)) -> CalibrationProfile:
    profile = session.get(CalibrationProfile, profile_id)
    if not profile:
        raise HTTPException(404, "calibration profile not found")
    d = _device(session, profile.device_id)
    profile.config = profile_snapshot(d)
    profile.updated_at = now_ms()
    _activate_profile(session, profile, d)
    session.commit()
    session.refresh(profile)
    await hub.changed("calibration_profiles")
    return profile


@router.post("/profiles/{profile_id}/activate")
async def activate_profile(profile_id: str, session: Session = Depends(get_session)) -> CalibrationProfile:
    profile = session.get(CalibrationProfile, profile_id)
    if not profile:
        raise HTTPException(404, "calibration profile not found")
    d = _device(session, profile.device_id)
    _activate_profile(session, profile, d)
    session.commit()
    session.refresh(profile)
    await hub.push_config(d.id)
    hub.mark_devices_dirty()
    await hub.changed("calibration_profiles")
    await runner.reload()
    return profile
