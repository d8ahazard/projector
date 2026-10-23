from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, col, select

from ..comfy import jobs
from ..comfy.client import ComfyError
from ..db import get_session
from ..models import ComfyJob, ComfyWorkflow
from ..sync.hub import hub

router = APIRouter(prefix="/api/comfy", tags=["comfy"])


class WorkflowIn(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    description: Optional[str] = None
    workflow: Optional[dict[str, Any]] = None
    params: Optional[list[dict[str, Any]]] = None


class JobIn(BaseModel):
    workflow_id: str
    params: dict[str, Any] = {}
    season: str = ""
    tags: list[str] = []
    count: int = 1


@router.get("/status")
async def status() -> dict:
    url = jobs.comfy_url()
    if not url:
        return {"configured": False, "ok": False, "url": ""}
    try:
        stats = await jobs.client().stats()
        devices = [{"name": d.get("name"), "vram_total": d.get("vram_total"), "vram_free": d.get("vram_free")} for d in stats.get("devices", [])]
        return {"configured": True, "ok": True, "url": url, "version": stats.get("system", {}).get("comfyui_version"), "devices": devices}
    except Exception as e:
        return {"configured": True, "ok": False, "url": url, "error": str(e)}


@router.get("/workflows")
def list_workflows(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(select(ComfyWorkflow).order_by(col(ComfyWorkflow.builtin).desc(), ComfyWorkflow.name)).all()
    return [{k: v for k, v in r.model_dump().items() if k != "workflow"} for r in rows]


@router.get("/workflows/{wid}")
def get_workflow(wid: str, session: Session = Depends(get_session)) -> dict:
    wf = session.get(ComfyWorkflow, wid)
    if not wf:
        raise HTTPException(404, "workflow not found")
    return {**wf.model_dump(), "nodes": jobs.workflow_nodes(wf.workflow)}


def _check_workflow(wf: Any) -> None:
    if isinstance(wf, dict) and "nodes" in wf and "links" in wf:
        raise HTTPException(400, "This is a UI-format workflow. In ComfyUI use Workflow > Export (API) and upload that file.")
    if not jobs.is_api_format(wf):
        raise HTTPException(400, "Not a ComfyUI API-format workflow")


@router.post("/workflows")
async def create_workflow(body: WorkflowIn, session: Session = Depends(get_session)) -> dict:
    _check_workflow(body.workflow)
    wf = ComfyWorkflow(
        name=body.name or "Custom workflow",
        kind=body.kind or "video",
        description=body.description or "",
        workflow=body.workflow,
        params=body.params if body.params is not None else jobs.guess_params(body.workflow or {}),
    )
    session.add(wf)
    session.commit()
    session.refresh(wf)
    await hub.changed("comfy_workflows")
    return {**wf.model_dump(), "nodes": jobs.workflow_nodes(wf.workflow)}


@router.put("/workflows/{wid}")
async def update_workflow(wid: str, body: WorkflowIn, session: Session = Depends(get_session)) -> dict:
    wf = session.get(ComfyWorkflow, wid)
    if not wf:
        raise HTTPException(404, "workflow not found")
    data = body.model_dump(exclude_none=True)
    if "workflow" in data:
        _check_workflow(data["workflow"])
    for k, v in data.items():
        setattr(wf, k, v)
    try:
        jobs.apply_params(wf.workflow, wf.params, {p["name"]: "placeholder.png" for p in wf.params if p.get("type") == "image"})
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, f"invalid parameter mapping: {e}")
    session.add(wf)
    session.commit()
    session.refresh(wf)
    await hub.changed("comfy_workflows")
    return {**wf.model_dump(), "nodes": jobs.workflow_nodes(wf.workflow)}


@router.delete("/workflows/{wid}", status_code=204)
async def delete_workflow(wid: str, session: Session = Depends(get_session)) -> None:
    wf = session.get(ComfyWorkflow, wid)
    if wf and wf.builtin:
        raise HTTPException(400, "built-in workflows can't be deleted")
    if wf:
        session.delete(wf)
        session.commit()
    await hub.changed("comfy_workflows")


@router.post("/upload-image")
async def upload_image(image: UploadFile = File(...)) -> dict:
    try:
        name = await jobs.client().upload_image(await image.read(), image.filename or "input.png")
    except ComfyError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"ComfyUI upload failed: {e}")
    return {"name": name}


@router.get("/jobs")
def list_jobs(limit: int = 50, session: Session = Depends(get_session)) -> list[ComfyJob]:
    return list(session.exec(select(ComfyJob).order_by(col(ComfyJob.created_at).desc()).limit(limit)).all())


@router.post("/jobs")
async def create_jobs(body: JobIn) -> list[ComfyJob]:
    if not jobs.comfy_url():
        raise HTTPException(400, "Set the ComfyUI URL in Settings first")
    created = []
    for i in range(max(1, min(body.count, 16))):
        values = dict(body.params)
        if i > 0:
            for k in list(values):
                if k == "seed":
                    values[k] = -1  # variations
        try:
            job = jobs.create_job(body.workflow_id, values, body.season, body.tags)
        except ValueError as e:
            raise HTTPException(400, str(e))
        jobs.runner.submit(job.id)
        created.append(job)
    await hub.changed("comfy_jobs")
    return created


@router.delete("/jobs/{jid}")
async def cancel_job(jid: str) -> dict:
    if not jobs.get_job(jid):
        raise HTTPException(404, "job not found")
    await jobs.runner.cancel(jid)
    return {"ok": True}
