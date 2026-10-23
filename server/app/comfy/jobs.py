"""Workflow templates, parameter mapping, and job execution with auto-import."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
from pathlib import Path
from typing import Any, Optional

from sqlmodel import col, select

from ..api.system import get_setting
from ..config import COMFY_TEMPLATES_DIR, WORK_DIR
from ..db import session_scope
from ..media.pipeline import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT, pipeline
from ..models import ComfyJob, ComfyWorkflow
from .client import ComfyClient, ComfyError, output_files

log = logging.getLogger(__name__)
OUT_DIR = WORK_DIR / "downloads" / "comfy"
POLL_S = 2.0
TIMEOUT_S = 3 * 3600


def comfy_url() -> str:
    with session_scope() as s:
        return (get_setting(s, "comfy_url") or "").strip()


def client() -> ComfyClient:
    url = comfy_url()
    if not url:
        raise ComfyError("ComfyUI URL is not set (Settings)")
    return ComfyClient(url)


def seed_builtins() -> None:
    """Loads bundled templates; updates existing builtins by name."""
    with session_scope() as s:
        for path in sorted(COMFY_TEMPLATES_DIR.glob("*.json")):
            try:
                t = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log.warning("bad comfy template %s: %s", path.name, e)
                continue
            row = s.exec(select(ComfyWorkflow).where(ComfyWorkflow.name == t["name"], ComfyWorkflow.builtin == True)).first()  # noqa: E712
            if row is None:
                row = ComfyWorkflow(name=t["name"], builtin=True)
            row.kind = t.get("kind", "video")
            row.description = t.get("description", "")
            row.workflow = t["workflow"]
            row.params = t.get("params", [])
            s.add(row)
        s.commit()


def is_api_format(wf: Any) -> bool:
    return isinstance(wf, dict) and bool(wf) and all(isinstance(v, dict) and "class_type" in v for v in wf.values())


def apply_params(workflow: dict, params: list[dict], values: dict[str, Any]) -> tuple[dict, dict[str, Any]]:
    """Returns (prompt, resolved values). Seeds < 0 or empty are randomized."""
    wf = copy.deepcopy(workflow)
    resolved: dict[str, Any] = {}
    for p in params:
        v = values.get(p["name"], p.get("default"))
        t = p.get("type", "string")
        if t == "seed":
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = -1
            if v < 0:
                v = random.randint(0, 2**48)
        elif t == "int":
            v = int(float(v))
        elif t == "float":
            v = float(v)
        elif t == "image" and not v:
            raise ValueError(f"'{p.get('label', p['name'])}' needs an image")
        resolved[p["name"]] = v
        for target in [{"node": p["node"], "input": p["input"]}, *p.get("also", [])]:
            node = wf.get(str(target["node"]))
            if node is None:
                raise ValueError(f"param '{p['name']}' maps to missing node {target['node']}")
            node.setdefault("inputs", {})[target["input"]] = v
    return wf, resolved


def _title(wf: ComfyWorkflow, values: dict) -> str:
    text = values.get("prompt") or values.get("tags") or wf.name
    text = str(text).split(",")[0].strip()
    return (text[:60] + "…") if len(text) > 60 else text


class JobRunner:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}

    def submit(self, job_id: str) -> None:
        self.tasks[job_id] = asyncio.create_task(self._run(job_id))

    def resume_pending(self) -> None:
        """Re-attach to jobs that were queued/running when the server stopped."""
        with session_scope() as s:
            ids = [j.id for j in s.exec(select(ComfyJob).where(col(ComfyJob.status).in_(["queued", "running"]))).all()]
        for jid in ids:
            self.submit(jid)

    def _update(self, job_id: str, **fields) -> None:
        with session_scope() as s:
            j = s.get(ComfyJob, job_id)
            if not j:
                return
            for k, v in fields.items():
                setattr(j, k, v)
            s.add(j)
            s.commit()

    async def _changed(self) -> None:
        from ..sync.hub import hub

        await hub.changed("comfy_jobs")

    async def _run(self, job_id: str) -> None:
        from ..sync.hub import hub

        with session_scope() as s:
            job = s.get(ComfyJob, job_id)
            wf = s.get(ComfyWorkflow, job.workflow_id) if job else None
            if not job or not wf:
                return
            job_data = job.model_dump()
            wf_name, wf_obj = wf.name, ComfyWorkflow(**wf.model_dump())
        try:
            c = client()
            prompt_id = job_data["prompt_id"]
            if not prompt_id:
                prompt, resolved = apply_params(wf_obj.workflow, wf_obj.params, job_data["params"])
                prompt_id = await c.queue(prompt)
                self._update(job_id, prompt_id=prompt_id, status="running", params=resolved)
                await self._changed()
            history = await self._wait(c, job_id, prompt_id)
            status = (history.get("status") or {}).get("status_str")
            if status == "error":
                msgs = [m[1].get("exception_message", "") for m in (history.get("status") or {}).get("messages", []) if m[0] == "execution_error"]
                raise ComfyError(msgs[0] if msgs else "ComfyUI reported an error")
            files = output_files(history)
            if not files:
                raise ComfyError("workflow finished but produced no saved outputs (add a SaveVideo/SaveAudio node)")
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            asset_ids = []
            with session_scope() as s:
                j = s.get(ComfyJob, job_id)
                params = j.params if j else {}
            for f in files:
                ext = Path(f["filename"]).suffix.lower()
                if ext not in VIDEO_EXT | AUDIO_EXT | IMAGE_EXT:
                    continue
                dst = OUT_DIR / f"{job_id}_{Path(f['filename']).name}"
                await c.download(f, dst)
                asset = await asyncio.to_thread(
                    pipeline.import_file,
                    dst,
                    title=_title(wf_obj, params),
                    source="comfyui",
                    source_url="",
                    license="Generated",
                    attribution=f"ComfyUI · {wf_name}",
                    season=job_data["season"],
                    tags=sorted(set(job_data["tags"]) | {"generated"}),
                )
                asset_ids.append(asset.id)
            self._update(job_id, status="done", progress=1.0, asset_ids=asset_ids)
            await hub.toast(f"Generated {len(asset_ids)} clip(s); processing in the library")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("comfy job %s failed: %s", job_id, e)
            self._update(job_id, status="error", error=str(e)[:2000])
            await hub.toast(f"Generation failed: {e}", error=True)
        finally:
            self.tasks.pop(job_id, None)
            await self._changed()

    async def _wait(self, c: ComfyClient, job_id: str, prompt_id: str) -> dict:
        """Follows websocket progress when available; always confirms via /history."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TIMEOUT_S

        async def follow_ws() -> None:
            last = 0.0
            async for msg in c.events():
                data = msg.get("data") or {}
                if data.get("prompt_id") not in (None, prompt_id):
                    continue
                if msg.get("type") == "progress" and data.get("max"):
                    p = float(data["value"]) / float(data["max"])
                    if abs(p - last) >= 0.05:
                        last = p
                        self._update(job_id, progress=round(p, 3), status="running")
                        await self._changed()
                elif msg.get("type") == "executing" and data.get("node") is None and data.get("prompt_id") == prompt_id:
                    return
                elif msg.get("type") in ("execution_error", "execution_interrupted"):
                    return

        ws_task = asyncio.create_task(follow_ws())
        try:
            while loop.time() < deadline:
                # History entries only appear once a prompt has finished (or failed).
                h = await c.history(prompt_id)
                if h:
                    return h
                done, _ = await asyncio.wait({ws_task}, timeout=POLL_S)
                if done:
                    # Websocket finished or dropped; fall back to plain polling.
                    if not ws_task.cancelled() and ws_task.exception():
                        log.info("comfy websocket closed: %s", ws_task.exception())
                    await asyncio.sleep(0.5)
                    ws_task = asyncio.create_task(asyncio.sleep(TIMEOUT_S))
            raise ComfyError("timed out waiting for ComfyUI")
        finally:
            ws_task.cancel()

    async def cancel(self, job_id: str) -> None:
        with session_scope() as s:
            j = s.get(ComfyJob, job_id)
            prompt_id = j.prompt_id if j else ""
        if prompt_id:
            try:
                await client().cancel(prompt_id)
            except Exception as e:
                log.info("comfy cancel failed: %s", e)
        t = self.tasks.pop(job_id, None)
        if t:
            t.cancel()
        self._update(job_id, status="error", error="cancelled")
        await self._changed()


