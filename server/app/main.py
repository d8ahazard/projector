import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import discovery
from .api import (
    calibration,
    catalog,
    comfy,
    devices,
    layout,
    media,
    programs,
    system,
    library,
    generation,
    setup,
)
from .comfy import jobs as comfy_jobs
from .config import MEDIA_DIR, LEGACY_MEDIA_DIR, WEB_DIST, CALIBRATION_DIR
from .media.library import worker as library_worker
from .comfy.projects import worker as project_worker
from .db import init_db
from .media.pipeline import pipeline
from .sync import ws
from .sync.runner import runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    setup.recover()
    comfy_jobs.seed_builtins()
    comfy_jobs.runner.resume_pending()
    library_worker.resume()
    tasks = [
        asyncio.create_task(pipeline.run()),
        asyncio.create_task(pipeline.watch_import_folder()),
        asyncio.create_task(runner.run()),
        asyncio.create_task(project_worker.run()),
    ]
    await asyncio.to_thread(discovery.start)
    try:
        yield
    finally:
        await asyncio.to_thread(discovery.stop)
        for t in tasks:
            t.cancel()
        for t in library_worker.tasks:
            t.cancel()


app = FastAPI(title="Projector Server", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (system, layout, devices, media, programs, calibration, catalog, comfy, library, generation, setup):
    app.include_router(r.router)
app.include_router(media.credits_router)
app.include_router(system.download_router)
app.include_router(ws.router)

@app.get("/files/media/{path:path}")
async def media_file(path: str):
    from fastapi import HTTPException
    for root in (MEDIA_DIR, LEGACY_MEDIA_DIR):
        candidate = (root / path).resolve()
        if root.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
    raise HTTPException(404)


@app.get("/files/source/{source_id}")
async def source_file(source_id: str):
    """Serve an indexed original directly to a player without copying or transcoding it."""
    from fastapi import HTTPException
    from .db import session_scope
    from .models import SourceFile
    from .media.library import source_path
    with session_scope() as session:
        row = session.get(SourceFile, source_id)
        if not row or row.status != "indexed":
            raise HTTPException(404, "Source is unavailable")
        path = source_path(row)
    return FileResponse(path, media_type="video/mp4")

app.mount("/files/calibration", StaticFiles(directory=CALIBRATION_DIR), name="calibration")

if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = (WEB_DIST / path).resolve()
        if path and candidate.is_file() and WEB_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
