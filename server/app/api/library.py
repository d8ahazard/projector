import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select
from ..db import get_session
from ..models import SourceFile, MediaAsset, LibraryJob, MovementSegment, MovementLinkOverride, Program, now_ms
from ..config import LIBRARY_DIR, PACKS_DIR
from ..media.library import effective, worker
from ..sync.hub import hub

router = APIRouter(prefix="/api/library", tags=["library"])

PACK_MEDIA_EXTENSIONS = {".mp4"}


def pack_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    if not slug:
        raise HTTPException(400, "Enter a package name")
    return slug


def safe_pack_path(value: str) -> Path:
    raw = str(value or "").replace("\\", "/")
    parts = [part for part in PurePosixPath(raw).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(400, "A package file has an unsafe path")
    clean = [re.sub(r'[<>:"|?*\x00-\x1f]', "_", part).strip(" .") or "unnamed" for part in parts]
    return Path(*clean)


def pack_manifest(pack_id: str) -> dict:
    path = PACKS_DIR / pack_id / ".projector-pack.json"
    if not path.is_file():
        return {"id": pack_id, "name": pack_id.replace("-", " ").title()}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {"id": pack_id, "name": str(value.get("name") or pack_id)}
    except (OSError, ValueError):
        return {"id": pack_id, "name": pack_id.replace("-", " ").title()}


def movement_view(segment: MovementSegment, session: Session) -> dict:
    source = session.get(SourceFile, segment.source_file_id)
    asset = session.get(MediaAsset, segment.asset_id) if segment.asset_id else None
    return {**segment.model_dump(), "source": view(source, session) if source else None,
            "asset_status": asset.status if asset else "unprepared", "asset_error": asset.error if asset else ""}


def view(row, session):
    meta = effective(row)
    asset = session.get(MediaAsset, row.asset_id) if row.asset_id else None
    return {**row.model_dump(), **meta, "ready": bool(asset and asset.status == "ready"),
            "preparation": asset.status if asset else "unprepared", "preparation_error": asset.error if asset else "",
            "thumb_url": f"/api/library/files/{row.id}/thumb" if (LIBRARY_DIR / f"{row.id}.jpg").exists() else "",
            "preview_url": f"/api/library/files/{row.id}/preview" if (LIBRARY_DIR / f"{row.id}.mp4").exists() else "",
            "sheet_url": f"/api/library/files/{row.id}/sheet" if (LIBRARY_DIR / f"{row.id}-sheet.jpg").exists() else ""}


@router.get("/scenes")
def scenes(q: str = "", target: str = "", appearance: str = "", orientation: str = "", collection: str = "", character: str = "", readiness: str = "", buffers: bool = False, min_seconds: float = 0, max_seconds: float = 0, page: int = Query(1, ge=1), limit: int = Query(24, ge=1, le=100), session: Session = Depends(get_session)):
    from collections import Counter
    from pathlib import Path
    import re
    grouped = {}
    rows = session.exec(select(SourceFile).order_by(SourceFile.collection, SourceFile.title)).all()
    probable = Counter((r.size, Path(r.relative_path).name.lower()) for r in rows if r.status == "indexed")
    for row in rows:
        m = effective(row)
        if row.status == "ignored" or (m.get("buffer") and not buffers): continue
        if target and target not in m.get("targets", []): continue
        if appearance and appearance != m.get("appearance"): continue
        if orientation and orientation != m.get("orientation"): continue
        if collection and collection != row.collection: continue
        if character and character.lower() not in m.get("character", "").lower(): continue
        if q and re.sub(r"\W+", "", q.lower()) not in re.sub(r"\W+", "", (row.relative_path + " " + str(m)).lower().replace("_", "")): continue
        dur = row.technical.get("duration", 0)
        if dur < min_seconds or (max_seconds and dur > max_seconds): continue
        v = view(row, session)
        v["probable_copies"] = probable[(row.size, Path(row.relative_path).name.lower())]
        if readiness == "ready" and not v["ready"]: continue
        if readiness == "review" and not m.get("review") and row.status == "indexed": continue
        if readiness == "unprepared" and v["ready"]: continue
        score = (4 if target and target in m.get("targets", []) else 0) + (2 if appearance and appearance == m.get("appearance") else 0) + (1 if v["ready"] else 0)
        v["match_score"] = score
        v["match_reason"] = ", ".join(filter(None, [f"{target} format" if target else "", f"{appearance} appearance" if appearance else "", "ready to play" if v["ready"] else "prepare before playback"]))
        key = m.get("scene_key", row.scene_key)
        if not m.get("targets") and m.get("review"): key = f"{key}:{row.id}"
        grouped.setdefault(key, {"id": key, "title": m.get("title", row.title), "collection": row.collection, "variants": []})["variants"].append(v)
    items = list(grouped.values())
    for item in items:
        confirmed = {}; variants = []
        for v in item["variants"]:
            key = (v["sha256"], v["content_rotation"], v["appearance"], tuple(v["targets"]))
            if v["sha256"] and key in confirmed:
                confirmed[key]["identical_locations"].append(v["relative_path"])
            else:
                v["identical_locations"] = [v["relative_path"]]
                variants.append(v)
                if v["sha256"]: confirmed[key] = v
        item["variants"] = sorted(variants, key=lambda v: (-v["match_score"], v["relative_path"]))
    return {"items": items[(page-1)*limit:page*limit], "total": len(items), "page": page}


@router.get("/facets")
def facets(session: Session = Depends(get_session)):
    rows = session.exec(select(SourceFile)).all()
    return {"collections": sorted({r.collection for r in rows}), "characters": sorted({effective(r).get("character", "") for r in rows} - {""}),
            "counts": {status: sum(r.status == status for r in rows) for status in ("indexed", "ignored", "error", "missing")}, "total": len(rows)}


@router.get("/packs")
def source_packs(session: Session = Depends(get_session)) -> list[dict]:
    rows = [row for row in session.exec(select(SourceFile)).all() if row.relative_path.startswith("@packs/")]
    grouped: dict[str, list[SourceFile]] = {}
    for row in rows:
        parts = row.relative_path.split("/", 2)
        if len(parts) >= 2:
            grouped.setdefault(parts[1], []).append(row)
    result = []
    for pack_id, members in sorted(grouped.items(), key=lambda item: pack_manifest(item[0])["name"].lower()):
        prepared = 0
        processing = 0
        errors = sum(row.status == "error" for row in members)
        thumb = ""
        for row in members:
            asset = session.get(MediaAsset, row.asset_id) if row.asset_id else None
            if asset and asset.status == "ready": prepared += 1
            elif asset and asset.status in ("pending", "processing"): processing += 1
            if not thumb and (LIBRARY_DIR / f"{row.id}.jpg").is_file():
                thumb = f"/api/library/files/{row.id}/thumb"
        scenes = {effective(row).get("scene_key", row.scene_key) for row in members if row.status == "indexed"}
        characters = {str(effective(row).get("character") or "").strip() for row in members} - {""}
        collections = {row.collection for row in members if row.collection}
        result.append({**pack_manifest(pack_id), "files": len(members), "scenes": len(scenes),
                       "characters": len(characters), "collections": sorted(collections),
                       "prepared": prepared, "processing": processing, "errors": errors,
                       "thumbnail_url": thumb})
    return result


def _pack_destination(root: Path, relative: Path, expected_size: int = -1) -> tuple[Path, bool]:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        return destination, False
    if expected_size >= 0 and destination.stat().st_size == expected_size:
        return destination, True
    for index in range(2, 10000):
        candidate = destination.with_name(f"{destination.stem}-{index}{destination.suffix}")
        if not candidate.exists():
            return candidate, False
    raise HTTPException(409, f"Too many files named {destination.name}")


@router.post("/packs/import")
async def import_source_pack(
    files: list[UploadFile] = File(...),
    pack_name: str = Form(...),
) -> dict:
    pack_id = pack_slug(pack_name)
    root = PACKS_DIR / pack_id
    root.mkdir(parents=True, exist_ok=True)
    (root / ".projector-pack.json").write_text(
        json.dumps({"id": pack_id, "name": pack_name.strip(), "imported_at": now_ms()}, indent=2), encoding="utf-8"
    )
    queued = skipped = 0
    errors: list[str] = []
    expanded = 0
    expanded_bytes = 0
    for upload in files:
        filename = upload.filename or "upload"
        try:
            if Path(filename).suffix.lower() == ".zip":
                await upload.seek(0)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=PACKS_DIR) as staged:
                    shutil.copyfileobj(upload.file, staged, 1024 * 1024)
                    staged_path = Path(staged.name)
                try:
                    with zipfile.ZipFile(staged_path) as archive:
                        for info in archive.infolist():
                            if info.is_dir() or Path(info.filename).suffix.lower() not in PACK_MEDIA_EXTENSIONS:
                                continue
                            expanded += 1
                            expanded_bytes += info.file_size
                            if expanded > 5000 or expanded_bytes > 250 * 1024**3:
                                raise HTTPException(413, "Package is too large (limit: 5,000 videos / 250 GB expanded)")
                            relative = safe_pack_path(info.filename)
                            destination, duplicate = _pack_destination(root, relative, info.file_size)
                            if duplicate:
                                skipped += 1
                                continue
                            with archive.open(info) as source, destination.open("xb") as target:
                                shutil.copyfileobj(source, target, 1024 * 1024)
                            queued += 1
                finally:
                    staged_path.unlink(missing_ok=True)
            elif Path(filename).suffix.lower() in PACK_MEDIA_EXTENSIONS:
                relative = safe_pack_path(filename)
                expected = int(upload.size) if upload.size is not None else -1
                destination, duplicate = _pack_destination(root, relative, expected)
                if duplicate:
                    skipped += 1
                    continue
                await upload.seek(0)
                with destination.open("xb") as target:
                    shutil.copyfileobj(upload.file, target, 1024 * 1024)
                queued += 1
        except HTTPException:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            errors.append(f"{Path(filename).name}: {str(exc)[:180]}")
    if not queued and not skipped:
        raise HTTPException(400, "No MP4 videos were found in the selected folder or ZIP")
    scan_job = worker.submit("scan")
    return {"id": pack_id, "name": pack_name.strip(), "queued": queued, "skipped": skipped,
            "errors": errors, "scan_job_id": scan_job.id}


