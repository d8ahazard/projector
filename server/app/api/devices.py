from typing import Any, Optional

import cv2
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..geometry import rasterize_mask
from ..models import CalibrationProfile, Device, new_id, now_ms
from ..sync.hub import hub
from ..sync.runner import runner

router = APIRouter(prefix="/api/devices", tags=["devices"])

GUIDE_PATTERNS = {"grid", "outline", "crosshair", "colorbars", "gray", "white", "black", "identify", "image"}


class DeviceIn(BaseModel):
    name: Optional[str] = None
    surface_id: Optional[str] = None
    unassign: Optional[bool] = None
    mirror_h: Optional[bool] = None
    mirror_v: Optional[bool] = None
    corners: Optional[list[list[float]]] = None
    mask: Optional[list[list[float]]] = None
    feather: Optional[float] = None
    brightness: Optional[float] = None
    gamma: Optional[float] = None
    gain_r: Optional[float] = None
    gain_g: Optional[float] = None
    gain_b: Optional[float] = None
    volume: Optional[float] = None
    audio: Optional[bool] = None
    cache_budget_mb: Optional[int] = None
    output_width: Optional[int] = None
    output_height: Optional[int] = None


class GuideIn(BaseModel):
    pattern: Optional[str] = None
    params: dict[str, Any] = {}


class CommandIn(BaseModel):
    action: str


@router.post("/{device_id}/command")
async def device_command(device_id: str, body: CommandIn, request: Request, session: Session = Depends(get_session)) -> dict:
    # The controller is same-origin. Do not let an unrelated website issue commands.
    from urllib.parse import urlsplit
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Use this server's controller to manage players")
    if body.action not in ("restart", "update", "boot_settings", "update_settings", "maintenance_adb", "player_settings"):
        raise HTTPException(400, "Unknown player command")
    d = session.get(Device, device_id)
    if not d: raise HTTPException(404, "Device not found")
    if not hub.online(device_id): raise HTTPException(409, "Device is offline; commands are not queued")
    if d.capabilities.get("management_protocol") != 1:
        raise HTTPException(409, "Install the management-enabled APK once on this player")
    if body.action == "maintenance_adb" and not d.capabilities.get("maintenance_adb"):
        raise HTTPException(409, "Maintenance access has not been provisioned on this player")
    if body.action == "player_settings" and not d.capabilities.get("tv_menu"):
        raise HTTPException(409, "Update this player to open its settings remotely")
    command = {"type": "command", "id": new_id(), "action": body.action}
    if body.action == "update":
        from ..releases import current_release
        try: release = current_release()
        except (ValueError, OSError) as e: raise HTTPException(409, str(e))
        installed = d.capabilities.get("version_code", 0)
        if release["version_code"] <= installed:
            raise HTTPException(409, "Player already has this release or a newer one")
        command["release"] = release
    if not await hub.send_player(device_id, command): raise HTTPException(409, "Player disconnected")
    status = {"id": command["id"], "action": body.action, "state": "sent", "at": now_ms()}
    hub.status.setdefault(device_id, {})["command"] = status
    d.status = {**d.status, "command": status}; session.add(d); session.commit()
    hub.mark_devices_dirty()
    return status


@router.get("")
def list_devices() -> list[dict]:
    return hub.device_list()


@router.patch("/{device_id}")
async def update_device(device_id: str, body: DeviceIn, session: Session = Depends(get_session)) -> Device:
    d = session.get(Device, device_id)
    if not d:
        raise HTTPException(404, "device not found")
    data = body.model_dump(exclude_none=True)
    if "cache_budget_mb" in data and not 256 <= data["cache_budget_mb"] <= 8192:
        raise HTTPException(400, "cache budget must be between 256 and 8192 MB")
    if data.pop("unassign", False):
        d.surface_id = None
    if "corners" in data and len(data["corners"]) != 4:
        raise HTTPException(400, "corners must have 4 points")
    calibration_fields = {"surface_id", "output_width", "output_height", "mirror_h", "mirror_v", "corners", "mask", "feather",
                          "brightness", "gamma", "gain_r", "gain_g", "gain_b"}
    calibration_changed = bool(calibration_fields.intersection(data) or body.unassign)
    variant_layout_changed = bool({"surface_id", "output_width", "output_height", "corners"}.intersection(data) or body.unassign)
    if calibration_changed:
        for profile in session.exec(select(CalibrationProfile).where(CalibrationProfile.device_id == device_id)).all():
            if profile.active:
                profile.active = False
                profile.updated_at = now_ms()
                session.add(profile)
    for k, v in data.items():
        setattr(d, k, v)
    session.add(d)
    session.commit()
    session.refresh(d)
    await hub.push_config(device_id)
    hub.mark_devices_dirty()
    if calibration_changed:
        await hub.changed("calibration_profiles")
    if variant_layout_changed:
        await hub.changed("layout")
        await runner.reload()
    return d


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: str, session: Session = Depends(get_session)) -> None:
    d = session.get(Device, device_id)
    if d:
        session.delete(d)
        session.commit()
    hub.mark_devices_dirty()


@router.get("/{device_id}/mask.png")
def mask_png(device_id: str, session: Session = Depends(get_session)) -> Response:
    d = session.get(Device, device_id)
    if not d:
        raise HTTPException(404, "device not found")
    img = rasterize_mask(d.mask or [], d.feather)
    ok, buf = cv2.imencode(".png", img)
    return Response(buf.tobytes(), media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.post("/{device_id}/guide")
async def show_guide(device_id: str, body: GuideIn) -> dict:
    if body.pattern is not None and body.pattern not in GUIDE_PATTERNS:
        raise HTTPException(400, f"unknown pattern; use one of {sorted(GUIDE_PATTERNS)}")
    ok = await hub.guide(device_id, body.pattern, body.params)
    if not ok:
        raise HTTPException(409, "device is offline")
    return {"ok": True}


@router.post("/guide-all")
async def guide_all(body: GuideIn) -> dict:
    for dev in list(hub.players):
        await hub.guide(dev, body.pattern, body.params)
    return {"ok": True, "devices": len(hub.players)}


@router.post("/{device_id}/identify")
async def identify(device_id: str) -> dict:
    ok = await hub.guide(device_id, "identify", {"seconds": 5})
    if not ok:
        raise HTTPException(409, "device is offline")
    return {"ok": True}
