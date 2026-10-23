"""Read-only source inventory. Classification and preparation never mutate source files."""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import re
from pathlib import Path

from sqlmodel import select

from ..config import SOURCE_ROOT, LIBRARY_DIR, PACKS_DIR
from ..db import session_scope
from ..models import SourceFile, LibraryJob, MediaAsset, MovementSegment, now_ms
from . import ffmpeg


def classify(relative: str) -> dict:
    p = Path(relative)
    parts = p.parts
    # The videos bundled with the projector use orientation folders and encode
    # the real collection/scene in the filename. Treat the H/V files as scene
    # variants instead of creating two misleading orientation collections.
    bundled = parts and parts[0].lower() == "shitthatcamewiththeprojector"
    bundled_match = re.match(r"^\s*\d+\s*-\s*(.+?)\s*-\s*(.+?)\s*-\s*([hv])\s*$", p.stem, re.I) if bundled else None
    if bundled_match:
        collection, title, suffix = (x.strip() for x in bundled_match.groups())
        orientation = "vertical" if suffix.lower() == "v" or "vertical" in relative.lower() else "horizontal"
        details = {
            "Facade Of Fright": ("Facade Of Fright", "Facade Of Fright"),
            "Slender Specter Bent Out of Shape": ("Slender Specter", "Bent Out of Shape"),
            "Grave Risers": ("Grave Risers", "Grave Risers"),
            "Gathering Shadows": ("Zombie Horde", "Gathering Shadows"),
            "Ghost Pirate Yo Ho Ho": ("Ghost Pirate", "Yo Ho Ho"),
            "Endless Nightmare Excerpt": ("Endless Nightmare", "Excerpt"),
            "Baleful Blue Swarming Terrors": ("Baleful Blue", "Swarming Terrors"),
            "Creepster Now You See Me": ("Creepster", "Now You See Me"),
            "Cursed Coffin": ("Cursed Coffin", "Cursed Coffin"),
        }
        character, action = details.get(title, (title, title))
        key = hashlib.sha256(f"{collection.lower()}|{re.sub(r'[^a-z0-9]', '', title.lower())}".encode()).hexdigest()[:20]
        return {"title": title, "collection": collection, "scene_key": key,
                "targets": ["window", "wall", "tv"],
                "appearance": "shadow" if collection.lower() == "sinister shadows" else "enclosure",
                # These bundled portrait masters use the same clockwise
                # sideways storage as the full AtmosFX collection. A +90
                # display matrix presents them upright without re-encoding.
                "orientation": orientation, "content_rotation": 90 if orientation == "vertical" else 0,
                "character": character, "action": action, "season": "halloween",
                "buffer": False, "confidence": 0.98, "review": False}
    collection = parts[1] if len(parts) > 2 else parts[0]
    if parts[0].lower() == "legends of halloween":
        collection = "Legends of Halloween"
    text = relative.lower()
    # Promotional reels and setup walkthroughs are not show material. Keep the
    # source file untouched, but exclude it from the library and every roster.
    roster_excluded = bool(re.search(
        r"(?:^|[^a-z0-9])(trailer|promo|setup|installation|instructions?|tutorial|walkthrough|how[ _-]*to)(?:[^a-z0-9]|$)",
        text,
    ))
    tokens = re.split(r"[^a-z0-9]+", text)
    targets = []
    for name, words in {"window": ("window", "win"), "wall": ("wall",), "doorway": ("doorway", "door"), "tv": ("tv", "monitor"), "hollusion": ("hollusion", "holl", "hologram"), "prop": ("3dfx", "prop", "disc")}.items():
        if any(w in tokens for w in words):
            targets.append(name)
    orientation = "vertical" if "vertical" in tokens or re.search(r"[_ -]v(?:\.mp4)?$", text) else "horizontal"
    appearance = "shadow" if "shadow" in text else "enclosure" if "background" in text or "hallway" in text or "bg" in tokens else "black" if "hollusion" in targets or "black" in tokens else "unknown"
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", p.stem)
    stem = re.sub(r"(?i)\b(window|win|wall|doorway|tv|monitor|hollusion|holl|hologram|horizontal|vertical|background|bg|shadow|3dfx|disc|1080p|720p)\b", " ", stem.replace("_", " "))
    stem = re.sub(r"(?i)(?:^|\s)[hv](?:\s|$)", " ", stem)
    title = re.sub(r"\s+", " ", re.sub(r"[+_-]+", " ", stem)).strip() or p.stem
    # Only group by explicit scene name within a collection; never fuzzy-merge.
    key = hashlib.sha256(f"{collection.lower()}|{re.sub(r'[^a-z0-9]', '', title.lower())}".encode()).hexdigest()[:20]
    character = next((x for x in parts[2:-1] if not re.search(r"(?i)window|wall|holl|horizontal|vertical|tv|buffer|background|doorway", x)), "")
    if collection == "Legends of Halloween": character = p.stem.split("_")[0]
    if not character:
        candidate = p.stem.split("_")[0]
        if len(candidate) > 3: character = candidate
    return {"title": title, "collection": collection, "scene_key": key,
            "targets": targets, "appearance": appearance, "orientation": orientation,
            # AtmosFX portrait masters are stored sideways with their visual
            # top on the source frame's left edge, so they require a 90 degree
            # clockwise content correction.  The native MP4 wrapper converts
            # this to the inverse (-90) display matrix without re-encoding.
            "content_rotation": 90 if orientation == "vertical" else 0,
            "character": character, "action": title, "season": "christmas" if re.search(r"(?i)christmas|santa|xmas", relative) else "easter" if re.search(r"(?i)easter|bunny", relative) else "halloween",
            "buffer": "buffer" in text, "confidence": 0.9 if targets else 0.3,
            "review": not targets or orientation == "vertical" or appearance == "unknown",
            "roster_excluded": roster_excluded}


