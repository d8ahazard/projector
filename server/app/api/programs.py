import copy
from collections import defaultdict
from typing import Any, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..autofill import autofill
from ..compiler import compile_cue_preview, compile_preview
from ..db import get_session
from ..loader import load_layout
from ..models import MediaAsset, Program, Schedule, Setting, SourceFile, now_ms
from ..media.library import effective
from ..sync.hub import hub
from ..sync.runner import runner

router = APIRouter(prefix="/api", tags=["programs"])


class ProgramIn(BaseModel):
    name: Optional[str] = None
    loop_mode: Optional[str] = None
    seed: Optional[int] = None
    items: Optional[list[dict[str, Any]]] = None


class ScheduleIn(BaseModel):
    program_id: Optional[str] = None
    days: Optional[list[int]] = None
    start: Optional[str] = None
    end: Optional[str] = None
    enabled: Optional[bool] = None


class AutofillIn(BaseModel):
    name: str = "Auto program"
    hours: float = 4.0
    tags: list[str] = []
    season: str = ""
    patterns: Optional[list[str]] = None
    transitions: Optional[list[str]] = None
    seed: int = 1
    program_id: Optional[str] = None
    asset_ids: list[str] = []


class StartIn(BaseModel):
    program_id: str
    item_path: Optional[list[int]] = None


class JumpIn(BaseModel):
    item_path: list[int]


class OneShotIn(BaseModel):
    cue: dict[str, Any]
    immediate: bool = True


class ProjectorPreviewIn(BaseModel):
    device_ids: list[str]
    cue: Optional[dict[str, Any]] = None
    asset_id: Optional[str] = None


class MixProgramsIn(BaseModel):
    name: str = "After-dark mix"
    program_ids: list[str]
    mode: Literal["characters", "shows"] = "characters"


def _walk_items(items: list[dict]):
    for item in items:
        if item.get("type") == "block":
            yield from _walk_items(item.get("items", []))
        else:
            yield item


def _namespace_item(item: dict, prefix: str, source_program: str = "") -> dict:
    out = copy.deepcopy(item)
    if out.get("id"):
        out["id"] = f"{prefix}-{out['id']}"
    if source_program:
        out["source_program"] = source_program
    if out.get("type") == "block":
        out["items"] = [_namespace_item(child, prefix, source_program) for child in out.get("items", [])]
    return out


# ---------------------------------------------------------------- programs


@router.get("/programs")
def list_programs(session: Session = Depends(get_session)) -> list[Program]:
    return list(session.exec(select(Program).order_by(Program.name)).all())


@router.post("/programs")
async def create_program(body: ProgramIn, session: Session = Depends(get_session)) -> Program:
    p = Program(name=body.name or "New program", loop_mode=body.loop_mode or "loop", seed=body.seed or 1, items=body.items or [])
    session.add(p)
    session.commit()
    session.refresh(p)
    await hub.changed("programs")
    return p


