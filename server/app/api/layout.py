from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..geometry import Rect, bounds
from ..models import Surface
from ..sync.hub import hub
from ..sync.runner import runner

router = APIRouter(prefix="/api", tags=["layout"])


class SurfaceIn(BaseModel):
    name: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    groups: Optional[list[str]] = None
    sort: Optional[int] = None


@router.get("/surfaces")
def list_surfaces(session: Session = Depends(get_session)) -> list[Surface]:
    return list(session.exec(select(Surface).order_by(Surface.sort, Surface.x)).all())


@router.get("/layout")
def get_layout(session: Session = Depends(get_session)) -> dict:
    surfaces = list(session.exec(select(Surface).order_by(Surface.sort, Surface.x)).all())
    b = bounds(Rect(s.x, s.y, s.width, s.height) for s in surfaces)
    groups = sorted({g for s in surfaces for g in (s.groups or [])})
    return {"surfaces": surfaces, "groups": groups, "bounds": {"x": b.x, "y": b.y, "w": b.w, "h": b.h}}


@router.post("/surfaces")
async def create_surface(body: SurfaceIn, session: Session = Depends(get_session)) -> Surface:
    count = len(session.exec(select(Surface)).all())
    data = body.model_dump(exclude_none=True)
    data.setdefault("name", f"Window {count + 1}")
    data.setdefault("x", count * 72.0)
    data.setdefault("sort", count)
    s = Surface(**data)
    session.add(s)
    session.commit()
    session.refresh(s)
    await hub.changed("layout")
    return s


@router.patch("/surfaces/{sid}")
async def update_surface(sid: str, body: SurfaceIn, session: Session = Depends(get_session)) -> Surface:
    s = session.get(Surface, sid)
    if not s:
        raise HTTPException(404, "surface not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    session.add(s)
    session.commit()
    session.refresh(s)
    await hub.changed("layout")
    await hub.push_all_configs()
    await runner.reload()
    return s


@router.delete("/surfaces/{sid}", status_code=204)
async def delete_surface(sid: str, session: Session = Depends(get_session)) -> None:
    s = session.get(Surface, sid)
    if s:
        session.delete(s)
        session.commit()
    await hub.changed("layout")
    hub.mark_devices_dirty()