runner = JobRunner()


def workflow_nodes(wf: dict) -> list[dict]:
    """Literal (non-link) inputs per node, for mapping custom workflow parameters."""
    out = []
    for nid, node in wf.items():
        inputs = {k: v for k, v in (node.get("inputs") or {}).items() if not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str))}
        out.append({"id": nid, "class_type": node.get("class_type", ""), "title": (node.get("_meta") or {}).get("title", ""), "inputs": inputs})
    return sorted(out, key=lambda n: (len(n["id"]), n["id"]))


def guess_params(wf: dict) -> list[dict]:
    """Best-effort parameter mapping for an uploaded workflow."""
    params: list[dict] = []
    texts = [n for n in workflow_nodes(wf) if "text" in n["inputs"] and isinstance(n["inputs"]["text"], str)]
    for i, n in enumerate(texts[:2]):
        params.append({"name": "prompt" if i == 0 else "negative", "label": "Prompt" if i == 0 else "Negative prompt", "type": "text", "default": n["inputs"]["text"], "node": n["id"], "input": "text"})
    for n in workflow_nodes(wf):
        for key in ("seed", "noise_seed"):
            if key in n["inputs"] and not any(p["type"] == "seed" for p in params):
                params.append({"name": "seed", "label": "Seed", "type": "seed", "default": -1, "node": n["id"], "input": key})
        if n["class_type"] == "LoadImage" and not any(p["type"] == "image" for p in params):
            params.append({"name": "image", "label": "Image", "type": "image", "default": "", "node": n["id"], "input": "image"})
    return params


def create_job(workflow_id: str, values: dict, season: str, tags: list[str]) -> ComfyJob:
    with session_scope() as s:
        wf = s.get(ComfyWorkflow, workflow_id)
        if not wf:
            raise ValueError("workflow not found")
        apply_params(wf.workflow, wf.params, values)  # validate before queueing
        job = ComfyJob(workflow_id=workflow_id, params=values, season=season, tags=tags)
        s.add(job)
        s.commit()
        s.refresh(job)
        return job


def get_job(job_id: str) -> Optional[ComfyJob]:
    with session_scope() as s:
        return s.get(ComfyJob, job_id)