@router.post("/packs/shuffle")
async def shuffle_source_packs(body: dict, session: Session = Depends(get_session)) -> dict:
    pack_ids = list(dict.fromkeys(str(value) for value in body.get("pack_ids", [])))
    if not pack_ids or any(pack_slug(value) != value for value in pack_ids):
        raise HTTPException(400, "Choose at least one valid package")
    available = {row.relative_path.split("/", 2)[1] for row in session.exec(select(SourceFile)).all()
                 if row.relative_path.startswith("@packs/")}
    missing = [value for value in pack_ids if value not in available]
    if missing:
        raise HTTPException(404, f"Package not indexed: {', '.join(missing)}")
    default_name = " + ".join(pack_manifest(value)["name"] for value in pack_ids)
    program = Program(name=str(body.get("name") or f"{default_name} Shuffle").strip(), loop_mode="loop", seed=1, items=[])
    session.add(program); session.commit(); session.refresh(program)
    job = worker.submit("complete_roster", {"program_id": program.id,
                                             "source_prefixes": [f"@packs/{value}/" for value in pack_ids],
                                             "pack_ids": pack_ids})
    await hub.changed("programs")
    return {"program": program, "job": job}


@router.get("/jobs")
def jobs(session: Session = Depends(get_session)):
    return session.exec(select(LibraryJob).order_by(LibraryJob.created_at.desc()).limit(50)).all()


