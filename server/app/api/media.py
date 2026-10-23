import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..config import MEDIA_DIR
from ..db import get_session
from ..media import ffmpeg
from ..media.pipeline import pipeline
from ..models import MediaAsset, Setting
from ..sync.hub import hub

router = APIRouter(prefix="/api/media", tags=["media"])


class AssetIn(BaseModel):
    title: Optional[str] = None
    tags: Optional[list[str]] = None
    season: Optional[str] = None
    license: Optional[str] = None
    attribution: Optional[str] = None


class ReprocessIn(BaseModel):
    preset: dict


@router.post("/{aid}/compatibility")
async def compatibility(aid: str, session: Session = Depends(get_session)):
    from ..config import LEGACY_MEDIA_DIR
    from ..media.library import worker
    a=session.get(MediaAsset,aid)
    if not a: raise HTTPException(404,"Asset not found")
    if a.source_file_id:
        return worker.submit("prepare",{"source_id":a.source_file_id,"profile":"compatibility"})
    source=MEDIA_DIR/a.original_path
    if not source.is_file(): source=LEGACY_MEDIA_DIR/a.original_path
    if not source.is_file(): raise HTTPException(404,"Original file unavailable")
    copy=pipeline.import_file(source,move=False,title=a.title+" (30 FPS compatibility)",source=a.source,
        tags=a.tags,season=a.season,preset={**a.preset,"fps":30,"profile":"compatibility"},technical={**a.technical,"compatibility_of":a.id},published=a.published,asset_id="compat"+a.id)
    await hub.changed("media")
    return copy


@router.get("")
def list_media(
    kind: Optional[str] = None,
    season: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    session: Session = Depends(get_session),
) -> list[MediaAsset]:
    stmt = select(MediaAsset).where(MediaAsset.published == True).order_by(MediaAsset.created_at.desc())
    if kind:
        stmt = stmt.where(MediaAsset.kind == kind)
    if season:
        stmt = stmt.where(MediaAsset.season == season)
    rows = list(session.exec(stmt).all())
    if tag:
        rows = [a for a in rows if tag in (a.tags or [])]
    if q:
        ql = q.lower()
        rows = [a for a in rows if ql in a.title.lower() or any(ql in t for t in a.tags or [])]
    return rows


@router.get("/tags")
def list_tags(session: Session = Depends(get_session)) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in session.exec(select(MediaAsset)).all():
        for t in a.tags or []:
            counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


@router.get("/default-preset")
def get_default_preset(session: Session = Depends(get_session)) -> dict:
    st = session.get(Setting, "default_preset")
    return {**ffmpeg.DEFAULT_PRESET, **(st.value if st and st.value else {})}


@router.put("/default-preset")
def set_default_preset(preset: dict, session: Session = Depends(get_session)) -> dict:
    st = session.get(Setting, "default_preset") or Setting(key="default_preset")
    st.value = {k: v for k, v in preset.items() if k in ffmpeg.DEFAULT_PRESET}
    session.add(st)
    session.commit()
    return {**ffmpeg.DEFAULT_PRESET, **st.value}


@router.post("/upload")
async def upload(
    files: list[UploadFile] = File(...),
    season: str = Form(""),
    tags: str = Form(""),
    license: str = Form(""),
    attribution: str = Form(""),
) -> list[MediaAsset]:
    tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
    out = []
    for f in files:
        suffix = Path(f.filename or "upload.bin").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=MEDIA_DIR) as tmp:
            shutil.copyfileobj(f.file, tmp)
            tmp_path = Path(tmp.name)
        named = tmp_path.with_name(Path(f.filename or tmp_path.name).name)
        if named.exists():
            named = tmp_path
        else:
            tmp_path.rename(named)
        out.append(
            pipeline.import_file(
                named, source="upload", season=season, tags=tag_list, license=license, attribution=attribution
            )
        )
    return out


@router.patch("/{aid}")
async def update_asset(aid: str, body: AssetIn, session: Session = Depends(get_session)) -> MediaAsset:
    a = session.get(MediaAsset, aid)
    if not a:
        raise HTTPException(404, "asset not found")
    data = body.model_dump(exclude_none=True)
    if "tags" in data:
        data["tags"] = sorted({t.strip().lower() for t in data["tags"] if t.strip()})
    for k, v in data.items():
        setattr(a, k, v)
    session.add(a)
    session.commit()
    session.refresh(a)
    await hub.changed("media")
    return a


@router.post("/{aid}/reprocess")
async def reprocess(aid: str, body: ReprocessIn, session: Session = Depends(get_session)) -> MediaAsset:
    a = session.get(MediaAsset, aid)
    if not a:
        raise HTTPException(404, "asset not found")
    a.preset = {k: v for k, v in body.preset.items() if k in ffmpeg.DEFAULT_PRESET}
    session.add(a)
    session.commit()
    pipeline.enqueue(aid)
    session.refresh(a)
    return a


@router.delete("/{aid}", status_code=204)
async def delete_asset(aid: str, session: Session = Depends(get_session)) -> None:
    a = session.get(MediaAsset, aid)
    if not a:
        return
    for p in {a.original_path, a.rendition_path, a.thumb_path, a.preview_path}:
        if p:
            (MEDIA_DIR / p).unlink(missing_ok=True)
    session.delete(a)
    session.commit()
    await hub.changed("media")


credits_router = APIRouter(prefix="/api", tags=["media"])


@credits_router.get("/credits")
def credits(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(select(MediaAsset).order_by(MediaAsset.title)).all()
    return [
        {
            "id": a.id,
            "title": a.title,
            "source": a.source,
            "source_url": a.source_url,
            "license": a.license,
            "attribution": a.attribution,
        }
        for a in rows
        if a.license or a.attribution or a.source.startswith("catalog")
    ]