@router.post("/programs/mix")
async def mix_programs(body: MixProgramsIn, session: Session = Depends(get_session)) -> Program:
    if len(set(body.program_ids)) < 2:
        raise HTTPException(400, "choose at least two programs")
    programs = []
    for pid in dict.fromkeys(body.program_ids):
        program = session.get(Program, pid)
        if not program:
            raise HTTPException(404, f"program {pid} not found")
        programs.append(program)

    if body.mode == "shows":
        items = []
        for program in programs:
            for item in program.items:
                namespaced = _namespace_item(item, program.id, program.name)
                namespaced["name"] = f"{program.name} · {namespaced.get('name') or 'Set'}"
                items.append(namespaced)
        mixed = Program(name=body.name.strip() or "Shuffled shows", loop_mode="shuffle_blocks", seed=1, items=items)
    else:
        roster = []
        groups = []
        included: set[str] = set()
        source_schedulers = []
        extras: dict[tuple[str, str], list[str]] = defaultdict(list)
        for program in programs:
            for item in _walk_items(program.items):
                if item.get("type") == "scheduler":
                    source_schedulers.append(item)
                    id_map: dict[str, str] = {}
                    scheduler_character_ids: list[str] = []
                    for character in item.get("roster", []):
                        clips = [aid for aid in character.get("clips", []) if aid not in included]
                        if not clips:
                            continue
                        included.update(clips)
                        old_id = str(character.get("id") or len(roster))
                        new_id = f"{program.id}-{old_id}"
                        id_map[old_id] = new_id
                        scheduler_character_ids.append(new_id)
                        roster.append({
                            "id": new_id,
                            "name": character.get("name") or program.name,
                            "clips": clips,
                            "source_program": program.name,
                        })
                    source_groups = item.get("groups") or []
                    if source_groups:
                        for source_group in source_groups:
                            character_ids = [id_map[str(cid)] for cid in source_group.get("character_ids", [])
                                             if str(cid) in id_map]
                            if not character_ids:
                                continue
                            group = {
                                "id": f"{program.id}-{source_group.get('id') or len(groups)}",
                                "name": source_group.get("name") or program.name,
                                "character_ids": character_ids,
                            }
                            if source_group.get("active_characters") is not None:
                                group["active_characters"] = source_group["active_characters"]
                            groups.append(group)
                    elif scheduler_character_ids:
                        groups.append({
                            "id": f"{program.id}-{item.get('id') or len(groups)}",
                            "name": program.name,
                            "character_ids": scheduler_character_ids,
                        })
                    continue
                asset_ids = list(item.get("clips", []))
                asset_ids += [track.get("asset_id") for track in item.get("tracks", []) if track.get("asset_id")]
                for aid in asset_ids:
                    if aid in included:
                        continue
                    asset = session.get(MediaAsset, aid)
                    if not asset or asset.status != "ready":
                        continue
                    character = ""
                    if asset.source_file_id:
                        source = session.get(SourceFile, asset.source_file_id)
                        if source:
                            character = effective(source).get("character", "")
                    extras[(program.id, character or program.name)].append(aid)
                    included.add(aid)
        extra_group_ids: dict[str, list[str]] = defaultdict(list)
        for (pid, character), clips in extras.items():
            character_id = f"{pid}-extra-{len(roster)}"
            roster.append({"id": character_id, "name": character, "clips": clips,
                           "source_program": next(p.name for p in programs if p.id == pid)})
            extra_group_ids[pid].append(character_id)
        for pid, character_ids in extra_group_ids.items():
            source_name = next(p.name for p in programs if p.id == pid)
            groups.append({"id": f"{pid}-extras", "name": source_name, "character_ids": character_ids})
        if not roster:
            raise HTTPException(400, "selected programs contain no ready character clips")
        gap_min = min((float(s.get("gap_min", 4)) for s in source_schedulers), default=4)
        gap_max = max((float(s.get("gap_max", 15)) for s in source_schedulers), default=15)
        rotation_min = min((float(s.get("rotation_gap_min", 10)) for s in source_schedulers), default=10)
        rotation_max = max((float(s.get("rotation_gap_max", 30)) for s in source_schedulers), default=30)
        scheduler = {
            "type": "scheduler", "id": "mixed-character-marathon", "name": "Mixed character marathon",
            "roster": roster, "target": {"mode": "all"},
            "recycle_min": min((float(s.get("recycle_min", 1.5)) for s in source_schedulers), default=1.5),
            "recycle_max": max((float(s.get("recycle_max", 2)) for s in source_schedulers), default=2),
            "gap_min": gap_min, "gap_max": gap_max,
            "rotation_gap_min": rotation_min, "rotation_gap_max": rotation_max,
            "active_characters": 0,
            "surface_buffer": min((float(s.get("surface_buffer", 1)) for s in source_schedulers), default=1),
            "fit": "contain", "transition": {"type": "dip", "duration": 0.8, "easing": "ease-in-out"},
            "motion_mode": "auto", "groups": groups,
            "source_program_ids": [p.id for p in programs],
        }
        mixed = Program(name=body.name.strip() or "After-dark mix", loop_mode="loop", seed=1, items=[scheduler])
    session.add(mixed)
    session.commit()
    session.refresh(mixed)
    await hub.changed("programs")
    return mixed


@router.get("/programs/{pid}")
def get_program(pid: str, session: Session = Depends(get_session)) -> Program:
    p = session.get(Program, pid)
    if not p:
        raise HTTPException(404, "program not found")
    return p


