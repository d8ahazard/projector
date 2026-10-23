from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_
from sqlmodel import Session, col, select

from ..catalog.indexer import configured_sources, indexer
from ..catalog.sources import SEED_QUERIES, SOURCES
from ..db import get_session
from ..models import CatalogItem
from ..sync.hub import hub

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


class IndexIn(BaseModel):
    seasons: list[str] = ["halloween", "christmas"]
    sources: Optional[list[str]] = None
    queries: list[str] = []
    kinds: list[str] = ["video", "audio"]


class AddIn(BaseModel):
    tags: list[str] = []
    season: str = ""


@router.get("")
def list_catalog(
    q: str = "",
    season: str = "",
    source: str = "",
    kind: str = "",
    in_library: Optional[bool] = None,
    sort: str = "suitability",
    limit: int = 60,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    stmt = select(CatalogItem)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(CatalogItem.title).like(like), func.lower(CatalogItem.description).like(like), func.lower(cast(CatalogItem.tags, String)).like(like)))
    if season:
        stmt = stmt.where(CatalogItem.season == season)
    if source:
        stmt = stmt.where(CatalogItem.source == source)
    if kind:
        stmt = stmt.where(CatalogItem.kind == kind)
    if in_library is True:
        stmt = stmt.where(col(CatalogItem.asset_id).is_not(None))
    elif in_library is False:
        stmt = stmt.where(col(CatalogItem.asset_id).is_(None))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    order = {
        "suitability": col(CatalogItem.suitability).desc(),
        "title": col(CatalogItem.title).asc(),
        "duration": col(CatalogItem.duration_ms).asc(),
        "newest": col(CatalogItem.indexed_at).desc(),
    }.get(sort, col(CatalogItem.suitability).desc())
    items = session.exec(stmt.order_by(order).offset(offset).limit(min(limit, 200))).all()
    return {"items": items, "total": total}


@router.get("/status")
def status() -> dict:
    return {
        **indexer.status,
        "sources": configured_sources(),
        "seasons": list(SEED_QUERIES),
        "downloads": {str(k): v for k, v in indexer.downloads.items()},
    }


@router.post("/index")
def start_index(body: IndexIn) -> dict:
    sources = body.sources or list(SOURCES)
    if not indexer.start(body.seasons, sources, [q for q in body.queries if q.strip()], body.kinds):
        raise HTTPException(409, "indexing already running")
    return {"ok": True}


@router.post("/{item_id}/add")
def add_to_library(item_id: int, body: AddIn, session: Session = Depends(get_session)) -> dict:
    item = session.get(CatalogItem, item_id)
    if not item:
        raise HTTPException(404, "catalog item not found")
    if item.asset_id:
        return {"ok": True, "asset_id": item.asset_id, "already": True}
    indexer.add(item_id, body.tags, body.season)
    return {"ok": True}


@router.delete("/{item_id}", status_code=204)
async def hide_item(item_id: int, session: Session = Depends(get_session)) -> None:
    item = session.get(CatalogItem, item_id)
    if item:
        session.delete(item)
        session.commit()
    await hub.changed("catalog")
