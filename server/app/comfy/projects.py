"""Durable, approval-gated projection generation; one GPU submission at a time."""
import asyncio
import json
import random
import time
from pathlib import Path

import httpx
from sqlmodel import select
from ..config import GENERATION_DIR, MEDIA_DIR
from ..db import session_scope
from ..models import GenerationProject, StoryboardShot, GenerationTake, Approval, MediaAsset, Setting
from .jobs import client
from .client import output_files
from .projection_workflows import VERSION, image_graph, video_graph

GPU_LOCK = asyncio.Lock()

def setting(key, default=""):
    from ..api.system import get_setting
    with session_scope() as s:
        return get_setting(s, key, default)


async def capabilities():
    result = {"version": VERSION, "image": {}, "ltx": {}, "wan": {}}
    try:
        async with client()._http(15) as h:
            r = await h.get("/object_info"); r.raise_for_status(); schema = r.json()
    except Exception as e:
        return {**result, **{k: {"ready": False, "error": f"ComfyUI unavailable: {e}"} for k in ("image", "ltx", "wan")}}
    for key in ("image", "ltx", "wan"):
        try:
            graph = image_graph(schema, "Dependency check", 1, "reference.png") if key == "image" else video_graph(schema, "Dependency check", 1, "reference.png", 5, key)
            result[key] = {"ready": True, "nodes": len(graph), "error": ""}
        except ValueError as e: result[key] = {"ready": False, "error": str(e)}
    return result


def current_take(t, project, shot=None):
    return t.project_revision == project.revision and (not shot or t.revision == shot.revision)


def approved(s, t):
    return bool(s.exec(select(Approval).where(Approval.take_id == t.id, Approval.project_revision == t.project_revision, Approval.shot_revision == t.revision, Approval.decision == "approved")).first())


def assert_dependencies(s, t):
    p = s.get(GenerationProject, t.project_id)
    sh = s.get(StoryboardShot, t.shot_id) if t.shot_id else None
    if not p or not current_take(t, p, sh): raise ValueError("This take belongs to an earlier revision; regenerate from the current storyboard")
    if t.stage != "character":
        ref = s.get(GenerationTake, p.character_take_id) if p.character_take_id else None
        if not ref or not approved(s, ref) or not current_take(ref, p): raise ValueError("Approve a current character image first")
        if t.provenance.get("character_take_id") != ref.id: raise ValueError("Character selection changed; regenerate this take")
    if t.stage == "video":
        ref = s.get(GenerationTake, sh.image_take_id) if sh and sh.image_take_id else None
        if not ref or not approved(s, ref) or not current_take(ref, p, sh): raise ValueError("Approve the current starting image before animation")
        assert_dependencies(s, ref)
        if t.provenance.get("image_take_id") != ref.id: raise ValueError("Starting image changed; regenerate animation")


async def plan_project(project):
    async with GPU_LOCK:
        # Do not contend with Advanced ComfyUI jobs or retain their GPU models.
        c = client()
        async with c._http(15) as h:
            queue = (await h.get("/queue")).json()
            if queue.get("queue_running") or queue.get("queue_pending"):
                raise ValueError("Wait for the active ComfyUI work before running the local planner")
            await h.post("/free", json={"unload_models": True, "free_memory": True})
        try: return await _plan_project(project)
        finally:
            # The supplied llama.cpp launcher unloads after five idle seconds.
            await asyncio.sleep(6)


