"""Background catalog indexing and add-to-library downloads."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from sqlmodel import select

from ..api.system import get_setting
from ..config import WORK_DIR
from ..db import session_scope
from ..media.pipeline import pipeline
from ..models import CatalogItem, now_ms
from .sources import AUDIO_SEEDS, SEED_QUERIES, SOURCES, UA, resolve_archive

log = logging.getLogger(__name__)
DOWNLOAD_DIR = WORK_DIR / "downloads"
MAX_DOWNLOAD_BYTES = 2 * 1024**3


def _keys() -> dict:
    with session_scope() as s:
        return {k: get_setting(s, k) for k in ("pexels_api_key", "pixabay_api_key", "freesound_api_key")}


def configured_sources() -> dict[str, bool]:
    keys = _keys()
    return {name: (req is None or bool(keys.get(req))) for name, (_, _, req) in SOURCES.items()}


def upsert(items: list[dict]) -> int:
    """Insert or refresh items; keeps asset links and merges tags. Returns count of new rows."""
    new = 0
    with session_scope() as s:
        for it in items:
            row = s.exec(select(CatalogItem).where(CatalogItem.source == it["source"], CatalogItem.source_id == it["source_id"])).first()
            if row is None:
                s.add(CatalogItem(**it))
                new += 1
            else:
                tags = sorted(set(row.tags or []) | set(it.get("tags", [])))
                for k, v in it.items():
                    if k == "tags" or (v in ("", 0, None) and getattr(row, k)):
                        continue
                    setattr(row, k, v)
                row.tags = tags
                if not row.season and it.get("season"):
                    row.season = it["season"]
                row.indexed_at = now_ms()
                s.add(row)
        s.commit()
    return new


class Indexer:
    def __init__(self) -> None:
        self.task: Optional[asyncio.Task] = None
        self.status: dict = {"running": False, "done": 0, "total": 0, "added": 0, "errors": [], "current": "", "finished_at": None}
        self.downloads: dict[int, dict] = {}

    def start(self, seasons: list[str], sources: list[str], queries: list[str], kinds: list[str]) -> bool:
        if self.task and not self.task.done():
            return False
        self.task = asyncio.create_task(self._run(seasons, sources, queries, kinds))
        return True

    async def _run(self, seasons: list[str], sources: list[str], queries: list[str], kinds: list[str]) -> None:
        from ..sync.hub import hub

        keys = _keys()
        enabled = configured_sources()
        jobs: list[tuple[str, str, str, str]] = []  # source, query, season, kind
        for src in sources:
            if src not in SOURCES or not enabled.get(src):
                continue
            _, src_kinds, _ = SOURCES[src]
            for kind in kinds:
                if kind not in src_kinds:
                    continue
                if queries:
                    for q in queries:
                        jobs.append((src, q, seasons[0] if len(seasons) == 1 else "", kind))
                else:
                    for season in seasons:
                        seeds = AUDIO_SEEDS if kind == "audio" else SEED_QUERIES
                        for q in seeds.get(season, []):
                            jobs.append((src, q, season, kind))
        self.status = {"running": True, "done": 0, "total": len(jobs), "added": 0, "errors": [], "current": "", "finished_at": None}
        await hub.changed("catalog_status")
        last_push = 0.0
        async with httpx.AsyncClient(timeout=30, headers=UA, follow_redirects=True) as client:
            for src, q, season, kind in jobs:
                self.status["current"] = f"{src}: {q} ({kind})"
                fn = SOURCES[src][0]
                try:
                    items = await fn(client, q, season, kind, keys)
                    self.status["added"] += await asyncio.to_thread(upsert, items)
                except Exception as e:  # one bad source/query shouldn't stop the run
                    log.warning("catalog %s %r failed: %s", src, q, e)
                    if len(self.status["errors"]) < 20:
                        self.status["errors"].append(f"{src} '{q}': {e}")
                self.status["done"] += 1
                if time.monotonic() - last_push > 2:
                    last_push = time.monotonic()
                    await hub.changed("catalog_status")
                    await hub.changed("catalog")
                await asyncio.sleep(0.4)  # be polite to free APIs
        self.status.update(running=False, current="", finished_at=now_ms())
        await hub.changed("catalog_status")
        await hub.changed("catalog")
        await hub.toast(f"Catalog indexing finished: {self.status['added']} new items")

    # ------------------------------------------------------------ downloads

    def add(self, item_id: int, tags: list[str], season: str) -> None:
        if self.downloads.get(item_id, {}).get("state") == "downloading":
            return
        self.downloads[item_id] = {"state": "downloading", "progress": 0.0, "error": ""}
        asyncio.create_task(self._download(item_id, tags, season))

    async def _download(self, item_id: int, tags: list[str], season: str) -> None:
        from ..sync.hub import hub

        st = self.downloads[item_id]
        with session_scope() as s:
            item = s.get(CatalogItem, item_id)
            if not item:
                st.update(state="error", error="not found")
                return
            data = item.model_dump()
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=120), headers=UA, follow_redirects=True) as client:
                url = data["media_url"]
                if data["source"] == "archive" and not url:
                    url = await resolve_archive(client, data["source_id"], data["kind"], MAX_DOWNLOAD_BYTES) or ""
                if not url:
                    raise RuntimeError("no downloadable file found")
                ext = Path(urlparse(url).path).suffix.lower() or (".mp3" if data["kind"] == "audio" else ".mp4")
                dst = DOWNLOAD_DIR / f"catalog_{item_id}{ext}"
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length") or 0)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError(f"file too large ({total // 1024**2} MB)")
                    got = 0
                    last = 0.0
                    with open(dst, "wb") as f:
                        async for chunk in r.aiter_bytes(1 << 16):
                            f.write(chunk)
                            got += len(chunk)
                            if got > MAX_DOWNLOAD_BYTES:
                                raise RuntimeError("file too large")
                            if total:
                                st["progress"] = got / total
                            if time.monotonic() - last > 1:
                                last = time.monotonic()
                                await hub.changed("catalog_downloads")
            asset = await asyncio.to_thread(
                pipeline.import_file,
                dst,
                title=data["title"],
                source=f"catalog:{data['source']}",
                source_url=data["page_url"],
                license=data["license"],
                attribution=data["author"],
                season=season or data["season"],
                tags=sorted(set(tags) | set(data["tags"][:6])),
            )
            with session_scope() as s:
                item = s.get(CatalogItem, item_id)
                if item:
                    item.asset_id = asset.id
                    if url and not item.media_url:
                        item.media_url = url
                    s.add(item)
                    s.commit()
            st.update(state="done", progress=1.0, asset_id=asset.id)
            await hub.toast(f"Added '{data['title']}' to the library; processing…")
        except Exception as e:
            log.warning("catalog download %s failed: %s", item_id, e)
            st.update(state="error", error=str(e))
            await hub.toast(f"Download failed: {e}", error=True)
        await hub.changed("catalog_downloads")
        await hub.changed("catalog")


indexer = Indexer()