@router.get("/movement/segments")
def movement_segments(q: str = "", family: str = "", character: str = "", approval: str = "",
                      prepared: bool | None = None, page: int = Query(1, ge=1), limit: int = Query(24, ge=1, le=100),
                      session: Session = Depends(get_session)):
    rows = list(session.exec(select(MovementSegment).order_by(MovementSegment.confidence.desc(), MovementSegment.created_at.desc())).all())
    if family: rows = [x for x in rows if x.family == family]
    if character: rows = [x for x in rows if character.lower() in x.character.lower()]
    if approval: rows = [x for x in rows if x.approval == approval]
    if prepared is not None: rows = [x for x in rows if bool(x.asset_id) == prepared]
    if q:
        needle = q.lower()
        rows = [x for x in rows if needle in f"{x.character} {x.family} {x.narrative_group}".lower()]
    total = len(rows)
    rows = rows[(page-1)*limit:page*limit]
    return {"items": [movement_view(x, session) for x in rows], "total": total, "page": page,
            "facets": {"families": sorted({x.family for x in rows}), "characters": sorted({x.character for x in rows})}}


@router.patch("/movement/segments/{segment_id}")
async def update_movement_segment(segment_id: str, body: dict, session: Session = Depends(get_session)):
    row = session.get(MovementSegment, segment_id)
    if not row: raise HTTPException(404, "Movement segment not found")
    allowed = {"start_ms", "end_ms", "active_start_ms", "active_end_ms", "enter_edge", "exit_edge", "approval",
               "character", "family", "narrative_group", "mirror_safe"}
    if set(body)-allowed: raise HTTPException(400, "Unknown movement field")
    for edge in ("enter_edge", "exit_edge"):
        if edge in body and body[edge] not in {"left", "right", "top", "bottom", "none"}: raise HTTPException(400, "Invalid edge")
    if "approval" in body and body["approval"] not in {"auto", "approved", "review", "rejected"}: raise HTTPException(400, "Invalid approval")
    for key, value in body.items(): setattr(row, key, value)
    if row.end_ms <= row.start_ms or row.active_start_ms < row.start_ms or row.active_end_ms > row.end_ms:
        raise HTTPException(400, "Segment timing is inconsistent")
    row.updated_at = now_ms(); session.add(row); session.commit(); session.refresh(row)
    await hub.changed("library")
    return movement_view(row, session)


