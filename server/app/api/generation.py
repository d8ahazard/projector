from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from ..db import get_session
from ..config import GENERATION_DIR
from ..models import GenerationProject, StoryboardShot, GenerationTake, Approval, MediaAsset, Program, new_id
from ..comfy import projects
from ..sync.hub import hub

router = APIRouter(prefix="/api/generation", tags=["generation"])


@router.get("/capabilities")
async def capabilities():
    return await projects.capabilities()


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    concept: str = Field(default="", max_length=6000)
    character_design: str = Field(default="", max_length=3000)
    orientation: str = "vertical"
    style: str = Field(default="Cinematic practical creature effects, consistent anatomy and lighting", max_length=2000)
    background: str = "black"


class ShotIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(default="", max_length=4000)
    background: str = ""
    sound: str = Field(default="", max_length=2000)
    seconds: int = 5
    role: str = "action"
    surface: str = "A"
    handoff: str = "darkness"


def get(s, cls, id):
    row = s.get(cls, id)
    if not row: raise HTTPException(404, "Record not found")
    return row


def check_shot(body):
    if body.background not in ("", "black", "enclosure"): raise HTTPException(400, "Unknown shot background")
    if body.seconds not in (5,10) or body.role not in ("entrance","action","idle","exit") or body.handoff not in ("darkness","travel","effect"):
        raise HTTPException(400, "Invalid shot duration, role, or handoff")


@router.get("/projects")
def list_projects(session: Session = Depends(get_session)):
    return session.exec(select(GenerationProject).order_by(GenerationProject.created_at.desc())).all()


@router.post("/projects")
async def create_project(body: ProjectIn, session: Session = Depends(get_session)):
    if body.orientation not in ("vertical","horizontal"): raise HTTPException(400, "Unknown orientation")
    if body.background not in ("black","enclosure"): raise HTTPException(400, "Unknown background treatment")
    p = GenerationProject(**body.model_dump()); session.add(p); session.commit(); session.refresh(p)
    await hub.changed("generation")
    return p


@router.get("/projects/{pid}")
def detail(pid: str, session: Session = Depends(get_session)):
    p = get(session, GenerationProject, pid)
    shots = session.exec(select(StoryboardShot).where(StoryboardShot.project_id == pid).order_by(StoryboardShot.position)).all()
    takes = session.exec(select(GenerationTake).where(GenerationTake.project_id == pid).order_by(GenerationTake.created_at.desc())).all()
    rows = []
    for t in takes:
        sh = next((sh for sh in shots if sh.id == t.shot_id), None)
        a = session.get(MediaAsset, t.asset_id) if t.asset_id else None
        valid = True
        try: projects.assert_dependencies(session, t)
        except ValueError: valid = False
        rows.append({**t.model_dump(exclude={"workflow"}), "approved":projects.approved(session,t), "current":valid and projects.current_take(t,p,sh), "asset":a})
    return {"project":p, "shots":shots, "takes":rows}


@router.put("/projects/{pid}")
async def edit_project(pid: str, body: ProjectIn, session: Session = Depends(get_session)):
    if body.orientation not in ("vertical","horizontal"): raise HTTPException(400, "Unknown orientation")
    p = get(session, GenerationProject, pid)
    if body.background not in ("black","enclosure"): raise HTTPException(400, "Unknown background")
    changed = any(getattr(p,k) != v for k,v in body.model_dump().items() if k != "name")
    for k,v in body.model_dump().items(): setattr(p,k,v)
    if changed: p.revision += 1; p.character_take_id = None
    session.add(p); session.commit()
    await hub.changed("generation")
    return detail(pid, session)


@router.post("/projects/{pid}/plan")
async def plan(pid: str, session: Session = Depends(get_session)):
    p = get(session, GenerationProject, pid)
    try: proposal = await projects.plan_project(p)
    except Exception as e: raise HTTPException(502, f"Local planner failed: {str(e) or type(e).__name__}. Check the local service and its endpoint in Settings; cold model loading may take several minutes. You can also add beats manually.")
    # Append a new storyboard rather than destroy reviewed work.
    shots = proposal["shots"]
    if not p.character_design and proposal.get("character_design"):
        p.character_design=proposal["character_design"]; p.revision+=1; p.character_take_id=None; session.add(p)
    previous = session.exec(select(StoryboardShot).where(StoryboardShot.project_id == pid)).all()
    offset = max((s.position for s in previous), default=-1) + 1
    for sh in shots: sh.position += offset; session.add(sh)
    session.commit(); await hub.changed("generation")
    return detail(pid, session)


