from sqlmodel import Session, select

from .compiler import AssetInfo, Layout, SurfaceInfo
from .geometry import Rect
from .models import Device, MediaAsset, SourceFile, Surface


def asset_url(a: MediaAsset) -> str:
    if a.preset.get("profile") == "native" and a.source_file_id and not a.rendition_path:
        return f"/files/source/{a.source_file_id}"
    return f"/files/media/{a.rendition_path}" if a.rendition_path else ""


def load_layout(session: Session) -> Layout:
    surface_rows = session.exec(select(Surface)).all()
    surfaces = {s.id: SurfaceInfo(s.id, Rect(s.x, s.y, s.width, s.height), list(s.groups or []), s.sort,
                                  "vertical" if s.height > s.width else "horizontal",
                                  s.width / s.height if s.height else 1.0, s.name) for s in surface_rows}
    by_surface: dict[str, list[str]] = {}
    for d in session.exec(select(Device).where(Device.role == "player")).all():
        if d.surface_id:
            by_surface.setdefault(d.surface_id, []).append(d.id)
            # Calibration stores source TL/TR in projector-normalized coordinates.
            # A mostly vertical top edge means the projector is mounted on its side.
            if d.surface_id in surfaces and len(d.corners or []) == 4:
                tl, tr = d.corners[0], d.corners[1]
                dx = (tr[0] - tl[0]) * max(d.output_width, 1)
                dy = (tr[1] - tl[1]) * max(d.output_height, 1)
                if abs(dy) > abs(dx):
                    surfaces[d.surface_id].orientation = "vertical"
                    # The homography rotates the logical canvas into the physical
                    # output. Fit media against the rotated canvas aspect, while
                    # retaining the room-layout rect for travel and choreography.
                    base = surfaces[d.surface_id].fit_aspect
                    surfaces[d.surface_id].fit_aspect = 1.0 / base if base > 0 else 1.0
    source_rows = {row.id: row for row in session.exec(select(SourceFile)).all()}
    assets = {
        a.id: AssetInfo(
            id=a.id,
            duration_ms=a.duration_ms,
            width=a.width,
            height=a.height,
            sha256=a.sha256,
            url=asset_url(a),
            tags=list(a.tags or []),
            season=a.season,
            kind=a.kind,
            scene_key=(source_rows[a.source_file_id].overrides.get("scene_key") or source_rows[a.source_file_id].classification.get("scene_key") or source_rows[a.source_file_id].scene_key) if a.source_file_id in source_rows else "",
            orientation=(source_rows[a.source_file_id].overrides.get("orientation") or source_rows[a.source_file_id].classification.get("orientation") or ("vertical" if a.height > a.width else "horizontal")) if a.source_file_id in source_rows else ("vertical" if a.height > a.width else "horizontal"),
            current_variant=(source_rows[a.source_file_id].asset_id == a.id) if a.source_file_id in source_rows else True,
            title=a.title,
            motion=(a.technical.get("movement_segment") or
                    ((source_rows[a.source_file_id].overrides.get("motion") or source_rows[a.source_file_id].technical.get("motion") or {})
                     if a.source_file_id in source_rows else (a.technical.get("motion") or {}))),
        )
        for a in session.exec(select(MediaAsset).where(MediaAsset.status == "ready", MediaAsset.published == True)).all()
    }
    return Layout(surfaces, by_surface, assets)
