"""Program compiler: cues + layout -> absolute per-surface slots -> per-device events.

Program JSON (`Program.items`) is a list of items:

    cue   = {type: "cue", id, name, clips: [asset_id], pool: {tags, season},
             target: {mode: all|group|list, group, surfaces: [id]},
             pattern: together|each|sequence|span|chase|ping-pong|random-one,
             direction: left-right|right-left|top-bottom|bottom-top|list,
             spacing: {mode: after|stagger|travel, gap, stagger, speed, exit_at, enter_at},
             duration: {mode: clip|fixed|loops, seconds, loops},
             passes, repeat, hold, fit: contain|cover|stretch,
             keyframes: [{t, x, y, w, h, opacity}]   # span only, x/y/w/h relative to target bounds
             transition: {type, duration, easing, direction, spread, local},
             offsets: {surface_id: seconds}}
    scheduler = {type: "scheduler", id, name, roster: [{id, name, clips}],
                 target, recycle_min, recycle_max, gap_min, gap_max,
                 rotation_gap_min, rotation_gap_max, transition}
    block = {type: "block", id, name, repeat, shuffle, items: [cue]}

All times inside cue JSON are seconds; compiled output is milliseconds.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterator, Optional

from . import geometry as geo
from .geometry import Rect

DEFAULT_TRANSITION = {"type": "dip", "duration": 0.8, "easing": "ease-in-out"}
SPATIAL = ("wipe", "cascade", "ripple-out", "ripple-in")
OVERLAPPING = ("crossfade", "wipe")
MAX_EMPTY_ITERATIONS = 1


@dataclass
class SurfaceInfo:
    id: str
    rect: Rect
    groups: list[str] = field(default_factory=list)
    sort: int = 0
    orientation: str = ""  # vertical | horizontal, resolved from surface + calibrated projector mount
    # Aspect of the logical playback canvas before the corner pin is applied.
    # This differs from the room-layout rect when a landscape projector is
    # physically mounted on its side: a 16:9 output becomes a 9:16 canvas.
    fit_aspect: float = 0.0
    name: str = ""


@dataclass
class AssetInfo:
    id: str
    duration_ms: int
    width: int = 1280
    height: int = 720
    sha256: str = ""
    url: str = ""
    tags: list[str] = field(default_factory=list)
    season: str = ""
    kind: str = "video"
    scene_key: str = ""
    orientation: str = ""
    # Source-file corrections create a replacement asset while old concrete
    # IDs remain playable for saved cues. Automatic sibling selection should
    # prefer the replacement currently attached to the source record.
    current_variant: bool = True
    title: str = ""
    motion: dict = field(default_factory=dict)

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.width and self.height else 16 / 9


@dataclass
class Layout:
    surfaces: dict[str, SurfaceInfo]
    devices_by_surface: dict[str, list[str]]
    assets: dict[str, AssetInfo]


@dataclass
class Slot:
    surface_id: str
    start: int
    end: int
    asset_id: str
    cue_id: str
    cue_index: int
    transition: dict
    rect: list[dict]
    loop: bool
    audio: bool = True
    character_id: str = ""
    character_name: str = ""
    source_program: str = ""
    flip_h: bool = False
    motion_reason: str = ""

    def to_dict(self, layout: Optional[Layout] = None) -> dict:
        d = {
            "surface_id": self.surface_id,
            "start": self.start,
            "end": self.end,
            "asset_id": self.asset_id,
            "cue_id": self.cue_id,
            "cue_index": self.cue_index,
            "transition": self.transition,
            "rect": self.rect,
            "loop": self.loop,
            "audio": self.audio,
            "character_id": self.character_id,
            "character_name": self.character_name,
            "source_program": self.source_program,
            "flip_h": self.flip_h,
            "motion_reason": self.motion_reason,
        }
        if layout is not None:
            d["device_ids"] = layout.devices_by_surface.get(self.surface_id, [])
        return d


@dataclass
class CueInstance:
    cue_id: str
    name: str
    index: int
    start: int
    end: int
    next_start: int
    slots: list[Slot]
    item_path: list[int]
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------- helpers


def _sec(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def resolve_targets(cue: dict, layout: Layout) -> list[SurfaceInfo]:
    target = cue.get("target") or {"mode": "all"}
    mode = target.get("mode", "all")
    surfaces = sorted(layout.surfaces.values(), key=lambda s: (s.sort, s.rect.x, s.rect.y))
    if mode == "group":
        g = target.get("group", "")
        return [s for s in surfaces if g in s.groups]
    if mode == "list":
        return [layout.surfaces[sid] for sid in target.get("surfaces", []) if sid in layout.surfaces]
    return surfaces


def candidate_assets(cue: dict, layout: Layout) -> tuple[list[str], bool]:
    """Returns (asset ids, is_pool). Explicit clips win over pool."""
    clips = [c for c in cue.get("clips", []) if c in layout.assets and layout.assets[c].kind == "video"]
    if clips:
        return clips, False
    pool = cue.get("pool") or {}
    tags = {t.lower() for t in pool.get("tags", []) if t}
    season = (pool.get("season") or "").lower()
    if not tags and not season:
        return [], True
    out = []
    for a in layout.assets.values():
        if a.kind != "video":
            continue
        if season and a.season.lower() != season:
            continue
        if tags and not tags.intersection(t.lower() for t in a.tags):
            continue
        out.append(a.id)
    return sorted(out), True


def resolve_surface_variant(asset_id: str, surface: SurfaceInfo, layout: Layout, candidates: Optional[list[str]] = None) -> str:
    """Resolve a saved scene choice to the ready variant matching this calibrated output.

    The explicit asset remains the stable fallback. AtmosFX siblings share a scene key;
    generated/uploaded assets without siblings keep their concrete ID.
    """
    original = layout.assets[asset_id]
    wanted = surface.orientation or ("vertical" if surface.rect.h > surface.rect.w else "horizontal")
    if original.orientation == wanted:
        return asset_id
    siblings = [a for a in layout.assets.values() if original.scene_key and a.scene_key == original.scene_key and a.orientation == wanted and a.kind == "video"]
    current_siblings = [a for a in siblings if a.current_variant]
    if current_siblings:
        siblings = current_siblings
    pool = [layout.assets[aid] for aid in (candidates or []) if aid in layout.assets and layout.assets[aid].orientation == wanted]
    choices = siblings or pool
    if not choices:
        return asset_id
    original_tags = set(original.tags)
    choices.sort(key=lambda a: (-len(original_tags.intersection(a.tags)), abs(a.duration_ms - original.duration_ms), a.id))
    return choices[0].id


def required_assets(program: dict, layout: Layout, surface_ids: Optional[set[str]] = None) -> set[str]:
    """Resolve the concrete media needed by the selected projection surfaces.

    A player only caches renditions for its own calibrated surface.  Omitting
    ``surface_ids`` retains the full-program union used by editors and audits.
    """
    out: set[str] = set()
    for cue in iter_cues(program.get("items", [])):
        if cue.get("type") == "scheduler":
            targets = [s for s in resolve_targets(cue, layout) if surface_ids is None or s.id in surface_ids]
            for character in cue.get("roster", []):
                for aid in character.get("clips", []):
                    if aid in layout.assets:
                        out.update(resolve_surface_variant(aid, surface, layout, character.get("clips", [])) for surface in targets)
            continue
        if cue.get("type") == "encounter":
            for track in cue.get("tracks", []):
                aid, sid = track.get("asset_id"), track.get("surface_id")
                if aid in layout.assets and sid in layout.surfaces and (surface_ids is None or sid in surface_ids):
                    out.add(resolve_surface_variant(aid, layout.surfaces[sid], layout))
            continue
        ids, _ = candidate_assets(cue, layout)
        targets = [s for s in resolve_targets(cue, layout) if surface_ids is None or s.id in surface_ids]
        if cue.get("pattern") == "span":
            if targets:
                out.update(ids)
        else:
            out.update(resolve_surface_variant(aid, surface, layout, ids) for aid in ids for surface in targets)
    return out


def iter_cues(items: list[dict]) -> Iterator[dict]:
    for it in items:
        if it.get("type") == "block":
            yield from iter_cues(it.get("items", []))
        elif it.get("type", "cue") in ("cue", "encounter", "scheduler"):
            yield it


class _Picker:
    """Chooses clips for one cue instance; cycles explicit clips, samples pools."""

    def __init__(self, ids: list[str], is_pool: bool, rng: random.Random, rotation: int):
        self.ids = ids
        self.is_pool = is_pool
        self.rng = rng
        self.rotation = rotation
        self._bag: list[str] = []

    def pick(self, i: int) -> str:
        if not self.is_pool:
            return self.ids[(self.rotation + i) % len(self.ids)]
        if not self._bag:
            self._bag = list(self.ids)
            self.rng.shuffle(self._bag)
        return self._bag.pop()


def _clip_duration(cue: dict, asset: AssetInfo) -> tuple[int, bool]:
    rule = cue.get("duration") or {"mode": "clip"}
    base = max(asset.duration_ms, 1)
    mode = rule.get("mode", "clip")
    if mode == "fixed":
        d = ms(_sec(rule.get("seconds"), base / 1000))
    elif mode == "loops":
        d = base * max(1, int(_sec(rule.get("loops"), 1)))
    else:
        d = base
    return max(d, 1), d > base


def _static_rect(asset: AssetInfo, surface: SurfaceInfo, fit: str) -> list[dict]:
    aspect = surface.fit_aspect or (surface.rect.w / surface.rect.h if surface.rect.h else 1.0)
    # Scene selections normally resolve to a matching orientation first. If a
    # collection has no matching variant (for example, the wide singing
    # pumpkins), crop the center into the calibrated canvas instead of showing
    # a tiny letterboxed strip on a side-mounted projector.
    effective_fit = "cover" if fit == "contain" and surface.orientation and asset.orientation and asset.orientation != surface.orientation else fit
    x, y, w, h = geo.fit_rect(asset.aspect, Rect(0, 0, aspect, 1.0), effective_fit)
    return [{"t": 0, "x": x, "y": y, "w": w, "h": h, "opacity": 1.0}]


def _span_rects(cue: dict, asset: AssetInfo, targets: list[SurfaceInfo], fit: str) -> dict[str, list[dict]]:
    area = geo.bounds(s.rect for s in targets)
    kfs = cue.get("keyframes") or []
    if not kfs:
        r = geo.fit_world_rect(asset.aspect, area, fit)
        world_kfs = [(0, r, 1.0)]
    else:
        world_kfs = []
        for k in sorted(kfs, key=lambda k: _sec(k.get("t"))):
            r = Rect(
                area.x + _sec(k.get("x")) * area.w,
                area.y + _sec(k.get("y")) * area.h,
                _sec(k.get("w"), 1) * area.w,
                _sec(k.get("h"), 1) * area.h,
            )
            world_kfs.append((ms(_sec(k.get("t"))), r, _sec(k.get("opacity"), 1.0)))
    out = {}
    for s in targets:
        lst = []
        for t, r, op in world_kfs:
            x, y, w, h = geo.world_to_surface_rect(r, s.rect)
            lst.append({"t": t, "x": x, "y": y, "w": w, "h": h, "opacity": op})
        out[s.id] = lst
    return out


def _spacing_next(cue: dict, prev: SurfaceInfo, nxt: SurfaceInfo, prev_start: int, prev_dur: int) -> int:
    sp = cue.get("spacing") or {"mode": "after"}
    mode = sp.get("mode", "after")
    if mode == "stagger":
        return prev_start + ms(_sec(sp.get("stagger"), 1.0))
    if mode == "travel":
        speed_ft_s = max(_sec(sp.get("speed"), 4.0), 0.01)
        gap_in = geo.gap_distance(prev.rect, nxt.rect)
        exit_at = ms(_sec(sp.get("exit_at"), prev_dur / 1000)) if sp.get("exit_at") is not None else prev_dur
        enter_at = ms(_sec(sp.get("enter_at"), 0.0))
        return prev_start + exit_at + ms(gap_in / 12.0 / speed_ft_s) - enter_at
    return prev_start + prev_dur + ms(_sec(sp.get("gap"), 0.0))


# ---------------------------------------------------------------- cue expansion


@dataclass
class _State:
    last_random_target: Optional[str] = None
    last_scheduler_character: Optional[str] = None
    last_scheduler_characters: list[str] = field(default_factory=list)
    last_scheduler_surface: Optional[str] = None
    scheduler_character_bags: dict[str, list[str]] = field(default_factory=dict)
    scheduler_group_bags: dict[str, list[str]] = field(default_factory=dict)
    scheduler_active_groups: dict[str, str] = field(default_factory=dict)
    last_scheduler_group: Optional[str] = None
    index: int = 0


def expand_cue(
    cue: dict,
    start: int,
    layout: Layout,
    rng: random.Random,
    state: _State,
    rotation: int = 0,
    item_path: Optional[list[int]] = None,
) -> Optional[CueInstance]:
    if cue.get("type") == "scheduler":
        return expand_scheduler(cue, start, layout, rng, state, item_path)
    if cue.get("type") == "encounter":
        return expand_encounter(cue, start, layout, state, item_path)
    targets = resolve_targets(cue, layout)
    ids, is_pool = candidate_assets(cue, layout)
    cue_id = cue.get("id", "")
    name = cue.get("name") or cue.get("pattern", "cue")
    hold = ms(_sec(cue.get("hold"), 0.0))
    if not targets or not ids:
        if hold <= 0:
            return None
        inst = CueInstance(cue_id, name, state.index, start, start, start + hold, [], item_path or [],
                           {"type": cue.get("type", "cue"), "pattern": cue.get("pattern", "hold")})
        state.index += 1
        return inst

    picker = _Picker(ids, is_pool, rng, rotation)
    pattern = cue.get("pattern", "together")
    fit = cue.get("fit", "contain")
    direction = cue.get("direction") or ("left-right" if pattern in ("sequence", "chase", "ping-pong") else "list")
    offsets = cue.get("offsets") or {}
    plan: list[tuple[SurfaceInfo, int, str]] = []  # (surface, relative start ms, asset)

    if pattern in ("together", "span"):
        a = picker.pick(0)
        plan = [(s, 0, a) for s in targets]
    elif pattern == "each":
        plan = [(s, 0, picker.pick(j)) for j, s in enumerate(targets)]
    elif pattern in ("sequence", "chase", "ping-pong"):
        ordered = geo.order_by_direction(targets, lambda s: s.rect, direction)
        passes = 1 if pattern == "sequence" else max(1, int(_sec(cue.get("passes"), 2)))
        order: list[SurfaceInfo] = []
        for p in range(passes):
            seq = ordered if (pattern != "ping-pong" or p % 2 == 0) else list(reversed(ordered))
            if order and seq and seq[0].id == order[-1].id and len(seq) > 1:
                seq = seq[1:]
            order.extend(seq)
        t = 0
        explicit = not is_pool and len(ids) > 1
        same = picker.pick(0)
        for j, s in enumerate(order):
            a = picker.pick(j) if explicit else same
            if j > 0:
                prev_s, prev_t, prev_a = plan[-1]
                prev_dur, _ = _clip_duration(cue, layout.assets[prev_a])
                t = _spacing_next(cue, prev_s, s, prev_t, prev_dur)
            plan.append((s, t, a))
    elif pattern == "random-one":
        count = max(1, int(_sec(cue.get("passes"), 1)))
        t = 0
        for j in range(count):
            choices = [s for s in targets if s.id != state.last_random_target] or targets
            s = rng.choice(choices)
            state.last_random_target = s.id
            a = picker.pick(j)
            if plan:
                prev_s, prev_t, prev_a = plan[-1]
                prev_dur, _ = _clip_duration(cue, layout.assets[prev_a])
                t = prev_t + prev_dur + ms(_sec((cue.get("spacing") or {}).get("gap"), 0.0))
            plan.append((s, t, a))
    else:
        raise ValueError(f"unknown pattern {pattern}")

    if pattern != "span":
        plan = [(surface, rel, resolve_surface_variant(aid, surface, layout, ids)) for surface, rel, aid in plan]

    tr = dict(DEFAULT_TRANSITION)
    tr.update(cue.get("transition") or {})
    ttype = tr.get("type", "dip")
    tdur = ms(_sec(tr.get("duration"), 0.8))
    delays: dict[str, float] = {}
    if ttype in SPATIAL:
        rects = {s.id: s.rect for s in targets}
        delays = geo.spatial_delays(rects, ttype, tr.get("direction"), _sec(tr.get("spread"), 1.5))
    local = ttype
    if ttype in ("cascade", "ripple-out", "ripple-in"):
        local = tr.get("local") or "dip"

    span_rects = _span_rects(cue, layout.assets[plan[0][2]], targets, fit) if pattern == "span" else {}

    slots: list[Slot] = []
    for s, rel, a in plan:
        asset = layout.assets[a]
        dur, loop = _clip_duration(cue, asset)
        delay = ms(delays.get(s.id, 0.0))
        st = start + rel + delay + ms(_sec(offsets.get(s.id), 0.0))
        slots.append(
            Slot(
                surface_id=s.id,
                start=st,
                end=st + dur,
                asset_id=a,
                cue_id=cue_id,
                cue_index=state.index,
                transition={
                    "type": local,
                    "duration": tdur,
                    "easing": tr.get("easing", "ease-in-out"),
                    "direction": tr.get("direction", "right"),
                    "delay": delay,
                },
                rect=span_rects.get(s.id) or _static_rect(asset, s, fit),
                loop=loop,
                audio=bool((cue.get("audio_overrides") or {}).get(s.id, not (pattern in ("together", "span") and s.id != cue.get("audio_surface_id", targets[0].id)))),
                character_id=cue_id or name,
                character_name=name,
                source_program=str(cue.get("source_program") or ""),
            )
        )
    end = max(sl.end for sl in slots)
    first = min(sl.start for sl in slots)
    inst = CueInstance(cue_id, name, state.index, first, end, end + hold, slots, item_path or [],
                       {"type": cue.get("type", "cue"), "pattern": pattern,
                        "characters": [{"id": cue_id or name, "name": name,
                                        "source_program": str(cue.get("source_program") or ""),
                                        "appearances_total": len(slots)}]})
    state.index += 1
    return inst


def expand_scheduler(cue, start, layout, rng, state, item_path=None):
    """Build one multi-character rotation for an open-ended haunt.

    Each character lane has independent clip and surface bags. A central surface
    availability clock prevents collisions while still allowing different free
    projectors to play concurrently. This scales with the layout; there is no
    fixed window count.
    """
    targets = resolve_targets(cue, layout)
    roster = []
    for raw in cue.get("roster", []):
        clips = [a for a in raw.get("clips", []) if a in layout.assets and layout.assets[a].kind == "video"]
        if clips:
            roster.append({**raw, "clips": clips})
    if not targets or not roster:
        return None

    recycle_min = max(0.1, _sec(cue.get("recycle_min"), 1.5))
    recycle_max = max(recycle_min, _sec(cue.get("recycle_max"), 2.0))
    gap_min = max(0.0, _sec(cue.get("gap_min"), 4.0))
    gap_max = max(gap_min, _sec(cue.get("gap_max"), 15.0))
    rotation_min = max(0.0, _sec(cue.get("rotation_gap_min"), 10.0))
    rotation_max = max(rotation_min, _sec(cue.get("rotation_gap_max"), 30.0))
    surface_buffer = max(0.0, _sec(cue.get("surface_buffer"), 1.0))
    fit = cue.get("fit", "contain")
    tr = {**DEFAULT_TRANSITION, **(cue.get("transition") or {})}
    tr["duration"] = ms(max(0.0, _sec(tr.get("duration"), 1.2)))

    scheduler_id = str(cue.get("id", "scheduler"))
    all_by_id = {str(c.get("id", c.get("name", i))): c for i, c in enumerate(roster)}
    group = None
    group_defs = []
    for raw_group in cue.get("groups", []):
        ids = [str(cid) for cid in raw_group.get("character_ids", []) if str(cid) in all_by_id]
        if ids:
            group_defs.append({**raw_group, "character_ids": ids})
    grouped = bool(group_defs)
    if grouped:
        groups_by_id = {str(g.get("id") or g.get("name")): g for g in group_defs}
        active_id = state.scheduler_active_groups.get(scheduler_id)
        if active_id not in groups_by_id:
            group_bag = [gid for gid in state.scheduler_group_bags.get(scheduler_id, []) if gid in groups_by_id]
            if not group_bag:
                group_bag = list(groups_by_id)
                rng.shuffle(group_bag)
                if len(group_bag) > 1 and group_bag[-1] == state.last_scheduler_group:
                    group_bag[0], group_bag[-1] = group_bag[-1], group_bag[0]
            active_id = group_bag.pop()
            state.scheduler_group_bags[scheduler_id] = group_bag
            state.scheduler_active_groups[scheduler_id] = active_id
            state.last_scheduler_group = active_id
        group = groups_by_id[active_id]
        by_id = {cid: all_by_id[cid] for cid in group["character_ids"]}
        character_bag_key = f"{scheduler_id}:{active_id}"
        bag = [cid for cid in state.scheduler_character_bags.get(character_bag_key, []) if cid in by_id]
        if not bag:
            bag = list(by_id)
            rng.shuffle(bag)
        requested_lanes = int(_sec(group.get("active_characters"), _sec(cue.get("active_characters"), 0)))
        # Keep the requested number of compatible lanes active even when the
        # final draw from an odd-sized group contains only one new character.
        # A previously drawn member may accompany that last cast member, but
        # only undrawn IDs are removed from the group bag.
        lane_count = min(len(targets), requested_lanes if requested_lanes > 0 else len(targets))
        lane_count = min(len(by_id), lane_count)
    else:
        by_id = all_by_id
        character_bag_key = scheduler_id
        bag = [cid for cid in state.scheduler_character_bags.get(character_bag_key, []) if cid in by_id]
        requested_lanes = int(_sec(cue.get("active_characters"), 0))
        lane_count = min(len(roster), len(targets), requested_lanes if requested_lanes > 0 else len(targets))
    chosen_ids: list[str] = []
    while len(chosen_ids) < lane_count:
        if not bag:
            if grouped:
                break
            bag = list(by_id)
            rng.shuffle(bag)
            # The bag pops from the end. Avoid an immediate rotation repeat at
            # a bag boundary when another character is available.
            if len(bag) > 1 and bag[-1] in state.last_scheduler_characters:
                swap = next((i for i, cid in enumerate(bag[:-1]) if cid not in state.last_scheduler_characters), None)
                if swap is not None:
                    bag[swap], bag[-1] = bag[-1], bag[swap]
        pick_at = next((i for i in range(len(bag) - 1, -1, -1) if bag[i] not in chosen_ids), None)
        if pick_at is None:
            bag = []
            continue
        chosen_ids.append(bag.pop(pick_at))
    if grouped and len(chosen_ids) < lane_count:
        companions = [cid for cid in by_id if cid not in chosen_ids]
        rng.shuffle(companions)
        companions.sort(key=lambda cid: cid in state.last_scheduler_characters)
        chosen_ids.extend(companions[:lane_count - len(chosen_ids)])
    state.scheduler_character_bags[character_bag_key] = bag
    group_complete = grouped and not bag
    if group_complete:
        state.scheduler_active_groups.pop(scheduler_id, None)
    characters = [by_id[cid] for cid in chosen_ids]
    state.last_scheduler_characters = [str(c.get("id", c.get("name", "character"))) for c in characters]
    state.last_scheduler_character = state.last_scheduler_characters[0] if state.last_scheduler_characters else None

    lanes = []
    for character in characters:
        # One distinct action gets one appearance in this rotation. A recycle
        # ratio only extends characters that can vary the action; moving the
        # same file to another window is not variety.
        if len(character["clips"]) == 1:
            count = 1
        else:
            raw_count = len(character["clips"]) * rng.uniform(recycle_min, recycle_max)
            count = max(len(character["clips"]), int(raw_count) + (1 if rng.random() < raw_count % 1 else 0))
        surface_bag = list(targets)
        rng.shuffle(surface_bag)
        lanes.append({"character": character, "remaining": count, "total": count, "clips": [], "surfaces": surface_bag,
                      "last_surface": None, "last_asset": None, "last_slot": None, "travel_sign": rng.choice((-1, 1)),
                      "next": start + ms(rng.uniform(gap_min, gap_max))})

    surface_free = {surface.id: start for surface in targets}
    slots: list[Slot] = []
    while any(lane["remaining"] > 0 for lane in lanes):
        lane = min((x for x in lanes if x["remaining"] > 0), key=lambda x: x["next"])
        character = lane["character"]
        if not lane["clips"]:
            lane["clips"] = list(character["clips"])
            rng.shuffle(lane["clips"])
            if len(lane["clips"]) > 1 and lane["clips"][-1] == lane["last_asset"]:
                lane["clips"][0], lane["clips"][-1] = lane["clips"][-1], lane["clips"][0]
        if cue.get("smart_choreography") and lane.get("last_asset"):
            previous_motion = layout.assets[lane["last_asset"]].motion
            wanted = "left" if previous_motion.get("exit_edge") == "right" else "right" if previous_motion.get("exit_edge") == "left" else "none"
            def smart_score(aid: str) -> tuple[float, float]:
                motion = layout.assets[aid].motion
                score = float(motion.get("confidence") or 0)
                if motion.get("approval") in ("auto", "approved"): score += .25
                if wanted != "none" and motion.get("enter_edge") == wanted: score += .5
                elif wanted != "none" and motion.get("enter_edge") in ("left", "right") and motion.get("mirror_safe", True): score += .25
                return score, rng.random()
            best_at = max(range(len(lane["clips"])), key=lambda i: smart_score(lane["clips"][i]))
            asset_id = lane["clips"].pop(best_at)
        else:
            asset_id = lane["clips"].pop()
        lane["last_asset"] = asset_id
        if not lane["surfaces"]:
            lane["surfaces"] = list(targets)
            rng.shuffle(lane["surfaces"])
            if len(lane["surfaces"]) > 1 and lane["surfaces"][-1].id == lane["last_surface"]:
                lane["surfaces"][0], lane["surfaces"][-1] = lane["surfaces"][-1], lane["surfaces"][0]
        # Use the earliest-free surface still in this character's bag. In auto
        # motion mode prefer a physical progression across the room, reversing
        # at an edge, instead of teleporting between random windows.
        surface_choices = [s for s in lane["surfaces"] if s.id != lane["last_surface"]] or lane["surfaces"]
        motion_auto = str(cue.get("motion_mode", "auto")).lower() == "auto"
        previous = next((s for s in targets if s.id == lane["last_surface"]), None)
        if motion_auto and previous is not None and len(surface_choices) > 1:
            sign = lane["travel_sign"]
            forward = [s for s in surface_choices if (s.rect.x - previous.rect.x) * sign > 0]
            if not forward:
                sign *= -1
                lane["travel_sign"] = sign
                forward = [s for s in surface_choices if (s.rect.x - previous.rect.x) * sign > 0]
            candidates = forward or surface_choices
            surface = min(candidates, key=lambda s: (max(lane["next"], surface_free[s.id]), abs(s.rect.x - previous.rect.x)))
        else:
            surface = min(surface_choices, key=lambda s: max(lane["next"], surface_free[s.id]))
        lane["surfaces"].remove(surface)
        desired_sign = 0
        if previous is not None:
            desired_sign = 1 if surface.rect.x > previous.rect.x else -1 if surface.rect.x < previous.rect.x else 0
            # The prior appearance should leave toward this surface when its
            # analysis has a reliable exit direction.
            prior = lane.get("last_slot")
            if prior is not None and desired_sign:
                prior.flip_h, prior.motion_reason = _motion_flip(
                    layout.assets[prior.asset_id].motion, desired_sign, "exit"
                )
        lane["last_surface"] = surface.id
        state.last_scheduler_surface = surface.id
        cursor = max(lane["next"], surface_free[surface.id])
        asset_id = resolve_surface_variant(asset_id, surface, layout, character["clips"])
        asset = layout.assets[asset_id]
        duration = max(asset.duration_ms, 1)
        flip_h, motion_reason = _motion_flip(asset.motion, desired_sign, "enter") if desired_sign else (False, "first appearance")
        if cue.get("smart_choreography") and asset.motion.get("approval") in ("auto", "approved"):
            motion_reason = f"linked beat · {motion_reason}"
        slot = Slot(
            surface.id, cursor, cursor + duration, asset_id,
            cue.get("id", "scheduler"), state.index,
            dict(tr), _static_rect(asset, surface, fit), False, True,
            str(character.get("id", character.get("name", "character"))),
            str(character.get("name") or "Character"),
            str(character.get("source_program") or ""),
            flip_h, motion_reason,
        )
        slots.append(slot)
        lane["last_slot"] = slot
        end = cursor + duration
        surface_free[surface.id] = end + ms(surface_buffer)
        lane["next"] = end + ms(rng.uniform(gap_min, gap_max))
        lane["remaining"] -= 1

    end = max(slot.end for slot in slots)
    names = list(dict.fromkeys(c.get("name") or "Character" for c in characters))
    name = " + ".join(names) or cue.get("name") or "Marathon rotation"
    if group_complete:
        group_gap_min = max(rotation_min, _sec(cue.get("group_gap_min"), 15.0))
        group_gap_max = max(group_gap_min, _sec(cue.get("group_gap_max"), 30.0))
        next_start = end + ms(rng.uniform(group_gap_min, group_gap_max))
    else:
        next_start = end + ms(rng.uniform(rotation_min, rotation_max))
    remaining_names = [by_id[cid].get("name") or "Character" for cid in bag]
    details = {
        "type": "scheduler",
        "characters": [],
        "character_bag_remaining": remaining_names,
        "roster_size": len(roster),
        "collection_group": ({"id": str(group.get("id") or group.get("name")), "name": group.get("name") or "Collection",
                              "complete": group_complete} if group else None),
        "smart_choreography": bool(cue.get("smart_choreography")),
    }
    for lane in lanes:
        cid = str(lane["character"].get("id", lane["character"].get("name", "character")))
        existing = next((row for row in details["characters"] if row["id"] == cid), None)
        if existing:
            existing["appearances_total"] += lane["total"]
        else:
            details["characters"].append({"id": cid, "name": lane["character"].get("name") or "Character",
                                           "source_program": lane["character"].get("source_program", ""),
                                           "appearances_total": lane["total"]})
    result = CueInstance(cue.get("id", "scheduler"), name, state.index, start, end, next_start, slots, item_path or [], details)
    state.index += 1
    return result


def _motion_flip(motion: dict, desired_sign: int, phase: str) -> tuple[bool, str]:
    """Return a safe per-event horizontal flip for an intended room direction.

    desired_sign is -1 for room-left and +1 for room-right. Entrance and exit
    edges take precedence over whole-clip travel because many AtmosFX actions
    appear, perform in place, then disappear instead of crossing the frame.
    """
    if not desired_sign or not isinstance(motion, dict):
        return False, "motion direction unavailable"
    confidence = float(motion.get("confidence") or 0.0)
    if confidence < 0.55:
        return False, f"motion confidence {confidence:.2f}; unchanged"
    edge = str(motion.get(f"{phase}_edge") or "")
    native = 0
    source = ""
    if edge in ("left", "right"):
        native = 1 if (phase == "enter" and edge == "left") or (phase == "exit" and edge == "right") else -1
        source = f"{phase} {edge}"
    else:
        travel = str(motion.get("travel") or "")
        if travel == "left-right": native = 1
        elif travel == "right-left": native = -1
        source = travel
    if not native:
        return False, "no reliable horizontal movement; unchanged"
    flip = native != desired_sign
    if flip and motion.get("mirror_safe") is False:
        return False, f"{source}; mirroring disabled"
    desired = "right" if desired_sign > 0 else "left"
    return flip, f"{source} aligned {desired}" + (" by horizontal flip" if flip else "")


def expand_encounter(cue, start, layout, state, item_path=None):
    import math
    slots = []
    for track in cue.get("tracks", []):
        sid, aid = track.get("surface_id"), track.get("asset_id")
        if sid not in layout.surfaces: raise ValueError(f"Encounter surface is missing: {sid}")
        if aid not in layout.assets: raise ValueError(f"Encounter clip is not ready/published: {aid}")
        aid = resolve_surface_variant(aid, layout.surfaces[sid], layout)
        offset = float(track.get("at", 0))
        duration = float(track.get("seconds") or layout.assets[aid].duration_ms / 1000)
        if not math.isfinite(offset) or not math.isfinite(duration) or offset < 0 or duration <= 0:
            raise ValueError("Encounter times must be finite, nonnegative, with positive duration")
        st, end = start + ms(offset), start + ms(offset + duration)
        if any(x.surface_id == sid and st < x.end and end > x.start for x in slots):
            raise ValueError("Encounter clips overlap on the same surface; use sequential clips or bake the effect into a take")
        tr = {**DEFAULT_TRANSITION, **track.get("transition", {})}
        if tr["type"] not in ("cut","dip","wipe","crossfade"): raise ValueError("Unknown encounter transition")
        tr["duration"] = ms(min(max(0, float(tr.get("duration", 0.5))), duration/2))
        slots.append(Slot(sid, st, end, aid, cue.get("id", ""), state.index, tr,
                          _static_rect(layout.assets[aid], layout.surfaces[sid], track.get("fit", "contain")),
                          duration * 1000 > layout.assets[aid].duration_ms + 1,
                          bool(track.get("audio", not any(x.asset_id == aid and x.start == st for x in slots))),
                          str(cue.get("id") or cue.get("name") or "encounter"),
                          str(cue.get("name") or "Encounter"),
                          str(cue.get("source_program") or "")))
    if not slots: raise ValueError("Encounter needs at least one clip")
    end = max(s.end for s in slots)
    result = CueInstance(cue.get("id", ""), cue.get("name", "Encounter"), state.index, start, end,
                         end + ms(max(0,float(cue.get("hold",0)))), slots, item_path or [],
                         {"type": "encounter", "pattern": "tracks",
                          "characters": [{"id": str(cue.get("id") or cue.get("name") or "encounter"),
                                          "name": str(cue.get("name") or "Encounter"),
                                          "source_program": str(cue.get("source_program") or ""),
                                          "appearances_total": len(slots)}]})
    state.index += 1
    return result


def _overlap_for(cue: dict) -> int:
    tr = cue.get("transition") or {}
    ttype = tr.get("type", DEFAULT_TRANSITION["type"])
    if ttype in OVERLAPPING or (ttype in SPATIAL and tr.get("local") == "crossfade"):
        return ms(_sec(tr.get("duration"), 0.8))
    return 0


# ---------------------------------------------------------------- program iteration


def _flatten_iteration(items: list[dict], loop_mode: str, rng: random.Random, prev_last: Optional[int]):
    """Yields (cue, item_path, rotation) for one pass over the program."""
    order = list(range(len(items)))
    if loop_mode == "shuffle_blocks" and len(order) > 1:
        rng.shuffle(order)
        if prev_last is not None and order[0] == prev_last:
            order.append(order.pop(0))
    for top in order:
        it = items[top]
        if it.get("type") == "block":
            inner = [c for c in it.get("items", []) if c.get("type", "cue") in ("cue", "encounter", "scheduler")]
            for rep in range(max(1, int(_sec(it.get("repeat"), 1)))):
                idx = list(range(len(inner)))
                if it.get("shuffle"):
                    rng.shuffle(idx)
                for j in idx:
                    cue = inner[j]
                    for r in range(max(1, int(_sec(cue.get("repeat"), 1)))):
                        yield cue, [top, j], rep + r
        else:
            for r in range(max(1, int(_sec(it.get("repeat"), 1)))):
                yield it, [top], r


def iter_program(
    program: dict,
    layout: Layout,
    t0: int = 0,
    start_path: Optional[list[int]] = None,
    max_iterations: Optional[int] = None,
    state: Optional[_State] = None,
) -> Iterator[CueInstance]:
    """Yields cue instances in time order. Infinite for loop programs."""
    items = program.get("items", [])
    loop_mode = program.get("loop_mode", "loop")
    seed = int(program.get("seed", 1))
    state = state or _State()
    t = t0
    iteration = 0
    empty_iterations = 0
    prev_last: Optional[int] = None
    skipping = bool(start_path)
    while True:
        rng = random.Random(seed * 1_000_003 + iteration)
        produced = False
        last_top = None
        for cue, path, rotation in _flatten_iteration(items, loop_mode, rng, prev_last):
            last_top = path[0]
            if skipping:
                if path[0] != start_path[0]:
                    continue
                if len(start_path) > 1 and (len(path) < 2 or path[1] != start_path[1]):
                    continue
                skipping = False
            start = t - _overlap_for(cue) if produced or iteration > 0 else t
            inst = expand_cue(cue, start, layout, rng, state, rotation, path)
            if inst is None:
                continue
            produced = True
            t = max(inst.next_start, t)
            yield inst
        prev_last = last_top
        iteration += 1
        if skipping:
            # start_path not found; play from the top instead.
            skipping = False
            continue
        empty_iterations = 0 if produced else empty_iterations + 1
        if loop_mode == "once" or empty_iterations > MAX_EMPTY_ITERATIONS:
            return
        if max_iterations is not None and iteration >= max_iterations:
            return


def compile_preview(program: dict, layout: Layout, max_ms: int = 2 * 3600 * 1000) -> dict:
    slots: list[dict] = []
    cues: list[dict] = []
    end = 0
    for inst in iter_program(program, layout, 0, max_iterations=1):
        if inst.start > max_ms:
            break
        cues.append(
            {"cue_id": inst.cue_id, "name": inst.name, "start": inst.start, "end": inst.end, "item_path": inst.item_path}
        )
        slots.extend(s.to_dict(layout) for s in inst.slots)
        end = max(end, inst.next_start)
    return {"duration_ms": end, "slots": slots, "cues": cues}


def compile_cue_preview(cue: dict, layout: Layout) -> dict:
    inst = expand_cue(cue, 0, layout, random.Random(1), _State())
    if inst is None:
        return {"duration_ms": 0, "slots": [], "cues": []}
    return {
        "duration_ms": inst.next_start,
        "slots": [s.to_dict(layout) for s in inst.slots],
        "cues": [{"cue_id": inst.cue_id, "name": inst.name, "start": inst.start, "end": inst.end, "item_path": []}],
    }


def slot_to_events(slot: Slot, anchor: int, layout: Layout) -> list[dict]:
    """Expands a slot into one player event per device bound to its surface."""
    asset = layout.assets[slot.asset_id]
    out = []
    for dev in layout.devices_by_surface.get(slot.surface_id, []):
        out.append(
            {
                "id": f"{slot.cue_index}:{slot.surface_id}:{slot.start}",
                "device_id": dev,
                "t": anchor + slot.start,
                "end": anchor + slot.end,
                "asset_id": asset.id,
                "sha256": asset.sha256,
                "url": asset.url,
                "duration_ms": asset.duration_ms,
                "loop": slot.loop,
                "audio": slot.audio,
                "flip_h": slot.flip_h,
                "motion_reason": slot.motion_reason,
                "rect": slot.rect,
                "transition": slot.transition,
                "cue_id": slot.cue_id,
            }
        )
    return out