@router.post("/projects/{pid}/shots")
async def add_shot(pid: str, body: ShotIn, session: Session = Depends(get_session)):
    get(session, GenerationProject, pid); check_shot(body)
    old = session.exec(select(StoryboardShot).where(StoryboardShot.project_id == pid)).all()
    sh = StoryboardShot(project_id=pid, position=max((s.position for s in old),default=-1)+1, **body.model_dump())
    session.add(sh); session.commit(); session.refresh(sh); await hub.changed("generation")
    return sh


@router.put("/shots/{sid}")
async def edit_shot(sid: str, body: ShotIn, session: Session = Depends(get_session)):
    sh = get(session, StoryboardShot, sid); check_shot(body)
    if any(getattr(sh,k) != v for k,v in body.model_dump().items()):
        sh.revision += 1; sh.image_take_id = None
    for k,v in body.model_dump().items(): setattr(sh,k,v)
    session.add(sh); session.commit(); session.refresh(sh); await hub.changed("generation")
    return sh


@router.post("/projects/{pid}/render")
async def render(pid: str, body: dict, session: Session = Depends(get_session)):
    p = get(session, GenerationProject, pid)
    stage = body.get("stage", "character")
    if stage not in ("character","image","video"): raise HTTPException(400,"Unknown generation stage")
    if stage == "character" and not p.character_design.strip():
        raise HTTPException(409, "Describe the isolated character's appearance, or run the planner to propose it, before generating a reference")
    backend = body.get("backend", "ltx")
    if backend not in ("ltx","wan"): raise HTTPException(400,"Unknown backend")
    motion_prompt = body.get("motion_prompt", "")
    if not isinstance(motion_prompt, str) or len(motion_prompt) > 4000 or (motion_prompt and stage != "video"):
        raise HTTPException(400,"Motion overrides are limited to 4000 characters and animation takes")
    sh = get(session, StoryboardShot, body.get("shot_id")) if stage != "character" else None
    if sh and sh.project_id != pid: raise HTTPException(400,"Shot belongs to a different project")
    set_name, index = body.get("set_name",""), body.get("sequence_index", sh.position if sh else 0)
    direction, phase = body.get("travel_direction",""), body.get("travel_phase","")
    if not isinstance(set_name,str) or len(set_name)>120 or type(index) is not int or not 0<=index<=100:
        raise HTTPException(400,"Invalid take set name or sequence index")
    if direction or phase:
        if stage != "video" or direction not in ("left","right") or phase not in ("enter","exit"):
            raise HTTPException(400,"Travel is an animation option with left/right direction and enter/exit phase")
        if ((sh.background if sh else "") or p.background) != "black":
            raise HTTPException(400,"Window travel requires an isolated black background")
    try: take = projects.create_take(session,p,sh,stage,backend,motion_prompt,set_name,index,direction,phase)
    except ValueError as e: raise HTTPException(409,str(e))
    await hub.changed("generation")
    return take


@router.post("/takes/{tid}/approve")
async def approve(tid: str, session: Session = Depends(get_session)):
    t = get(session, GenerationTake, tid)
    if t.status != "done": raise HTTPException(409,"Take is not ready for review")
    try: projects.assert_dependencies(session,t)
    except ValueError as e: raise HTTPException(409,str(e))
    if not projects.approved(session,t): session.add(Approval(take_id=t.id,project_revision=t.project_revision,shot_revision=t.revision))
    if t.stage == "character":
        p = get(session,GenerationProject,t.project_id); p.character_take_id=t.id; session.add(p)
    elif t.stage == "image":
        sh=get(session,StoryboardShot,t.shot_id); sh.image_take_id=t.id; session.add(sh)
    session.commit(); await hub.changed("generation")
    return {"ok":True}


@router.post("/takes/{tid}/publish")
async def publish(tid: str, session: Session = Depends(get_session)):
    t=get(session,GenerationTake,tid)
    try: projects.assert_dependencies(session,t)
    except ValueError as e: raise HTTPException(409,str(e))
    if t.stage != "video" or t.status != "done" or not projects.approved(session,t): raise HTTPException(409,"Approve the finished video with sound before publishing")
    a=get(session,MediaAsset,t.asset_id)
    if a.status != "ready": raise HTTPException(409,"Finishing has not completed")
    a.published=True; session.add(a); session.commit(); session.refresh(a); await hub.changed("media"); await hub.changed("generation")
    return a