@router.post("/movement/segments/{segment_id}/split")
async def split_movement_segment(segment_id: str, body: dict, session: Session = Depends(get_session)):
    row = session.get(MovementSegment, segment_id)
    if not row: raise HTTPException(404, "Movement segment not found")
    at = int(body.get("at_ms") or 0)
    if at <= row.active_start_ms+500 or at >= row.active_end_ms-500: raise HTTPException(400, "Split must be inside active action")
    indexes = [x.segment_index for x in session.exec(select(MovementSegment).where(MovementSegment.source_file_id == row.source_file_id)).all()]
    second = MovementSegment(**{**row.model_dump(exclude={"id", "asset_id", "created_at", "updated_at"}),
                                "segment_index": max(indexes, default=-1)+1, "start_ms": at, "active_start_ms": at,
                                "lead_blank_ms": 0, "enter_edge": "none", "approval": "review"})
    row.end_ms = at; row.active_end_ms = at; row.tail_blank_ms = 0; row.exit_edge = "none"; row.asset_id = None; row.approval = "review"
    session.add(row); session.add(second); session.commit(); await hub.changed("library")
    return [movement_view(row, session), movement_view(second, session)]


@router.post("/movement/segments/merge")
async def merge_movement_segments(body: dict, session: Session = Depends(get_session)):
    a, b = session.get(MovementSegment, body.get("first_id")), session.get(MovementSegment, body.get("second_id"))
    if not a or not b: raise HTTPException(404, "Movement segment not found")
    if a.source_file_id != b.source_file_id: raise HTTPException(400, "Only segments from the same source can merge")
    a.start_ms, a.end_ms = min(a.start_ms, b.start_ms), max(a.end_ms, b.end_ms)
    a.active_start_ms, a.active_end_ms = min(a.active_start_ms, b.active_start_ms), max(a.active_end_ms, b.active_end_ms)
    a.enter_edge = a.enter_edge if a.active_start_ms <= b.active_start_ms else b.enter_edge
    a.exit_edge = a.exit_edge if a.active_end_ms >= b.active_end_ms else b.exit_edge
    a.asset_id = None; a.approval = "review"; a.updated_at = now_ms()
    session.add(a); session.delete(b); session.commit(); await hub.changed("library")
    return movement_view(a, session)


