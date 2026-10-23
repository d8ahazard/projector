import time
import uuid
from typing import Any, Optional

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def json_field(default: Any) -> Any:
    factory = (lambda: list(default)) if isinstance(default, list) else (lambda: dict(default))
    return Field(default_factory=factory, sa_column=Column(JSON))


class Surface(SQLModel, table=True):
    """A projection target (a window) placed in world coordinates, in inches."""

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    x: float = 0.0
    y: float = 0.0
    width: float = 48.0
    height: float = 72.0
    groups: list[str] = json_field([])
    sort: int = 0


DEFAULT_CORNERS = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


class Device(SQLModel, table=True):
    """A player stick + projector. Corner pin and mask are normalized 0..1."""

    id: str = Field(primary_key=True)
    name: str = "Player"
    role: str = "player"
    surface_id: Optional[str] = Field(default=None, foreign_key="surface.id", ondelete="SET NULL")
    output_width: int = 1280
    output_height: int = 720
    mirror_h: bool = False
    mirror_v: bool = False
    # Output-space positions of the surface corners TL, TR, BR, BL.
    corners: list[list[float]] = json_field(DEFAULT_CORNERS)
    # Polygon in surface-normalized coords; empty = whole surface.
    mask: list[list[float]] = json_field([])
    feather: float = 0.01
    brightness: float = 1.0
    gamma: float = 1.0
    gain_r: float = 1.0
    gain_g: float = 1.0
    gain_b: float = 1.0
    volume: float = 1.0
    audio: bool = True
    cache_budget_mb: int = 1536
    capabilities: dict = json_field({})
    status: dict = json_field({})
    last_seen: int = 0