@router.post("/takes/{tid}/cancel")
async def cancel(tid: str, session: Session = Depends(get_session)):
    t=get(session,GenerationTake,tid)
    if t.status not in ("queued","running","processing"): raise HTTPException(409,"Take cannot be cancelled in its current state")
    t.status="cancelled"; session.add(t); session.commit(); await hub.changed("generation")
    return {"ok":True}


@router.post("/takes/{tid}/retry")
async def retry(tid: str, session: Session = Depends(get_session)):
    t = get(session, GenerationTake, tid)
    if t.status != "error": raise HTTPException(409, "Only failed stages can be retried")
    try: projects.assert_dependencies(session, t)
    except ValueError as e: raise HTTPException(409, str(e))
    if t.prompt_id:
        try: history = await projects.client().history(t.prompt_id)
        except Exception as e: raise HTTPException(502, f"Cannot inspect previous job: {e}")
        if history and history.get("status", {}).get("status_str") == "error":
            t.provenance = {**t.provenance, "previous_prompt_ids": [*t.provenance.get("previous_prompt_ids", []), t.prompt_id]}
            t.prompt_id = None
    if t.asset_id:
        a = get(session, MediaAsset, t.asset_id)
        if a.status == "error":
            from ..media.pipeline import pipeline
            a.status = "pending"; a.error = ""; session.add(a); session.commit()
            pipeline.enqueue(a.id)
    t.status = "queued"; t.error = ""; session.add(t); session.commit()
    await hub.changed("generation")
    return t


@router.get("/takes/{tid}/files/{index}")
def take_file(tid: str, index: int, session: Session = Depends(get_session)):
    t=get(session,GenerationTake,tid)
    if index < 0 or index >= len(t.outputs): raise HTTPException(404)
    path=(GENERATION_DIR / t.outputs[index]["local"]).resolve()
    if GENERATION_DIR not in path.parents or not path.is_file(): raise HTTPException(404)
    return FileResponse(path)


@router.post("/projects/{pid}/encounter")
async def encounter(pid: str, body: dict, session: Session = Depends(get_session)):
    from ..compiler import compile_cue_preview
    from ..loader import load_layout
    p=get(session,GenerationProject,pid)
    mapping=body.get("surfaces", {})
    tracks=[]; cursor=0.0
    shots=session.exec(select(StoryboardShot).where(StoryboardShot.project_id == pid).order_by(StoryboardShot.position)).all()
    set_name=body.get("set_name", "")
    all_takes=session.exec(select(GenerationTake).where(GenerationTake.project_id == pid, GenerationTake.stage == "video").order_by(GenerationTake.created_at.desc())).all()
    groups={}
    if set_name:
        for t in all_takes:
            if t.provenance.get("set_name", "") == set_name:
                groups.setdefault(t.provenance.get("sequence_index",0),[]).append(t)
    else:
        # Keep the original encounter stable when alternative travel sets exist.
        groups={sh.position:[t for t in all_takes if t.shot_id==sh.id and not t.provenance.get("set_name")] for sh in shots}
    for index,takes in sorted(groups.items()):
        chosen=None; chosen_take=None
        for t in takes:
            try: projects.assert_dependencies(session,t)
            except ValueError: continue
            a=session.get(MediaAsset,t.asset_id) if t.asset_id else None
            if t.status == "done" and projects.approved(session,t) and a and a.published and a.status == "ready": chosen=a; chosen_take=t; break
        if not chosen: raise HTTPException(409,f"Publish a current approved take for step {index+1}")
        sh=get(session,StoryboardShot,chosen_take.shot_id)
        surface=mapping.get(sh.surface)
        if not surface: raise HTTPException(400,f"Map storyboard window {sh.surface} to a layout surface")
        travel=bool(chosen_take.provenance.get("travel_direction"))
        tracks.append({"surface_id":surface,"asset_id":chosen.id,"at":cursor,"audio":True,"handoff":"travel" if travel else sh.handoff,
                       "transition":{"type":"cut" if travel else "wipe" if sh.handoff=="travel" else "dip","duration":0 if travel else 0.5}})
        cursor += chosen.duration_ms/1000 + (0 if travel else 0.7 if sh.handoff=="darkness" else 0.2)
    if not tracks: raise HTTPException(400,"Storyboard has no shots")
    name=f"{p.name} — {set_name}" if set_name else p.name
    item={"type":"encounter","id":new_id(),"name":name,"tracks":tracks,"hold":1,"clips":[],"target":{"mode":"all"},"pattern":"together"}
    try: compile_cue_preview(item,load_layout(session))
    except ValueError as e: raise HTTPException(400,str(e))
    program=Program(name=name,items=[item]); session.add(program); session.commit(); session.refresh(program)
    await hub.changed("programs")
    return program