def source_path(row: SourceFile) -> Path:
    if row.relative_path.startswith("@packs/"):
        relative = Path(row.relative_path.removeprefix("@packs/"))
        root = PACKS_DIR.resolve()
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError("pack source path escapes configured library")
        return path
    root = SOURCE_ROOT.resolve()
    path = (root / row.relative_path).resolve()
    if root not in path.parents:
        raise ValueError("source path escapes configured library")
    return path


def effective(row: SourceFile) -> dict:
    return {**row.classification, **row.overrides}


def job_update(jid: str, **values):
    with session_scope() as s:
        j = s.get(LibraryJob, jid)
        for k, v in values.items():
            setattr(j, k, v)
        s.add(j)
        s.commit()
    if worker.loop and not worker.loop.is_closed():
        from ..sync.hub import hub
        worker.loop.call_soon_threadsafe(asyncio.create_task, hub.changed("library"))


def analyze_motion(jid: str, source_ids: list[str] | None = None):
    from .motion import ANALYZER_VERSION, analyze_video
    with session_scope() as s:
        rows = s.exec(select(SourceFile).where(SourceFile.status == "indexed")).all()
        if source_ids:
            wanted = set(source_ids)
            rows = [row for row in rows if row.id in wanted]
    failures = []
    completed = 0
    for row in rows:
        try:
            meta = effective(row)
            result = analyze_video(source_path(row), int(meta.get("content_rotation") or 0))
            with session_scope() as s:
                current = s.get(SourceFile, row.id)
                current.technical = {**(current.technical or {}), "motion": result}
                current.updated_at = now_ms()
                s.add(current)
                prior = {x.segment_index: x for x in s.exec(
                    select(MovementSegment).where(MovementSegment.source_file_id == row.id)
                ).all()}
                kept: set[int] = set()
                for raw in result.get("segments", []):
                    index = int(raw["index"]); kept.add(index)
                    segment = prior.get(index) or MovementSegment(source_file_id=row.id, segment_index=index)
                    old_decision = segment.approval if segment.approval in ("approved", "rejected") else ""
                    segment.analyzer_version = ANALYZER_VERSION
                    for field in ("start_ms", "end_ms", "active_start_ms", "active_end_ms", "lead_blank_ms", "tail_blank_ms",
                                  "enter_edge", "exit_edge", "dx", "dy", "confidence", "mirror_safe"):
                        setattr(segment, field, raw[field])
                    segment.character = str(meta.get("character") or "")
                    segment.family = row.collection
                    segment.narrative_group = str(meta.get("action") or row.scene_key)
                    segment.approval = old_decision or raw["approval"]
                    segment.audio = {"present": bool((row.technical or {}).get("has_audio")), "preserve": True}
                    segment.evidence = {"hard_cuts_ms": raw.get("hard_cuts_ms", []), "samples": raw.get("samples", 0),
                                        "scene_key": row.scene_key, "analyzer": "activity+cuts"}
                    segment.updated_at = now_ms()
                    s.add(segment)
                for index, stale in prior.items():
                    if index not in kept and stale.analyzer_version == ANALYZER_VERSION:
                        s.delete(stale)
                s.commit()
        except Exception as exc:
            failures.append({"source_id": row.id, "error": str(exc)[:300]})
        completed += 1
        if completed == len(rows) or completed % 5 == 0:
            job_update(jid, progress=completed / max(1, len(rows)),
                       detail={"completed": completed, "total": len(rows), "errors": failures})
    job_update(jid, detail={"completed": completed, "total": len(rows), "errors": failures})


