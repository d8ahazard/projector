"""Media import + processing queue."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from sqlmodel import select

from ..config import IMPORT_DIR, MEDIA_DIR, LEGACY_MEDIA_DIR, ORIGINALS_DIR, RENDITIONS_DIR, THUMBS_DIR, LIBRARY_DIR
from ..db import session_scope
from ..models import MediaAsset, Setting, SourceFile, Rendition, new_id
from . import ffmpeg
from .suitability import analyze

log = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".ogv", ".wmv", ".ts", ".gif"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SEASON_WORDS = {
    "halloween": ("halloween", "ghost", "zombie", "skelet", "witch", "pumpkin", "haunt", "spook", "bone", "reaper", "clown", "terror", "sinister", "night stalker", "apparition"),
    "christmas": ("christmas", "xmas", "santa", "snow", "reindeer", "elf", "elves", "holiday", "winter", "noel", "ornament"),
}


def guess_season(text: str) -> str:
    t = text.lower()
    for season, words in SEASON_WORDS.items():
        if any(w in t for w in words):
            return season
    return ""


def guess_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return "video"


def title_from_name(name: str) -> str:
    stem = Path(name).stem
    return re.sub(r"[_\-.]+", " ", stem).strip().title() or "Untitled"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(MEDIA_DIR).as_posix()


def display_matrix_rotation(clockwise_content_rotation: int) -> int:
    """Convert our clockwise pixel correction to MP4 matrix convention."""
    return (-int(clockwise_content_rotation or 0)) % 360


def default_preset() -> dict:
    with session_scope() as s:
        st = s.get(Setting, "default_preset")
        return {**ffmpeg.DEFAULT_PRESET, **(st.value if st and st.value else {})}


class Pipeline:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.current: Optional[str] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def _put(self, asset_id: str) -> None:
        """Thread-safe enqueue; import_file may be called from worker threads."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self.loop and running is not self.loop:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, asset_id)
        else:
            self.queue.put_nowait(asset_id)

    def import_file(
        self,
        src: Path,
        *,
        title: str = "",
        source: str = "upload",
        source_url: str = "",
        license: str = "",
        attribution: str = "",
        season: str = "",
        tags: Optional[list[str]] = None,
        preset: Optional[dict] = None,
        move: bool = True,
        published: bool = True,
        technical: Optional[dict] = None,
        asset_id: Optional[str] = None,
    ) -> MediaAsset:
        """Copy/move a file into the library and queue it for processing."""
        aid = asset_id or new_id()
        with session_scope() as s:
            existing = s.get(MediaAsset, aid)
            if existing:
                return existing
        ext = src.suffix.lower() or ".bin"
        dst = ORIGINALS_DIR / f"{aid}{ext}"
        if move:
            shutil.move(str(src), dst)
        else:
            shutil.copy2(src, dst)
        title = title or title_from_name(src.name)
        asset = MediaAsset(
            id=aid,
            title=title,
            kind=guess_kind(dst),
            source=source,
            source_url=source_url,
            license=license,
            attribution=attribution,
            season=season or guess_season(f"{title} {src.as_posix()}"),
            tags=sorted(set(tags or [])),
            original_path=rel(dst),
            preset=preset if preset is not None else default_preset(),
            status="pending",
            published=published,
            technical=technical or {},
        )
        with session_scope() as s:
            s.add(asset)
            s.commit()
            s.refresh(asset)
        self._put(aid)
        self._notify()
        return asset

    def enqueue(self, asset_id: str) -> None:
        with session_scope() as s:
            a = s.get(MediaAsset, asset_id)
            if a:
                a.status = "pending"
                a.error = ""
                s.add(a)
                s.commit()
        self._put(asset_id)
        self._notify()

    def _notify(self) -> None:
        from ..sync.hub import hub

        if self.loop:
            asyncio.run_coroutine_threadsafe(hub.changed("media"), self.loop)

    def _set(self, asset_id: str, **fields) -> None:
        with session_scope() as s:
            a = s.get(MediaAsset, asset_id)
            if not a:
                return
            for k, v in fields.items():
                setattr(a, k, v)
            s.add(a)
            s.commit()

    def process(self, asset_id: str) -> None:
        """Blocking: runs in a worker thread."""
        with session_scope() as s:
            a = s.get(MediaAsset, asset_id)
            if not a:
                return
            if a.source_file_id:
                from .library import source_path
                source = s.get(SourceFile, a.source_file_id)
                if not source:
                    raise ValueError("Original source record missing")
                src = source_path(source)
            else:
                src = MEDIA_DIR / a.original_path
                if not src.is_file():
                    src = LEGACY_MEDIA_DIR / a.original_path
            kind, preset = a.kind, dict(a.preset or {})
            technical = dict(a.technical or {})
            source_file_id = a.source_file_id
        self._set(asset_id, status="processing", error="")
        info = ffmpeg.probe(src)
        native_info = dict(info)
        if kind == "video" and source_file_id and preset.get("profile") == "native":
            rotation = int(preset.get("content_rotation", 0) or 0)
            out = src
            rendition_path = ""
            if rotation:
                out = RENDITIONS_DIR / f"{asset_id}.mp4"
                # Preserve the encoded streams bit-for-bit. A display matrix lets
                # Media3/SurfaceTexture rotate projector-side vertical masters.
                ffmpeg._run([ffmpeg.FFMPEG, "-y", "-v", "error", "-display_rotation", str(display_matrix_rotation(rotation)),
                             "-i", str(src), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(out)])
                info = ffmpeg.probe(out)
                rendition_path = rel(out)
            thumb = THUMBS_DIR / f"{asset_id}.jpg"
            catalogue_thumb = LIBRARY_DIR / f"{source_file_id}.jpg"
            if catalogue_thumb.is_file():
                shutil.copy2(catalogue_thumb, thumb)
            else:
                ffmpeg.thumbnail(out, thumb, min(info["duration"] * 0.4, 5.0))
            digest = sha256_file(out)
            self._set(asset_id,
                      width=info["width"], height=info["height"], duration_ms=int(info["duration"] * 1000),
                      rendition_path=rendition_path, thumb_path=rel(thumb), sha256=digest, size=out.stat().st_size,
                      status="ready", error="", technical={**technical, "native": native_info, "delivery": info,
                      "direct_source": not rotation, "stream_copy_rotation": rotation, "playback_verified": False})
            with session_scope() as s:
                r = s.exec(select(Rendition).where(Rendition.asset_id == asset_id, Rendition.profile == "native")).first()
                r = r or Rendition(asset_id=asset_id, profile="native", path=rendition_path or f"source:{source_file_id}")
                r.path, r.technical, r.sha256, r.size = rendition_path or f"source:{source_file_id}", info, digest, out.stat().st_size
                s.add(r); s.commit()
            return
        if kind == "video" and info["is_image"]:
            kind = "image"
        if kind == "video" and not info["has_video"] and info["has_audio"]:
            kind = "audio"

        thumb = THUMBS_DIR / f"{asset_id}.jpg"
        fields: dict = {"kind": kind}
        if kind == "image":
            ffmpeg.image_thumb(src, thumb)
            fields.update(width=info["width"], height=info["height"], rendition_path=rel(src), thumb_path=rel(thumb))
            out = src
        elif kind == "audio":
            out = RENDITIONS_DIR / f"{asset_id}.m4a"
            ffmpeg.transcode(ffmpeg.build_audio_cmd(src, out, preset))
            ffmpeg.waveform(out, thumb)
            fields.update(rendition_path=rel(out), thumb_path=rel(thumb))
        else:
            out = RENDITIONS_DIR / f"{asset_id}.mp4"
            ffmpeg.transcode(ffmpeg.build_video_cmd(src, out, preset, info))
            rinfo = ffmpeg.probe(out)
            dur = rinfo["duration"]
            ffmpeg.thumbnail(out, thumb, min(dur * 0.4, 5.0))
            gif = THUMBS_DIR / f"{asset_id}.gif"
            try:
                ffmpeg.preview_gif(out, gif, max(0.0, min(dur * 0.3, dur - 5)), min(5.0, dur))
                fields["preview_path"] = rel(gif)
            except ffmpeg.FFmpegError as e:
                log.warning("preview gif failed for %s: %s", asset_id, e)
            fields.update(
                width=rinfo["width"],
                height=rinfo["height"],
                rendition_path=rel(out),
                thumb_path=rel(thumb),
                suitability=analyze(out),
            )
            info = rinfo
        fields.update(
            duration_ms=int(info["duration"] * 1000),
            sha256=sha256_file(out),
            size=out.stat().st_size,
            status="ready",
            technical={**technical, "native": native_info, "delivery": info, "playback_verified": False},
        )
        self._set(asset_id, **fields)
        with session_scope() as s:
            r = s.exec(select(Rendition).where(Rendition.asset_id == asset_id, Rendition.profile == preset.get("profile", "source"))).first()
            r = r or Rendition(asset_id=asset_id, profile=preset.get("profile", "source"), path=rel(out))
            r.path, r.technical, r.sha256, r.size = rel(out), info, fields["sha256"], fields["size"]
            s.add(r); s.commit()

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        with session_scope() as s:
            for a in s.exec(select(MediaAsset).where(MediaAsset.status.in_(["pending", "processing"]))).all():
                self.queue.put_nowait(a.id)
        while True:
            aid = await self.queue.get()
            self.current = aid
            try:
                await asyncio.to_thread(self.process, aid)
                log.info("processed asset %s", aid)
            except Exception as e:
                log.exception("processing %s failed", aid)
                self._set(aid, status="error", error=str(e)[:2000])
            finally:
                self.current = None
                self._notify()

    async def watch_import_folder(self) -> None:
        """Files dropped in data/import are imported once their size stops changing.

        Sub-folder names become tags, e.g. import/halloween/atmosfx/clip.mp4.
        """
        sizes: dict[Path, int] = {}
        while True:
            try:
                await asyncio.sleep(5)
                seen = set()
                for p in IMPORT_DIR.rglob("*"):
                    if not p.is_file() or p.name.startswith("."):
                        continue
                    ext = p.suffix.lower()
                    if ext not in VIDEO_EXT | AUDIO_EXT | IMAGE_EXT:
                        continue
                    seen.add(p)
                    size = p.stat().st_size
                    if sizes.get(p) != size:
                        sizes[p] = size
                        continue
                    parts = [x.lower() for x in p.relative_to(IMPORT_DIR).parts[:-1]]
                    season = next((x for x in parts if x in SEASON_WORDS), "")
                    tags = [x for x in parts if x not in SEASON_WORDS]
                    await asyncio.to_thread(
                        self.import_file, p, source="import-folder", season=season, tags=tags
                    )
                    sizes.pop(p, None)
                    seen.discard(p)
                for p in list(sizes):
                    if p not in seen:
                        sizes.pop(p, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("import folder scan failed")


pipeline = Pipeline()
