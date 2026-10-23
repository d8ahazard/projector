import io
import os
from typing import Any

import qrcode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlmodel import Session

from ..config import APK_DIR, public_url
from ..db import get_session
from ..models import Setting
from ..sync.hub import hub, server_now

router = APIRouter(prefix="/api", tags=["system"])
download_router = APIRouter(tags=["system"])

# Settings editable from the UI; env vars act as defaults.
SETTING_KEYS = {
    "pexels_api_key": "PEXELS_API_KEY",
    "pixabay_api_key": "PIXABAY_API_KEY",
    "freesound_api_key": "FREESOUND_API_KEY",
    "comfy_url": "COMFY_URL",
    "planner_url": "PROJECTOR_PLANNER_URL",
    "planner_model": "PROJECTOR_PLANNER_MODEL",
}
SECRET_KEYS = {"pexels_api_key", "pixabay_api_key", "freesound_api_key"}


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    st = session.get(Setting, key)
    if st is not None and st.value not in (None, ""):
        return st.value
    env = SETTING_KEYS.get(key)
    if env and os.environ.get(env):
        return os.environ[env]
    return default


@router.get("/info")
def info() -> dict:
    apk = APK_DIR / "projector.apk"
    return {
        "server_url": public_url(),
        "server_time": server_now(),
        "apk_available": apk.exists(),
        "apk_url": f"{public_url()}/download/app.apk",
        "players_online": len(hub.players),
    }


@router.get("/app-release")
def app_release() -> dict:
    from ..releases import current_release
    try:
        return {"available": True, **current_release()}
    except (ValueError, OSError) as e:
        return {"available": False, "error": str(e)}


@router.get("/settings")
def read_settings(session: Session = Depends(get_session)) -> dict:
    out = {}
    for key in SETTING_KEYS:
        val = get_setting(session, key, "")
        if key in SECRET_KEYS and val:
            out[key] = "••••" + str(val)[-4:]
        else:
            out[key] = val
    return out


@router.put("/settings")
def write_settings(body: dict, session: Session = Depends(get_session)) -> dict:
    for key, val in body.items():
        if key not in SETTING_KEYS:
            raise HTTPException(400, f"unknown setting {key}")
        if isinstance(val, str) and val.startswith("••••"):
            continue
        st = session.get(Setting, key) or Setting(key=key)
        st.value = val
        session.add(st)
    session.commit()
    return read_settings(session)


@router.get("/qr")
def qr(data: str) -> Response:
    img = qrcode.make(data, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


@download_router.get("/download/app.apk")
def download_apk(sha256: str = "") -> FileResponse:
    apk = APK_DIR / "projector.apk"
    if not apk.exists():
        raise HTTPException(404, "APK not built yet; see README (docker compose --profile apk run --rm apk)")
    if sha256:
        from ..releases import current_release
        try:
            if current_release()["sha256"] != sha256:
                raise ValueError("Release changed; request the update again")
        except (ValueError, OSError) as e:
            raise HTTPException(409, str(e))
    return FileResponse(apk, media_type="application/vnd.android.package-archive", filename="projector.apk")