def scan(jid: str):
    if not SOURCE_ROOT.is_dir() and not PACKS_DIR.is_dir():
        raise ValueError(f"Source libraries unavailable: {SOURCE_ROOT} and {PACKS_DIR}")
    files: list[tuple[Path, str]] = []
    if SOURCE_ROOT.is_dir():
        files.extend((p, p.relative_to(SOURCE_ROOT).as_posix()) for p in SOURCE_ROOT.rglob("*")
                     if p.is_file() and p.suffix.lower() == ".mp4")
    if PACKS_DIR.is_dir():
        files.extend((p, "@packs/" + p.relative_to(PACKS_DIR).as_posix()) for p in PACKS_DIR.rglob("*")
                     if p.is_file() and p.suffix.lower() == ".mp4")
    files.sort(key=lambda item: item[1].lower())
    with session_scope() as s:
        existing = {r.relative_path: SourceFile(**r.model_dump()) for r in s.exec(select(SourceFile)).all()}
    counts = {"total": len(files), "readable": 0, "ignored": 0, "errors": 0, "missing": 0}

    def inspect(item):
        p, relative = item
        old = existing.get(relative)
        st = p.stat()
        classify_path = relative
        pack_id = ""
        if relative.startswith("@packs/"):
            pieces = relative.split("/", 2)
            pack_id = pieces[1]
            classify_path = pieces[2] if len(pieces) > 2 else p.name
        c = classify(classify_path)
        if pack_id:
            c = {**c, "pack_id": pack_id}
        # Threads work on detached copies, including when a caller owns a session.
        row = SourceFile(**old.model_dump()) if old else SourceFile(relative_path=relative, scene_key=c["scene_key"], title=c["title"], collection=c["collection"])
        ignore_source = p.name.startswith("._") or "__MACOSX" in p.parts or c.get("roster_excluded", False)
        unchanged = (old and old.size == st.st_size and old.mtime_ns == str(st.st_mtime_ns)
                     and old.status == ("ignored" if ignore_source else "indexed"))
        row.classification = c
        row.scene_key, row.title, row.collection = c["scene_key"], c["title"], c["collection"]
        if not unchanged:
            row.sha256 = ""
            row.asset_id = None  # previously prepared assets remain valid for saved programs
            row.size, row.mtime_ns = st.st_size, str(st.st_mtime_ns)
            row.error = ""
            if ignore_source:
                row.status = "ignored"
            else:
                try:
                    row.technical = ffmpeg.probe(p)
                    if not row.technical["has_video"]:
                        raise ValueError("No video stream")
                    row.status = "indexed"
                except Exception as e:
                    row.status, row.error = "error", str(e)[:1200]
        row.updated_at = now_ms()
        if row.technical.get("height", 0) > row.technical.get("width", 0):
            row.classification = {**row.classification, "orientation": "vertical", "content_rotation": 0}
        return row

    seen = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i, row in enumerate(pool.map(inspect, files)):
            seen.add(row.relative_path)
            counts[{"indexed": "readable", "ignored": "ignored"}.get(row.status, "errors")] += 1
            with session_scope() as s:
                s.merge(row)
                s.commit()
            if i % 20 == 0:
                job_update(jid, progress=(i + 1) / max(len(files), 1), detail=counts.copy())
    with session_scope() as s:
        for relative, row in existing.items():
            if relative not in seen:
                row.status, row.error = "missing", "Source file is no longer available"
                counts["missing"] += 1
                s.merge(row)
        s.commit()
    job_update(jid, detail=counts, progress=1)


