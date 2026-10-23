"""Build complete, variant-aware projection rosters from the source catalogue."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from ..models import SourceFile
from .library import effective


EXCLUDED = (
    "singing pumpkin", "jack-o-lantern jamboree", "jack-o_-lantern jamboree",
    "legends of halloween", "inferno", "trick r treat", "trick 'r treat",
    "trick ’r treat", "easter",
)


def _words(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def eligible(row: SourceFile) -> bool:
    meta = effective(row)
    text = _words(" ".join((row.relative_path, row.collection, str(meta.get("title", "")),
                            str(meta.get("character", "")))))
    return (row.status == "indexed" and not meta.get("buffer") and not meta.get("roster_excluded")
            and meta.get("season") != "easter"
            and not any(_words(term) in text for term in EXCLUDED))


def identity(row: SourceFile) -> tuple[str, str, str]:
    """Return family, character and action names independent of target/orientation."""
    meta = effective(row)
    collection = row.collection
    path = row.relative_path
    if "macabre manor" in _words(collection + " " + path):
        if "evil twins" in path.lower(): character = "Evil Twins"
        elif "family unit" in path.lower(): character = "Manor Family"
        elif "lady of the house" in path.lower(): character = "Lady of the House"
        elif "lord of the manor" in path.lower(): character = "Lord of the Manor"
        else: character = "Macabre Manor"
        action = str(meta.get("action") or meta.get("title") or row.title)
        action = re.sub(r"(?i)^mm\s+(twins|family|lady|lord|transition)\s+", "", action)
        action = re.sub(r"(?i)^(twins|family|lady|lord)\s+", "", action)
        action = re.sub(r"\s*\([^)]*(?:hollusion|window|tv)[^)]*\)\s*$", "", action, flags=re.I)
        if "transition" in path.lower() or "transition" in str(meta.get("action", "")).lower():
            character = "Manor Transitions"
            action = re.sub(r"(?i)^mm\s+", "", str(meta.get("action") or row.title))
        return "Macabre Manor", character, action.strip()
    if "blood walls" in _words(collection + " " + path):
        action = str(meta.get("action") or meta.get("title") or row.title)
        base = re.sub(r"(?i)\s+(wall|window)(?:\s*-.*)?$", "", str(meta.get("title") or row.title)).strip()
        character = re.sub(r"(?i)\s+(lightning|spotlight|strobe|reversed)$", "", base).strip()
        return "Blood Walls", character or "Blood Walls", action
    family = re.sub(r"(?i)^atmosfx\s*[-_]*\s*", "", collection).strip() or collection
    character = str(meta.get("character") or family).strip()
    action = str(meta.get("action") or meta.get("title") or row.title).strip()
    return family, character, action


def semantic_key(row: SourceFile) -> tuple[str, str, str]:
    family, character, action = identity(row)
    # Filename families sometimes include a second legacy copy whose only
    # difference is the MM prefix or a target name in parentheses.
    return _words(family), _words(character), _words(action)


def _variant_score(row: SourceFile) -> tuple[int, int, int, str]:
    meta = effective(row)
    targets = set(meta.get("targets") or [])
    target = 0 if "window" in targets else 1 if "wall" in targets else 2 if "tv" in targets else 3
    orientation = 0 if meta.get("orientation") == "horizontal" else 1
    canonical_name = 0 if re.search(r"(?i)(?:^|[/_])MM[_ -]", row.relative_path) else 1
    return target, orientation, canonical_name, row.relative_path


@dataclass
class RosterAction:
    family: str
    character: str
    action: str
    canonical: SourceFile
    variants: list[SourceFile]


def select_actions(rows: list[SourceFile], include_all: bool = False) -> list[RosterAction]:
    semantic: dict[tuple[str, str, str], list[SourceFile]] = defaultdict(list)
    for row in rows:
        meta = effective(row)
        allowed = (row.status == "indexed" and not meta.get("buffer") and not meta.get("roster_excluded")) if include_all else eligible(row)
        if allowed:
            semantic[semantic_key(row)].append(row)
    actions: list[RosterAction] = []
    for candidates in semantic.values():
        family, character, action = identity(min(candidates, key=_variant_score))
        # Choose one scene-key family, preferring its window masters. This
        # removes alternate filenames without collapsing merely-similar files.
        by_scene: dict[str, list[SourceFile]] = defaultdict(list)
        for row in candidates: by_scene[row.scene_key].append(row)
        variants = min(by_scene.values(), key=lambda group: min(_variant_score(r) for r in group))
        canonical = min(variants, key=_variant_score)
        actions.append(RosterAction(family, character, action, canonical, sorted(variants, key=_variant_score)))
    return sorted(actions, key=lambda x: (_words(x.family), _words(x.character), _words(x.action)))


def preparation_sources(actions: list[RosterAction]) -> list[SourceFile]:
    """Canonical clip plus an orientation sibling, never every delivery duplicate."""
    selected: list[SourceFile] = []
    for action in actions:
        selected.append(action.canonical)
        vertical = next((r for r in action.variants if effective(r).get("orientation") == "vertical"
                         and set(effective(r).get("targets") or []) == set(effective(action.canonical).get("targets") or [])), None)
        if vertical and vertical.id != action.canonical.id:
            selected.append(vertical)
    return list({row.id: row for row in selected}.values())


def build_scheduler(actions: list[RosterAction], asset_ids: dict[str, str], previous: dict | None = None) -> dict:
    previous = previous or {}
    chars: dict[tuple[str, str], dict] = {}
    for action in actions:
        aid = asset_ids.get(action.canonical.id)
        if not aid: continue
        key = (action.family, action.character)
        item = chars.setdefault(key, {"id": "catalog-" + _words("-".join(key)).replace(" ", "-")[:70],
                                      "name": action.character, "clips": [], "actions": []})
        if aid not in item["clips"]: item["clips"].append(aid)
        item["actions"].append(action.action)
    roster = list(chars.values())
    groups = []
    for family in sorted({key[0] for key in chars}, key=str.lower):
        members = [item["id"] for key, item in chars.items() if key[0] == family]
        groups.append({"id": "family-" + _words(family).replace(" ", "-"), "name": family,
                       "character_ids": members, "active_characters": min(2, len(members))})
    return {
        "type": "scheduler", "id": previous.get("id", "everything-else-catalog"),
        "name": previous.get("name", "Everything Else character scheduler"),
        "roster": roster, "groups": groups, "target": previous.get("target", {"mode": "all"}),
        "recycle_min": previous.get("recycle_min", 1.5), "recycle_max": previous.get("recycle_max", 2.0),
        "gap_min": previous.get("gap_min", 2), "gap_max": previous.get("gap_max", 6),
        "rotation_gap_min": previous.get("rotation_gap_min", 8), "rotation_gap_max": previous.get("rotation_gap_max", 18),
        "group_gap_min": previous.get("group_gap_min", 8), "group_gap_max": previous.get("group_gap_max", 18),
        "active_characters": 0, "surface_buffer": previous.get("surface_buffer", .75),
        "fit": previous.get("fit", "contain"), "transition": previous.get("transition", {"type": "dip", "duration": .8, "easing": "ease-in-out"}),
        "motion_mode": "auto", "smart_choreography": True, "catalog_complete": True,
    }
