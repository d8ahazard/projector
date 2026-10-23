"""Generate a long, varied program from tags/season so hours of footage need no hand-editing."""

from __future__ import annotations

import random
import uuid

from .compiler import Layout, compile_cue_preview

DEFAULT_PATTERNS = ["together", "each", "sequence", "random-one", "span"]
DEFAULT_TRANSITIONS = ["dip", "crossfade", "cascade", "wipe"]


def _transition(kind: str, rng: random.Random) -> dict:
    if kind == "wipe":
        return {"type": "wipe", "duration": 1.0, "direction": rng.choice(["left", "right"]), "spread": 1.5}
    if kind == "cascade":
        return {"type": "cascade", "duration": 0.8, "direction": rng.choice(["left", "right"]), "spread": 1.5, "local": "dip"}
    if kind in ("ripple-out", "ripple-in"):
        return {"type": kind, "duration": 0.8, "spread": 1.2, "local": "dip"}
    if kind == "crossfade":
        return {"type": "crossfade", "duration": 1.2}
    if kind == "cut":
        return {"type": "cut", "duration": 0}
    return {"type": "dip", "duration": 0.8}


def autofill(
    layout: Layout,
    hours: float,
    tags: list[str],
    season: str,
    patterns: list[str] | None = None,
    transitions: list[str] | None = None,
    seed: int = 1,
) -> list[dict]:
    rng = random.Random(seed)
    patterns = patterns or DEFAULT_PATTERNS
    transitions = transitions or DEFAULT_TRANSITIONS
    tagset = {t.lower() for t in tags}
    pool = [
        a.id
        for a in layout.assets.values()
        if a.kind == "video"
        and (not season or a.season.lower() == season.lower())
        and (not tagset or tagset.intersection(t.lower() for t in a.tags))
    ]
    if not pool:
        return []
    n_surfaces = max(1, len(layout.surfaces))
    if n_surfaces < 2:
        patterns = [p for p in patterns if p in ("together", "random-one")] or ["together"]

    target_ms = int(hours * 3600 * 1000)
    total = 0
    items: list[dict] = []
    bag: list[str] = []

    def take() -> str:
        nonlocal bag
        if not bag:
            bag = list(pool)
            rng.shuffle(bag)
        return bag.pop()

    guard = 0
    while total < target_ms and guard < 5000:
        guard += 1
        pattern = rng.choice(patterns)
        cue: dict = {
            "type": "cue",
            "id": uuid.uuid4().hex[:8],
            "pattern": pattern,
            "target": {"mode": "all"},
            "fit": "contain",
            "transition": _transition(rng.choice(transitions), rng),
        }
        if pattern == "each":
            cue["clips"] = [take() for _ in range(min(n_surfaces, 4))]
        else:
            cue["clips"] = [take()]
        if pattern == "sequence":
            cue["direction"] = rng.choice(["left-right", "right-left"])
            cue["spacing"] = rng.choice([{"mode": "travel", "speed": 4}, {"mode": "after", "gap": -1.0}])
        elif pattern == "random-one":
            cue["passes"] = rng.randint(2, 4)
        elif pattern == "span":
            cue["fit"] = "cover"
        prev = compile_cue_preview(cue, layout)
        dur = prev["duration_ms"]
        if dur <= 0:
            continue
        cue["name"] = f"{pattern} #{len(items) + 1}"
        items.append(cue)
        total += dur
    return items