def make_preview(sid: str, video: bool = True):
    import cv2
    import numpy as np
    with session_scope() as s:
        row = s.get(SourceFile, sid)
        if not row or row.status != "indexed":
            raise ValueError("Source is unavailable")
        path, meta, info = source_path(row), effective(row), row.technical
    cap = cv2.VideoCapture(str(path))
    samples = []
    try:
        for ratio in (0.08, 0.2, 0.4, 0.6, 0.8, 0.92):
            t = info.get("duration", 0) * ratio
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            angle = meta.get("content_rotation", 0)
            if angle in (90, 180, 270):
                frame = cv2.rotate(frame, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[angle])
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            score = float(np.mean(gray > 24))
            h, w = frame.shape[:2]
            image = cv2.resize(frame, (max(1, round(w * min(320 / w, 180 / h))), max(1, round(h * min(320 / w, 180 / h)))))
            tile = np.zeros((180, 320, 3), dtype=np.uint8)
            ih, iw = image.shape[:2]
            tile[(180-ih)//2:(180-ih)//2+ih, (320-iw)//2:(320-iw)//2+iw] = image
            samples.append((score, t, tile))
    finally:
        cap.release()
    if not samples:
        raise ValueError("Cannot decode preview frames")
    best = max(samples, key=lambda x: x[0])
    cv2.imwrite(str(LIBRARY_DIR / f"{sid}.jpg"), best[2])
    tiles = [x[2] for x in samples]
    tiles += [np.zeros_like(tiles[0])] * (6 - len(tiles))
    cv2.imwrite(str(LIBRARY_DIR / f"{sid}-sheet.jpg"), np.vstack((np.hstack(tiles[:3]), np.hstack(tiles[3:]))))
    if not video:
        return
    rotation = {90: "transpose=1,", 180: "hflip,vflip,", 270: "transpose=2,"}.get(meta.get("content_rotation", 0), "")
    ffmpeg._run([ffmpeg.FFMPEG, "-y", "-v", "error", "-ss", str(max(0, best[1]-1)), "-i", str(path), "-t", "5", "-vf", rotation + "scale=480:480:force_original_aspect_ratio=decrease,fps=15", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-movflags", "+faststart", str(LIBRARY_DIR / f"{sid}.mp4")], 120)


def prepare(sid: str, profile: str = "source") -> str:
    from .pipeline import pipeline, default_preset, sha256_file
    from ..models import new_id
    with session_scope() as s:
        row = s.get(SourceFile, sid)
        if not row or row.status != "indexed":
            raise ValueError("Source is unavailable; scan the library first")
        if row.asset_id:
            old = s.get(MediaAsset, row.asset_id)
            if old and old.status != "error" and old.preset.get("profile", "source") == profile:
                return old.id
        path, meta = source_path(row), effective(row)
        digest = row.sha256 or sha256_file(path)
        # Hash confirms identity; matching names or sizes alone never merge files.
        others = s.exec(select(SourceFile).where(SourceFile.sha256 == digest)).all()
        preset = {**default_preset(), "fps": 30 if profile == "compatibility" else 0,
                  "content_rotation": meta.get("content_rotation", 0), "profile": profile, "mirror": False}
        for other in others:
            candidate = s.get(MediaAsset, other.asset_id) if other.asset_id else None
            if candidate and candidate.preset == preset and candidate.status != "error":
                row.sha256, row.asset_id = digest, candidate.id
                s.add(row); s.commit()
                return candidate.id
        aid = new_id()
        is_pack = row.relative_path.startswith("@packs/")
        a = MediaAsset(id=aid, title=meta.get("title", row.title), source="source-pack" if is_pack else "atmosfx", source_file_id=sid,
                       license="User supplied" if is_pack else "Personal use only", original_path="", preset=preset,
                       tags=sorted(set(meta["targets"] + [meta["appearance"], meta["character"], row.collection]) - {""}),
                       season=meta["season"], technical=row.technical)
        row.sha256, row.asset_id = digest, aid
        s.add(a); s.add(row); s.commit()
    pipeline.enqueue(aid)
    return aid


async def build_complete_roster(jid: str, program_id: str, source_prefix: str = "", source_prefixes: list[str] | None = None):
    """Prepare canonical catalogue actions and atomically install the finished roster."""
    from .roster import build_scheduler, preparation_sources, select_actions
    from ..models import Program
    with session_scope() as s:
        rows = list(s.exec(select(SourceFile).where(SourceFile.status == "indexed")).all())
        prefixes = source_prefixes or ([source_prefix] if source_prefix else [])
        if prefixes:
            rows = [row for row in rows if any(row.relative_path.startswith(prefix) for prefix in prefixes)]
        program = s.get(Program, program_id)
        if not program:
            raise ValueError("Program not found")
    actions = select_actions(rows, include_all=bool(prefixes))
    if not actions:
        raise ValueError("This pack has no playable video scenes")
    sources = preparation_sources(actions)
    asset_ids: dict[str, str] = {}
    failures: list[dict] = []
    for index, row in enumerate(sources):
        try:
            asset_ids[row.id] = await asyncio.to_thread(prepare, row.id, "native")
        except Exception as exc:
            failures.append({"source_id": row.id, "error": str(exc)[:300]})
        job_update(jid, progress=.4 * (index + 1) / max(1, len(sources)), detail={
            "phase": "queueing", "actions": len(actions), "sources": len(sources),
            "queued": index + 1, "ready": 0, "errors": failures,
        })
    pending = set(asset_ids.values())
    while pending:
        await asyncio.sleep(.5)
        with session_scope() as s:
            records = {a.id: a for a in s.exec(select(MediaAsset).where(MediaAsset.id.in_(pending))).all()}
        for aid in list(pending):
            record = records.get(aid)
            if record and record.status == "ready": pending.remove(aid)
            elif record and record.status == "error":
                pending.remove(aid); failures.append({"asset_id": aid, "error": record.error[:300]})
        ready = len(asset_ids) - len(pending) - sum(1 for x in failures if "asset_id" in x)
        job_update(jid, progress=.4 + .5 * (len(asset_ids) - len(pending)) / max(1, len(asset_ids)), detail={
            "phase": "preparing", "actions": len(actions), "sources": len(sources),
            "queued": len(asset_ids), "ready": ready, "errors": failures,
        })
    with session_scope() as s:
        program = s.get(Program, program_id)
        ready_ids = {a.id for a in s.exec(select(MediaAsset).where(MediaAsset.id.in_(set(asset_ids.values())), MediaAsset.status == "ready")).all()}
        canonical_assets = {sid: aid for sid, aid in asset_ids.items() if aid in ready_ids}
        previous = next((item for item in program.items if item.get("type") == "scheduler"), {})
        scheduler = build_scheduler(actions, canonical_assets, previous)
        items = list(program.items)
        replaced = False
        for i, item in enumerate(items):
            if item.get("type") == "scheduler": items[i] = scheduler; replaced = True; break
        if not replaced: items.append(scheduler)
        program.items, program.updated_at = items, now_ms()
        s.add(program); s.commit()
    job_update(jid, progress=.95, detail={"phase": "installed", "actions": len(actions),
               "sources": len(sources), "characters": len(scheduler["roster"]),
               "families": len(scheduler["groups"]), "errors": failures})
    from ..sync.runner import runner
    from ..sync.hub import hub
    await hub.changed("programs")
    if runner.program and runner.program.get("id") == program_id:
        await runner.reload()
    # Movement analysis is additive and can continue after the complete roster
    # is already available for ordinary playback.
    with session_scope() as s:
        analyzed = {x.source_file_id for x in s.exec(select(MovementSegment).where(MovementSegment.analyzer_version == 2)).all()}
    missing = [a.canonical.id for a in actions if a.canonical.id not in analyzed]
    if missing:
        worker.submit("motion", {"source_ids": missing})


class LibraryWorker:
    def __init__(self):
        self.tasks = set()
        self.loop = None

    def submit(self, kind: str, detail: dict | None = None) -> LibraryJob:
        detail = detail or {}
        with session_scope() as s:
            for old in s.exec(select(LibraryJob).where(LibraryJob.status.in_(["queued", "running"]))).all():
                if old.kind == kind and old.detail.get("source_id") == detail.get("source_id") and old.detail.get("profile") == detail.get("profile"):
                    return old
            job = LibraryJob(kind=kind, detail=detail)
            s.add(job); s.commit(); s.refresh(job)
        task = asyncio.create_task(self.run(job.id))
        self.tasks.add(task); task.add_done_callback(self.tasks.discard)
        return job

    async def run(self, jid):
        from ..sync.hub import hub
        with session_scope() as s:
            job = s.get(LibraryJob, jid)
        job_update(jid, status="running")
        try:
            if job.kind == "scan":
                await asyncio.to_thread(scan, jid)
                self.submit("thumbnails")
            elif job.kind == "thumbnails":
                with session_scope() as s:
                    ids = [r.id for r in s.exec(select(SourceFile).where(SourceFile.status == "indexed")).all() if not (LIBRARY_DIR / f"{r.id}.jpg").exists()]
                failures = []
                async def preview(sid):
                    try: await asyncio.to_thread(make_preview, sid, False)
                    except Exception as e: failures.append({"source_id":sid,"error":str(e)[:300]})
                for i in range(0, len(ids), 2):
                    await asyncio.gather(*(preview(sid) for sid in ids[i:i+2]))
                    job_update(jid, progress=min(i+2,len(ids))/max(1,len(ids)), detail={"completed":min(i+2,len(ids)),"total":len(ids),"errors":failures})
                job_update(jid, detail={"completed":len(ids),"total":len(ids),"errors":failures})
            elif job.kind == "preview":
                await asyncio.to_thread(make_preview, job.detail["source_id"])
            elif job.kind == "prepare":
                if job.detail.get("profile") == "interpolated60":
                    from .interpolation import prepare_interpolated
                    await prepare_interpolated(jid,job.detail["source_id"])
                else:
                    aid = await asyncio.to_thread(prepare, job.detail["source_id"], job.detail.get("profile", "source"))
                    job_update(jid, detail={**job.detail, "asset_id": aid})
            elif job.kind == "motion":
                await asyncio.to_thread(analyze_motion, jid, job.detail.get("source_ids"))
                # A running scheduler owns an in-memory Layout snapshot. Reload
                # it so newly analyzed directions affect the next horizon.
                from ..sync.runner import runner
                if runner.program:
                    await runner.reload()
            elif job.kind == "complete_roster":
                await build_complete_roster(jid, job.detail["program_id"], job.detail.get("source_prefix", ""), job.detail.get("source_prefixes"))
            job_update(jid, status="done", progress=1)
        except Exception as e:
            job_update(jid, status="error", error=str(e)[:2000])
        await hub.changed("library")

    def resume(self):
        self.loop = asyncio.get_running_loop()
        with session_scope() as s:
            ids = [j.id for j in s.exec(select(LibraryJob).where(LibraryJob.status.in_(["queued", "running"]))).all()]
        for jid in ids:
            task = asyncio.create_task(self.run(jid))
            self.tasks.add(task); task.add_done_callback(self.tasks.discard)


worker = LibraryWorker()