async def _plan_project(project):
    url = setting("planner_url", "http://127.0.0.1:8081/v1")
    model = setting("planner_model", "local")
    instructions = ('Return a JSON object with character_design (appearance of ONE isolated creature only, no story, windows, scenery, panels, labels, or actions) and a shots array of 4-8 creature encounter beats. Each shot has name, role (entrance/action/idle/exit), prompt, sound, seconds (5 or 10), surface (A/B/C), handoff (darkness/travel/effect). '
                    'Default to 5 seconds; use 10 only for a deliberately slow action. Maintain one character, fixed camera, full body within frame. Describe practical visible actions and corresponding sounds. Begin with an entrance, end with an exit, include movement between windows. A reappearance is an entrance; dissolving is an exit, not an idle loop. No markdown.')
    async with httpx.AsyncClient(timeout=600) as c:
        deadline = time.monotonic()+600
        while True:
            r = await c.post(url.rstrip("/") + "/chat/completions", json={"model": model, "messages": [{"role":"system", "content":instructions}, {"role":"user", "content":f"Concept: {project.concept}\nStyle: {project.style}\nBackground: {project.background}"}], "temperature":0.7, "max_tokens":3000, "response_format":{"type":"json_object"}})
            if r.status_code != 503 or time.monotonic() >= deadline: break
            await asyncio.sleep(5)
        r.raise_for_status()
        payload = json.loads(r.json()["choices"][0]["message"]["content"])
    shots = payload.get("shots", [])
    if not isinstance(shots, list) or not 1 <= len(shots) <= 16: raise ValueError("Planner returned invalid storyboard")
    output = []
    for i, item in enumerate(shots):
        if not isinstance(item, dict) or not isinstance(item.get("prompt"), str): raise ValueError("Planner returned an invalid shot")
        output.append(StoryboardShot(project_id=project.id, position=i, name=str(item.get("name", f"Beat {i+1}"))[:200],
            prompt=item["prompt"][:4000], sound=str(item.get("sound", ""))[:2000], seconds=10 if item.get("seconds") == 10 else 5,
            role=item.get("role") if item.get("role") in ("entrance","action","idle","exit") else "action",
            surface=str(item.get("surface", "A"))[:40], handoff=item.get("handoff") if item.get("handoff") in ("darkness","travel","effect") else "darkness"))
    return {"shots":output,"character_design":str(payload.get("character_design", ""))[:3000]}


def create_take(s, project, shot, stage, backend="ltx", motion_prompt="", set_name="", sequence_index=0, travel_direction="", travel_phase=""):
    take = GenerationTake(project_id=project.id, shot_id=shot.id if shot else None, stage=stage,
        revision=shot.revision if shot else project.revision, project_revision=project.revision,
        provenance={"version":VERSION, "seed":random.randrange(2**48), "backend":backend,
            "character_take_id":project.character_take_id, "image_take_id":shot.image_take_id if shot else None,
            "concept":project.concept, "character_design":project.character_design, "orientation":project.orientation,
            "style":project.style, "background":(shot.background if shot else "") or project.background,
            "prompt":shot.prompt if shot else project.concept, "sound":shot.sound if shot else "",
            "seconds":shot.seconds if shot else 5, "role":shot.role if shot else "character",
            "handoff":shot.handoff if shot else "darkness", "motion_prompt":motion_prompt,
            "set_name":set_name, "sequence_index":sequence_index,
            "travel_direction":travel_direction,"travel_phase":travel_phase})
    assert_dependencies(s, take)
    s.add(take); s.commit(); s.refresh(take)
    return take