class MediaAsset(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    title: str
    kind: str = "video"  # video | audio | image
    source: str = "upload"  # upload | import-folder | catalog:<src> | comfyui
    source_url: str = ""
    license: str = ""
    attribution: str = ""
    tags: list[str] = json_field([])
    season: str = ""  # halloween | christmas | ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    original_path: str = ""
    rendition_path: str = ""
    thumb_path: str = ""
    preview_path: str = ""
    sha256: str = ""
    size: int = 0
    suitability: dict = json_field({})
    preset: dict = json_field({})
    status: str = "pending"  # pending | processing | ready | error
    error: str = ""
    created_at: int = Field(default_factory=now_ms)
    source_file_id: Optional[str] = None
    published: bool = True
    technical: dict = json_field({})


class SourceFile(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    relative_path: str = Field(unique=True, index=True)
    scene_key: str = Field(index=True)
    title: str
    collection: str = Field(index=True)
    classification: dict = json_field({})
    overrides: dict = json_field({})
    technical: dict = json_field({})
    size: int = 0
    mtime_ns: str = ""
    sha256: str = Field(default="", index=True)
    status: str = "indexed"
    error: str = ""
    asset_id: Optional[str] = None
    updated_at: int = Field(default_factory=now_ms)


class MovementSegment(SQLModel, table=True):
    """A coherent, reusable action detected inside an indexed source video."""

    __table_args__ = (UniqueConstraint("source_file_id", "analyzer_version", "segment_index"),)
    id: str = Field(default_factory=new_id, primary_key=True)
    source_file_id: str = Field(index=True, foreign_key="sourcefile.id", ondelete="CASCADE")
    analyzer_version: int = 2
    segment_index: int = 0
    start_ms: int = 0
    end_ms: int = 0
    active_start_ms: int = 0
    active_end_ms: int = 0
    lead_blank_ms: int = 0
    tail_blank_ms: int = 0
    enter_edge: str = "none"
    exit_edge: str = "none"
    dx: float = 0.0
    dy: float = 0.0
    confidence: float = 0.0
    approval: str = "review"  # auto | approved | review | rejected
    character: str = ""
    family: str = ""
    narrative_group: str = ""
    mirror_safe: bool = True
    audio: dict = json_field({})
    evidence: dict = json_field({})
    asset_id: Optional[str] = Field(default=None, index=True)
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)


class MovementLinkOverride(SQLModel, table=True):
    """Sparse human corrections to the compatibility graph derived at compile time."""

    __table_args__ = (UniqueConstraint("from_segment_id", "to_segment_id"),)
    id: str = Field(default_factory=new_id, primary_key=True)
    from_segment_id: str = Field(index=True, foreign_key="movementsegment.id", ondelete="CASCADE")
    to_segment_id: str = Field(index=True, foreign_key="movementsegment.id", ondelete="CASCADE")
    decision: str = "force"  # force | reject
    relation: str = "same_character"
    note: str = ""
    created_at: int = Field(default_factory=now_ms)


class LibraryJob(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    kind: str
    status: str = "queued"
    progress: float = 0
    detail: dict = json_field({})
    error: str = ""
    created_at: int = Field(default_factory=now_ms)


class GenerationProject(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    concept: str = ""
    character_design: str = ""
    orientation: str = "vertical"
    style: str = "Cinematic practical creature effects, consistent anatomy and lighting"
    background: str = "black"
    revision: int = 1
    character_take_id: Optional[str] = None
    created_at: int = Field(default_factory=now_ms)


class StoryboardShot(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(index=True)
    position: int = 0
    revision: int = 1
    name: str
    role: str = "action"
    prompt: str = ""
    background: str = ""
    sound: str = ""
    seconds: int = 5
    surface: str = "A"
    handoff: str = "darkness"
    image_take_id: Optional[str] = None


class GenerationTake(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(index=True)
    shot_id: Optional[str] = Field(default=None, index=True)
    stage: str
    revision: int
    project_revision: int
    status: str = "queued"
    prompt_id: str = ""
    workflow: dict = json_field({})
    provenance: dict = json_field({})
    outputs: list[dict] = json_field([])
    asset_id: Optional[str] = None
    error: str = ""
    elapsed_seconds: float = 0
    created_at: int = Field(default_factory=now_ms)


class Approval(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    take_id: str = Field(index=True)
    project_revision: int
    shot_revision: int
    decision: str = "approved"
    created_at: int = Field(default_factory=now_ms)


class Rendition(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    asset_id: str = Field(index=True)
    profile: str
    path: str
    technical: dict = json_field({})
    sha256: str = ""
    size: int = 0


class Program(SQLModel, table=True):
    """A queue of cues and blocks. `items` holds the choreography JSON (see compiler)."""

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    loop_mode: str = "loop"  # loop | once | shuffle_blocks
    seed: int = 1
    items: list[dict] = json_field([])
    updated_at: int = Field(default_factory=now_ms)


class Schedule(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    program_id: str = Field(foreign_key="program.id", ondelete="CASCADE")
    days: list[int] = json_field([0, 1, 2, 3, 4, 5, 6])  # Monday = 0
    start: str = "18:00"
    end: str = "23:00"
    enabled: bool = True


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))


class CatalogItem(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source", "source_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str
    source_id: str
    kind: str = "video"
    title: str = ""
    description: str = ""
    page_url: str = ""
    media_url: str = ""
    thumb_url: str = ""
    preview_url: str = ""
    license: str = ""
    license_url: str = ""
    author: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    season: str = ""
    tags: list[str] = json_field([])
    suitability: float = 0.0
    asset_id: Optional[str] = None
    indexed_at: int = Field(default_factory=now_ms)


class ComfyWorkflow(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    kind: str = "video"  # video | audio | image
    description: str = ""
    workflow: dict = json_field({})
    # [{name, label, type, default, node, input}]
    params: list[dict] = json_field([])
    builtin: bool = False


class ComfyJob(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    workflow_id: str
    params: dict = json_field({})
    prompt_id: str = ""
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    asset_ids: list[str] = json_field([])
    season: str = ""
    tags: list[str] = json_field([])
    error: str = ""
    created_at: int = Field(default_factory=now_ms)


class CalibrationSession(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    kind: str = "geometry"  # geometry | color
    device_ids: list[str] = json_field([])
    data: dict = json_field({})
    created_at: int = Field(default_factory=now_ms)


class CalibrationProfile(SQLModel, table=True):
    """Named projector placement settings, e.g. wall test or front window."""

    id: str = Field(default_factory=new_id, primary_key=True)
    device_id: str = Field(foreign_key="device.id", ondelete="CASCADE", index=True)
    name: str
    config: dict = json_field({})
    active: bool = False
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)