@router.post("/movement/segments/{segment_id}/prepare")
async def prepare_movement_segment(segment_id: str, session: Session = Depends(get_session)):
    from ..media import ffmpeg
    from ..media.pipeline import pipeline
    row = session.get(MovementSegment, segment_id)
    if not row: raise HTTPException(404, "Movement segment not found")
    if row.approval not in ("auto", "approved"): raise HTTPException(409, "Approve this segment before preparation")
    source = session.get(SourceFile, row.source_file_id)
    if not source: raise HTTPException(404, "Source file missing")
    asset = session.get(MediaAsset, row.asset_id) if row.asset_id else None
    if not asset:
        meta = {**source.classification, **source.overrides}
        aid = f"mv{row.id}"
        asset = MediaAsset(id=aid, title=f"{source.title} · beat {row.segment_index+1}", source="movement-segment",
                           source_file_id=source.id, tags=sorted(set(["movement", "smart-handoff", row.family, row.character])-{""}),
                           season=str(meta.get("season") or ""), published=True,
                           preset={**ffmpeg.DEFAULT_PRESET, "profile": "movement", "trim_start": row.start_ms/1000,
                                   "trim_end": max(0, float((source.technical or {}).get("duration") or 0)-row.end_ms/1000),
                                   "source_duration": float((source.technical or {}).get("duration") or 0),
                                   "content_rotation": int(meta.get("content_rotation") or 0)},
                           technical={"movement_segment": row.model_dump(exclude={"asset_id"})})
        session.add(asset); row.asset_id = aid; row.updated_at = now_ms(); session.add(row); session.commit()
        pipeline.enqueue(aid)
    elif asset.status == "error": pipeline.enqueue(asset.id)
    await hub.changed("media")
    return movement_view(row, session)


@router.post("/movement/prepare-approved")
async def prepare_approved_movement(session: Session = Depends(get_session)):
    rows = session.exec(select(MovementSegment).where(MovementSegment.approval.in_(["auto", "approved"]))).all()
    queued, skipped, errors = [], 0, []
    for row in rows:
        try:
            result = await prepare_movement_segment(row.id, session)
            if result.get("asset_status") == "ready": skipped += 1
            else: queued.append(row.id)
        except HTTPException as exc:
            errors.append({"segment_id": row.id, "error": str(exc.detail)})
    return {"queued": len(queued), "already_ready": skipped, "errors": errors}