class ProjectWorker:
    def __init__(self):
        self.wake = asyncio.Event()
        self.last_family = None
        self.last_finished = 0.0

    async def release_models(self):
        try:
            async with client()._http(15) as h:
                q = (await h.get("/queue")).json()
                if not q.get("queue_running") and not q.get("queue_pending"):
                    await h.post("/free",json={"unload_models":True,"free_memory":True})
        except Exception: pass

    def update(self, tid, **fields):
        with session_scope() as s:
            row = s.get(GenerationTake, tid)
            for key, value in fields.items(): setattr(row, key, value)
            s.add(row); s.commit()

    async def reference(self, cid):
        with session_scope() as s:
            t = s.get(GenerationTake, cid)
            f = next((f for f in t.outputs if f.get("local") and Path(f["local"]).suffix.lower() in (".png",".jpg",".webp")), None)
        if not f: raise ValueError("Approved reference image is missing")
        p = GENERATION_DIR / f["local"]
        return await client().upload_image(p.read_bytes(), f"projector-{cid}{p.suffix}")

    async def execute(self, t):
        started = time.monotonic()
        c = client()
        if t.status == "submitting" and not t.prompt_id:
            raise ValueError("Submission interrupted before its ID was saved. Check ComfyUI history before explicitly retrying; no duplicate job was submitted.")
        with session_scope() as s: assert_dependencies(s, t)
        if not t.prompt_id:
            async with c._http(60) as h:
                response = await h.get("/object_info"); response.raise_for_status(); schema = response.json()
            v = t.provenance
            background = "Pure black background, no floor or scenery, isolated illuminated creature." if v["background"] == "black" else "Dim containment enclosure behind window glass, background stays very dark."
            design = v.get("character_design") or v["concept"]
            action = "Neutral standing design reference pose." if t.stage == "character" else (v.get("motion_prompt") or v["prompt"])
            prompt = f"{design}. {v['style']}. {action}. {background} Static locked camera, consistent creature identity and scale, entire subject within frame, generous dark margins."
            if t.stage == "video":
                prompt = (f"Animate the supplied approved starting image. {action}. {v['style']}. {background} "
                          "Preserve the supplied character's identity, anatomy, palette and light direction. "
                          "Keep the camera locked, maintain the subject's scale and leave generous dark margins.")
                prompt += f" Sound: {v['sound']}."
                travel = bool(v.get("travel_direction"))
                if travel:
                    prompt += " The creature performs its body motion in place, stays fully visible and illuminated through the last frame. The lateral travel across the window is added after animation."
                if not travel and v.get("role") == "entrance" and v.get("handoff") == "darkness":
                    prompt += " The subject stays clearly illuminated and visible throughout this take; the entrance fade from black is applied after animation."
                if not travel and v.get("role") == "exit": prompt += " The creature vanishes into darkness."
                if not travel and v.get("handoff") == "effect":
                    if v.get("role") == "exit": prompt += " The creature dissolves completely into curling smoke and disappears by the last frame."
                    elif v.get("role") == "entrance": prompt += " Curling smoke clears to reveal the illuminated creature, which remains visible at the end."
                    else: prompt += " Curling smoke accompanies the action."
                    prompt += " Keep the smoke inside the safe margins; include a soft whoosh and its decay."
                prompt += " Only the described environmental sounds; no speech or music."
                if not travel and v.get("handoff") == "travel": prompt += " Move consistently from left to right, entering at the left margin and leaving at the right margin, without moving the camera."
                ref = await self.reference(v["image_take_id"])
                graph = video_graph(schema, prompt, v["seed"], ref, v["seconds"], v["backend"], v.get("orientation","vertical"))
            else:
                if t.stage == "character": prompt += " Single full-body character design reference in a neutral pose. One creature only; do not illustrate the sequence, multiple windows, panels, letters, or labels."
                else:
                    # The reference supplies identity. Repeating a character brief
                    # containing a neutral pose can override the storyboard action.
                    prompt = (f"Edit the supplied character reference into this single starting pose: {action} "
                              f"{v['style']}. {background} Preserve the reference character's anatomy, face, "
                              "palette and light direction. Change only its pose and the requested effects. "
                              "Show one still moment before the action begins, with a locked camera. "
                              "Keep the entire character including all fabric inside the central 80 percent "
                              "of the image height, leaving clear black space above and below it. "
                              "No collage, panels, text or additional figures.")
                ref = await self.reference(v["character_take_id"]) if t.stage == "image" else None
                graph = image_graph(schema, prompt, v["seed"], ref, v.get("orientation","vertical"))
            self.update(t.id, workflow=graph, status="submitting")
            pid = await c.queue(graph)
            self.update(t.id, prompt_id=pid, status="running")
            t.prompt_id = pid
        history = None
        deadline = time.monotonic() + 7200
        while not history:
            with session_scope() as s:
                live = s.get(GenerationTake, t.id)
                if live.status == "cancelled":
                    await c.cancel(t.prompt_id)
                    return
            history = await c.history(t.prompt_id)
            if time.monotonic() > deadline: raise ValueError("Generation timed out; inspect ComfyUI history before retrying")
            if not history: await asyncio.sleep(2)
        if history.get("status", {}).get("status_str") == "error":
            raise ValueError(str(history.get("status", {}).get("messages", "ComfyUI execution failed"))[-2000:])
        files = output_files(history)
        if not files: raise ValueError("Workflow produced no saved output")
        downloaded = []
        for i, file in enumerate(files):
            suffix = Path(file["filename"]).suffix.lower()
            if suffix not in (".png",".jpg",".webp",".mp4",".webm"): continue
            name = f"{t.id}-{i}{suffix}"
            dest = GENERATION_DIR / name
            if not dest.is_file():
                partial = dest.with_suffix(dest.suffix + ".partial")
                await c.download(file, partial)
                partial.replace(dest)
            downloaded.append({**file, "local":name, "url":f"/api/generation/takes/{t.id}/files/{len(downloaded)}"})
        self.update(t.id, outputs=downloaded, elapsed_seconds=t.elapsed_seconds + time.monotonic()-started)
        if t.stage == "video":
            from ..media.pipeline import pipeline
            from ..media import ffmpeg
            # Only interpolated output can enter the 60 FPS finishing path.
            delivery = next((f for f in downloaded if "delivery120" in f["filename"]), None)
            if not delivery: raise ValueError("Interpolated delivery output missing; refusing duplicated-frame 60 FPS delivery")
            with session_scope() as s:
                saved = s.get(GenerationTake, t.id)
                aid = saved.asset_id
            if not aid:
                info = await asyncio.to_thread(ffmpeg.probe, GENERATION_DIR / delivery["local"])
                native = next((f for f in downloaded if "native24" in f["filename"]), None)
                if not native: raise ValueError("Native take missing; cannot verify original duration")
                native_info = await asyncio.to_thread(ffmpeg.probe, GENERATION_DIR / native["local"])
                if abs(info["fps"]-120) > 0.1 or not info["has_audio"]: raise ValueError("Delivery must contain interpolated 120 FPS video and synchronized audio")
                asset = await asyncio.to_thread(pipeline.import_file, GENERATION_DIR / delivery["local"], title=t.provenance["prompt"][:80], source="generated-project", move=False, published=False,
                    preset={"fps":60,"max_height":720,"mirror":False,"black_crush":8,"profile":"generated60","loudnorm":True,"safe_margin":0.05,"source_duration":native_info["duration"],
                            "travel_direction":t.provenance.get("travel_direction",""),"travel_phase":t.provenance.get("travel_phase",""),
                            "fade_in":0.35 if t.provenance.get("role") == "entrance" and not t.provenance.get("travel_direction") else 0,
                            "fade_out":0.5 if t.provenance.get("role") == "exit" and not t.provenance.get("travel_direction") else 0},
                    technical={"native_fps":24,"interpolation":"RIFE 5x → 120 → 60","take_id":t.id}, asset_id="gen" + t.id)
                aid = asset.id
                self.update(t.id, asset_id=aid, status="processing")
            while True:
                with session_scope() as s: asset = s.get(MediaAsset, aid)
                if asset.status == "error": raise ValueError(asset.error)
                if asset.status == "ready": break
                if time.monotonic() > deadline: raise ValueError("Finishing timed out")
                await asyncio.sleep(2)
            if abs(asset.technical.get("delivery", {}).get("fps", 0)-60) > 0.1: raise ValueError("Finished video is not 60 FPS")
        with session_scope() as s:
            latest = s.get(GenerationTake, t.id)
            if latest.status == "cancelled": return
        self.update(t.id, status="done", elapsed_seconds=t.elapsed_seconds+time.monotonic()-started)

    async def run(self):
        from ..sync.hub import hub
        while True:
            with session_scope() as s:
                t = s.exec(select(GenerationTake).where(GenerationTake.status.in_(["queued","running","submitting","processing"])).order_by(GenerationTake.created_at)).first()
            if not t:
                if self.last_family and time.monotonic()-self.last_finished > 300:
                    async with GPU_LOCK: await self.release_models()
                    self.last_family = None
                await asyncio.sleep(2); continue
            try:
                async with GPU_LOCK:
                    family = "image" if t.stage in ("character","image") else t.provenance.get("backend","ltx")
                    # A server restart need not discard ComfyUI's warm cache.
                    # Explicitly release models on observed stage changes;
                    # ComfyUI manages memory for the first submission itself.
                    if self.last_family is not None and self.last_family != family: await self.release_models()
                    self.last_family = family
                    await self.execute(t)
            except asyncio.CancelledError: raise
            except Exception as e:
                with session_scope() as s: latest=s.get(GenerationTake,t.id)
                if latest.status != "cancelled": self.update(t.id, status="error", error=str(e)[:2000])
                await self.release_models()
            finally: self.last_finished = time.monotonic()
            await hub.changed("generation")


worker = ProjectWorker()
