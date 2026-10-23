"""On-demand AtmosFX interpolation, bounded chunks, restartable ComfyUI jobs.

No source writes. Audio is remuxed once from the original after interpolation.
Scene cuts are segment boundaries so optical flow never crosses a detected cut.
"""
import asyncio
import math
import re
import time
from pathlib import Path
from sqlmodel import select
from ..config import WORK_DIR
from ..db import session_scope
from ..models import SourceFile, LibraryJob, MediaAsset
from . import ffmpeg
from .library import source_path, effective, job_update
from .pipeline import pipeline


def boundaries(path, duration, fps):
    out = ffmpeg._run([ffmpeg.FFMPEG, "-v", "error", "-i", str(path), "-vf", "scale=160:90,select='gt(scene,0.3)',metadata=print:file=-", "-an", "-f", "null", "-"], 1800)
    cuts = sorted({0, *[round(float(x)*fps) for x in re.findall(r"pts_time:([0-9.]+)", out)], round(duration*fps)})
    spans = []
    chunk = max(1, round(fps*4))
    for start, end in zip(cuts, cuts[1:]):
        for first in range(start, end, chunk):
            count = min(chunk, end-first)
            spans.append({"first":first, "count":count, "extra":int(first+count < end)})
    return spans


async def prepare_interpolated(jid, sid):
    from ..comfy.projects import GPU_LOCK
    from ..comfy.jobs import client
    from ..comfy.client import output_files
    from ..comfy.projection_workflows import interpolation_graph, VERSION
    started = time.monotonic()
    with session_scope() as s:
        row = s.get(SourceFile, sid); job = s.get(LibraryJob, jid)
        if not row or row.status != "indexed": raise ValueError("Source unavailable; rescan the catalogue")
        path, meta, info, detail = source_path(row), effective(row), row.technical, dict(job.detail)
        previous = s.get(MediaAsset,row.asset_id) if row.asset_id else None
        if previous and previous.status == "ready" and previous.preset.get("profile") == "interpolated60":
            job_update(jid,detail={**detail,"asset_id":previous.id,"reused":True})
            return
    fps = info.get("fps", 0)
    if not 20 <= fps <= 31: raise ValueError("Interpolation currently accepts 20–31 FPS source video")
    multiplier = 5 if fps < 26 else 2
    folder = WORK_DIR / "interpolation" / jid
    folder.mkdir(parents=True, exist_ok=True)
    if "spans" not in detail:
        detail.update(spans=await asyncio.to_thread(boundaries, path, info["duration"], fps), version=VERSION, chunks={}, rotation=meta.get("content_rotation", 0))
        job_update(jid, detail=detail)
    chunks = dict(detail.get("chunks", {}))
    c = client()
    async with GPU_LOCK:
        async with c._http(30) as h:
            queue = (await h.get("/queue")).json()
            if not queue.get("queue_running") and not queue.get("queue_pending"):
                await h.post("/free",json={"unload_models":True,"free_memory":True})
            r = await h.get("/object_info"); r.raise_for_status(); schema = r.json()
        interpolation_graph(schema, "reference.mp4", fps, multiplier)
        for i, span in enumerate(detail["spans"]):
            final = folder / f"part-{i:04}.mp4"
            if final.is_file() and chunks.get(str(i), {}).get("done"): continue
            state = dict(chunks.get(str(i), {}))
            if state.get("submitting") and not state.get("prompt_id"):
                raise ValueError(f"Chunk {i+1} submission was interrupted before its ID was saved; inspect ComfyUI history before creating a new preparation job")
            if not state.get("prompt_id"):
                part = folder / f"input-{i:04}.mp4"
                rotate = {90:"transpose=1,",180:"hflip,vflip,",270:"transpose=2,"}.get(detail["rotation"], "")
                await asyncio.to_thread(ffmpeg._run, [ffmpeg.FFMPEG,"-y","-v","error","-ss",str(span["first"]/fps),"-i",str(path),"-frames:v",str(span["count"]+span["extra"]),"-vf",rotate+ffmpeg._even_scale(720),"-an","-c:v","libx264","-preset","fast","-crf","16",str(part)])
                if span["count"] + span["extra"] < 2:
                    duration=(round((span["first"]+span["count"])/fps*60)-round(span["first"]/fps*60))/60
                    await asyncio.to_thread(ffmpeg._run,[ffmpeg.FFMPEG,"-y","-v","error","-i",str(part),"-vf","tpad=stop_mode=clone:stop_duration=0.1,fps=60","-t",str(duration),"-an","-c:v","libx264","-preset","fast","-crf","16",str(final)])
                    chunks[str(i)]={"done":True,"single_frame_cut":True}; detail["chunks"]=chunks; job_update(jid,detail=detail)
                    continue
                async with c._http(300) as h:
                    with part.open("rb") as f:
                        r = await h.post("/upload/image",files={"image":(f"projector-{jid}-{i}.mp4",f,"video/mp4")},data={"overwrite":"true"})
                        r.raise_for_status(); uploaded = r.json()
                filename = "/".join(filter(None,[uploaded.get("subfolder"),uploaded["name"]]))
                graph = interpolation_graph(schema, filename, fps, multiplier)
                state.update(submitting=True, workflow=graph)
                chunks[str(i)] = state; detail["chunks"] = chunks; job_update(jid,detail=detail)
                state.update(prompt_id=await c.queue(graph),submitting=False)
                chunks[str(i)] = state; job_update(jid,detail=detail)
            deadline = time.monotonic()+3600
            history = await c.history(state["prompt_id"])
            while not history:
                if time.monotonic()>deadline: raise ValueError("Interpolation timed out; retry resumes the recorded job")
                await asyncio.sleep(2); history = await c.history(state["prompt_id"])
            if history.get("status",{}).get("status_str") == "error":
                state.update(previous_prompt_ids=[*state.get("previous_prompt_ids",[]),state["prompt_id"]],prompt_id=None)
                chunks[str(i)]=state; job_update(jid,detail=detail)
                raise ValueError(f"Interpolation chunk {i+1} failed: {history.get('status',{}).get('messages')}")
            files = output_files(history)
            output = next((f for f in files if f["filename"].endswith(".mp4")), None)
            if not output: raise ValueError("Interpolation produced no video")
            raw = folder / f"interpolated-{i:04}.mp4"
            await c.download(output,raw)
            # Round cumulative boundaries, not each chunk duration, to avoid drift.
            duration = (round((span["first"]+span["count"])/fps*60)-round(span["first"]/fps*60))/60
            await asyncio.to_thread(ffmpeg._run,[ffmpeg.FFMPEG,"-y","-v","error","-i",str(raw),"-vf","tpad=stop_mode=clone:stop_duration=0.1,fps=60","-t",str(duration),"-an","-c:v","libx264","-preset","fast","-crf","16","-fps_mode","cfr",str(final)])
            state["done"] = True; chunks[str(i)] = state
            detail.update(chunks=chunks,completed=i+1,total=len(detail["spans"]),elapsed_seconds=time.monotonic()-started)
            job_update(jid,detail=detail,progress=(i+1)/max(1,len(detail["spans"])))
        async with c._http() as h: await h.post("/free",json={"unload_models":True,"free_memory":True})
    manifest = folder / "concat.txt"
    manifest.write_text("".join(f"file 'part-{i:04}.mp4'\n" for i in range(len(detail["spans"]))),encoding="utf-8")
    joined = folder / "interpolated60.mp4"
    # Original audio and its tails stay aligned to the original timebase.
    await asyncio.to_thread(ffmpeg._run,[ffmpeg.FFMPEG,"-y","-v","error","-f","concat","-safe","0","-i",str(manifest),"-i",str(path),"-map","0:v:0","-map","1:a:0?","-c:v","copy","-c:a","aac","-t",str(info["duration"]),"-movflags","+faststart",str(joined)])
    asset = await asyncio.to_thread(pipeline.import_file,joined,move=False,asset_id="lib"+jid,title=meta.get("title",row.title)+" (60 FPS interpolated)",source="atmosfx",license="Personal use only",season=meta.get("season",""),tags=meta.get("targets",[]),preset={"fps":60,"profile":"interpolated60","mirror":False,"content_rotation":0},technical={"native_fps":fps,"interpolation":f"RIFE {multiplier}x → {fps*multiplier:.3f} → 60; cuts segmented","source_file_id":sid,"native_source":info})
    with session_scope() as s:
        row = s.get(SourceFile,sid); row.asset_id=asset.id; s.add(row); s.commit()
    detail.update(asset_id=asset.id,elapsed_seconds=time.monotonic()-started)
    job_update(jid,detail=detail)