@router.get("/movement/roster")
def movement_roster(session: Session = Depends(get_session)):
    segments = session.exec(select(MovementSegment).where(MovementSegment.approval.in_(["auto", "approved"]))).all()
    ready = {a.id for a in session.exec(select(MediaAsset).where(MediaAsset.status == "ready", MediaAsset.published == True)).all()}
    characters: dict[tuple[str, str], dict] = {}
    for segment in segments:
        if not segment.asset_id or segment.asset_id not in ready: continue
        key = (segment.family, segment.character or segment.narrative_group)
        row = characters.setdefault(key, {"id": f"movement-{len(characters)}", "name": key[1], "clips": [],
                                          "source_program": key[0], "family": key[0]})
        if segment.asset_id not in row["clips"]: row["clips"].append(segment.asset_id)
    roster = list(characters.values())
    groups = []
    for family in sorted({x["family"] for x in roster}):
        groups.append({"id": f"family-{len(groups)}", "name": family,
                       "character_ids": [x["id"] for x in roster if x["family"] == family]})
    for row in roster: row.pop("family", None)
    return {"roster": roster, "groups": groups, "segments": sum(len(x["clips"]) for x in roster)}


def _link_score(a: MovementSegment, b: MovementSegment) -> tuple[float, str, bool]:
    same = bool(a.character and a.character.lower() == b.character.lower())
    narrative = bool(a.narrative_group and a.narrative_group == b.narrative_group)
    related = a.family == b.family
    if not (same or narrative or related): return 0, "", False
    score = .55 if same else .42 if narrative else .18
    if a.exit_edge in ("left", "right"): score += .22
    if b.enter_edge in ("left", "right"): score += .18
    desired = "left" if a.exit_edge == "right" else "right" if a.exit_edge == "left" else "none"
    flip = desired != "none" and b.enter_edge not in (desired, "none") and b.mirror_safe
    if desired == b.enter_edge: score += .05
    return min(1, score), "same_character" if same else "narrative" if narrative else "family", flip


@router.get("/movement/links")
def movement_links(limit: int = Query(100, ge=1, le=500), session: Session = Depends(get_session)):
    rows = list(session.exec(select(MovementSegment).where(MovementSegment.approval.in_(["auto", "approved"]))).all())
    overrides = {(x.from_segment_id, x.to_segment_id): x for x in session.exec(select(MovementLinkOverride)).all()}
    links = []
    for a in rows:
        for b in rows:
            if a.id == b.id: continue
            override = overrides.get((a.id, b.id))
            if override and override.decision == "reject": continue
            score, relation, flip = _link_score(a, b)
            if override and override.decision == "force": score, relation = 1, override.relation
            if score >= .55: links.append({"from_segment_id": a.id, "to_segment_id": b.id, "score": round(score, 3),
                                           "relation": relation, "flip_h": flip, "forced": bool(override)})
    return sorted(links, key=lambda x: -x["score"])[:limit]


@router.put("/movement/links/{from_id}/{to_id}")
async def movement_link_override(from_id: str, to_id: str, body: dict, session: Session = Depends(get_session)):
    if not session.get(MovementSegment, from_id) or not session.get(MovementSegment, to_id): raise HTTPException(404, "Segment missing")
    decision = body.get("decision")
    if decision not in ("force", "reject"): raise HTTPException(400, "Decision must be force or reject")
    row = session.exec(select(MovementLinkOverride).where(MovementLinkOverride.from_segment_id == from_id,
                                                          MovementLinkOverride.to_segment_id == to_id)).first()
    row = row or MovementLinkOverride(from_segment_id=from_id, to_segment_id=to_id)
    row.decision, row.relation, row.note = decision, str(body.get("relation") or "same_character"), str(body.get("note") or "")
    session.add(row); session.commit(); await hub.changed("library"); return row


@router.post("/scan")
async def scan_library(): return worker.submit("scan")


@router.post("/motion")
async def analyze_library_motion(body: dict | None = None, session: Session = Depends(get_session)):
    source_ids = (body or {}).get("source_ids")
    if source_ids is not None:
        if not isinstance(source_ids, list) or not source_ids or any(not isinstance(x, str) for x in source_ids):
            raise HTTPException(400, "source_ids must be a non-empty list")
        known = {r.id for r in session.exec(select(SourceFile).where(SourceFile.id.in_(source_ids))).all()}
        if known != set(source_ids): raise HTTPException(404, "One or more source files do not exist")
    return worker.submit("motion", {"source_ids": source_ids} if source_ids else {})