@router.put("/programs/{pid}")
async def update_program(pid: str, body: ProgramIn, session: Session = Depends(get_session)) -> Program:
    p = session.get(Program, pid)
    if not p:
        raise HTTPException(404, "program not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    p.updated_at = now_ms()
    session.add(p)
    session.commit()
    session.refresh(p)
    await hub.changed("programs")
    if runner.program and runner.program["id"] == pid:
        await runner.reload()
    return p


@router.delete("/programs/{pid}", status_code=204)
async def delete_program(pid: str, session: Session = Depends(get_session)) -> None:
    p = session.get(Program, pid)
    if p:
        session.delete(p)
        session.commit()
    await hub.changed("programs")


@router.post("/programs/{pid}/duplicate")
async def duplicate_program(pid: str, session: Session = Depends(get_session)) -> Program:
    p = session.get(Program, pid)
    if not p:
        raise HTTPException(404, "program not found")
    copy = Program(name=f"{p.name} copy", loop_mode=p.loop_mode, seed=p.seed, items=p.items)
    session.add(copy)
    session.commit()
    session.refresh(copy)
    await hub.changed("programs")
    return copy


@router.post("/programs/{pid}/sync-catalog")
async def sync_program_catalog(pid: str, session: Session = Depends(get_session)):
    """Fill a marathon from every distinct eligible catalogue action."""
    if not session.get(Program, pid):
        raise HTTPException(404, "program not found")
    from ..media.library import worker
    return worker.submit("complete_roster", {"program_id": pid})


@router.get("/programs/{pid}/preview")
def preview_program(pid: str, max_minutes: int = 120, session: Session = Depends(get_session)) -> dict:
    p = session.get(Program, pid)
    if not p:
        raise HTTPException(404, "program not found")
    return compile_preview(p.model_dump(), load_layout(session), max_minutes * 60_000)


@router.post("/preview/program")
def preview_program_body(body: ProgramIn, session: Session = Depends(get_session)) -> dict:
    prog = {"items": body.items or [], "loop_mode": body.loop_mode or "loop", "seed": body.seed or 1}
    return compile_preview(prog, load_layout(session))


@router.post("/preview/cue")
def preview_cue(cue: dict[str, Any], session: Session = Depends(get_session)) -> dict:
    try:
        return compile_cue_preview(cue, load_layout(session))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/programs/autofill")
async def autofill_program(body: AutofillIn, session: Session = Depends(get_session)) -> Program:
    layout = load_layout(session)
    if body.asset_ids:
        layout.assets = {k:v for k,v in layout.assets.items() if k in body.asset_ids}
    items = autofill(layout, body.hours, body.tags, body.season, body.patterns, body.transitions, body.seed)
    if not items:
        raise HTTPException(400, "no ready videos match those tags/season")
    if body.program_id:
        p = session.get(Program, body.program_id)
        if not p:
            raise HTTPException(404, "program not found")
        p.items = list(p.items) + items
        p.updated_at = now_ms()
    else:
        p = Program(name=body.name, loop_mode="loop", seed=body.seed, items=items)
    session.add(p)
    session.commit()
    session.refresh(p)
    await hub.changed("programs")
    return p


# ---------------------------------------------------------------- runner

@router.get("/programs/{pid}/preflight")
def check_preflight(pid: str, session: Session = Depends(get_session)):
    from ..preflight import preflight
    p=session.get(Program,pid)
    if not p: raise HTTPException(404,"Program not found")
    try: return preflight(p.model_dump(),session)
    except ValueError as e: raise HTTPException(400,str(e))


@router.get("/runner")
def runner_state() -> dict:
    return runner.public_state()


@router.post("/runner/start")
async def runner_start(body: StartIn) -> dict:
    try:
        await runner.start(body.program_id, body.item_path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return runner.public_state()


@router.post("/runner/force")
async def runner_force() -> dict:
    await runner.force_start()
    return runner.public_state()


@router.post("/runner/stop")
async def runner_stop() -> dict:
    await runner.stop()
    return runner.public_state()


@router.post("/runner/pause")
async def runner_pause() -> dict:
    await runner.pause()
    return runner.public_state()


@router.post("/runner/resume")
async def runner_resume() -> dict:
    await runner.resume()
    return runner.public_state()


@router.post("/runner/skip")
async def runner_skip() -> dict:
    await runner.skip()
    return runner.public_state()


@router.post("/runner/jump")
async def runner_jump(body: JumpIn) -> dict:
    await runner.jump(body.item_path)
    return runner.public_state()


@router.post("/runner/oneshot")
async def runner_oneshot(body: OneShotIn) -> dict:
    try:
        await runner.oneshot(body.cue, body.immediate)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return runner.public_state()


@router.post("/runner/preview")
async def runner_preview(body: ProjectorPreviewIn) -> dict:
    cue = body.cue
    if cue is None:
        if not body.asset_id:
            raise HTTPException(400, "asset_id or cue is required")
        cue = {
            "type": "cue", "id": "projector-preview", "name": "Clip preview",
            "clips": [body.asset_id], "target": {"mode": "list", "surfaces": []},
            "pattern": "together", "fit": "contain",
            "transition": {"type": "dip", "duration": 0.35, "easing": "ease-in-out"},
        }
    try:
        return await runner.preview(cue, body.device_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- schedules


@router.get("/schedules")
def list_schedules(session: Session = Depends(get_session)) -> dict:
    idle = session.get(Setting, "idle_program_id")
    return {"schedules": list(session.exec(select(Schedule)).all()), "idle_program_id": idle.value if idle else None}


@router.post("/schedules")
async def create_schedule(body: ScheduleIn, session: Session = Depends(get_session)) -> Schedule:
    if not body.program_id:
        raise HTTPException(400, "program_id required")
    s = Schedule(**body.model_dump(exclude_none=True))
    session.add(s)
    session.commit()
    session.refresh(s)
    await hub.changed("schedules")
    return s


@router.patch("/schedules/{sid}")
async def update_schedule(sid: str, body: ScheduleIn, session: Session = Depends(get_session)) -> Schedule:
    s = session.get(Schedule, sid)
    if not s:
        raise HTTPException(404, "schedule not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    session.add(s)
    session.commit()
    session.refresh(s)
    await hub.changed("schedules")
    return s


@router.delete("/schedules/{sid}", status_code=204)
async def delete_schedule(sid: str, session: Session = Depends(get_session)) -> None:
    s = session.get(Schedule, sid)
    if s:
        session.delete(s)
        session.commit()
    await hub.changed("schedules")


@router.put("/schedules/idle")
async def set_idle_program(body: dict, session: Session = Depends(get_session)) -> dict:
    st = session.get(Setting, "idle_program_id") or Setting(key="idle_program_id")
    st.value = body.get("program_id")
    session.add(st)
    session.commit()
    await hub.changed("schedules")
    return {"idle_program_id": st.value}