@router.post("/files/{sid}/prepare")
async def prepare_file(sid: str, profile: str = "source", session: Session = Depends(get_session)):
    if profile not in ("native", "source", "compatibility", "interpolated60"): raise HTTPException(400, "Unknown profile")
    if not session.get(SourceFile, sid): raise HTTPException(404, "Source not found")
    return worker.submit("prepare", {"source_id": sid, "profile": profile})


@router.post("/jobs/{jid}/retry")
async def retry_job(jid: str, session: Session = Depends(get_session)):
    import asyncio
    job = session.get(LibraryJob, jid)
    if not job: raise HTTPException(404, "Job not found")
    if job.status != "error": raise HTTPException(409, "Only failed jobs can be retried")
    job.status="queued"; job.error=""; session.add(job); session.commit()
    task=asyncio.create_task(worker.run(jid)); worker.tasks.add(task); task.add_done_callback(worker.tasks.discard)
    return job


@router.post("/files/{sid}/preview")
async def preview_file(sid: str, session: Session = Depends(get_session)):
    if not session.get(SourceFile, sid): raise HTTPException(404, "Source not found")
    return worker.submit("preview", {"source_id": sid})


@router.patch("/files/{sid}")
async def correct(sid: str, body: dict, session: Session = Depends(get_session)):
    row = session.get(SourceFile, sid)
    if not row: raise HTTPException(404, "Source not found")
    allowed = {"title", "targets", "appearance", "orientation", "content_rotation", "character", "action", "season", "buffer", "review", "scene_key", "motion"}
    if set(body) - allowed: raise HTTPException(400, "Unknown classification field")
    for key in ("title","appearance","orientation","character","action","season","scene_key"):
        if key in body and not isinstance(body[key],str): raise HTTPException(400,f"{key} must be text")
    for key in ("buffer","review"):
        if key in body and not isinstance(body[key],bool): raise HTTPException(400,f"{key} must be true or false")
    if "appearance" in body and body["appearance"] not in ("black","enclosure","shadow","unknown"): raise HTTPException(400,"Unknown appearance")
    if "orientation" in body and body["orientation"] not in ("vertical","horizontal"): raise HTTPException(400,"Unknown orientation")
    if "content_rotation" in body and body["content_rotation"] not in (0,90,180,270): raise HTTPException(400, "Rotation must be 0, 90, 180 or 270")
    if "targets" in body and (not isinstance(body["targets"], list) or any(not isinstance(x,str) or x not in {"window","wall","doorway","tv","hollusion","prop"} for x in body["targets"])): raise HTTPException(400, "Invalid targets")
    if "motion" in body:
        m = body["motion"]
        if not isinstance(m, dict) or m.get("enter_edge", "none") not in {"left","right","top","bottom","none"} or m.get("exit_edge", "none") not in {"left","right","top","bottom","none"} or m.get("travel", "stationary") not in {"left-right","right-left","top-bottom","bottom-top","stationary"}:
            raise HTTPException(400, "Invalid motion correction")
    row.overrides = {**row.overrides, **body}
    if "content_rotation" in body: row.asset_id = None
    session.add(row); session.commit(); session.refresh(row)
    if "content_rotation" in body: worker.submit("preview", {"source_id": sid})
    await hub.changed("library")
    return view(row, session)


@router.get("/files/{sid}/{kind}")
def artifact(sid: str, kind: str, session: Session = Depends(get_session)):
    if not session.get(SourceFile, sid): raise HTTPException(404, "Source not found")
    suffix = {"thumb": ".jpg", "sheet": "-sheet.jpg", "preview": ".mp4"}.get(kind)
    if not suffix: raise HTTPException(404)
    p = LIBRARY_DIR / f"{sid}{suffix}"
    if not p.is_file(): raise HTTPException(404, "Preview has not been prepared")
    return FileResponse(p)
