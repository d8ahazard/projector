"""Create open-ended AtmosFX marathon shows from prepared concrete assets.

The scheduler targets every current and future layout surface. Each run receives a
fresh runtime seed, cycles a character's clip bag 1.5–2 times, leaves randomized
darkness between appearances, then rotates to a different roster character.
"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8000/api"


def request(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


assets = request("GET", "/media")
by_source = {asset.get("source_file_id"): asset for asset in assets}


def aid(source: str) -> str:
    asset = by_source.get(source)
    if not asset or asset.get("status") != "ready" or not asset.get("published"):
        raise RuntimeError(f"AtmosFX source {source} is not ready and published")
    return asset["id"]


def character(name: str, *sources: str) -> dict:
    return {"id": name.lower().replace(" ", "-"), "name": name, "clips": [aid(s) for s in sources]}


def marathon(name: str, roster: list[dict], *, gap=(25, 100), rotation=(75, 300)) -> dict:
    return {
        "type": "scheduler", "id": name.lower().replace(" ", "-"), "name": name,
        "roster": roster,
        # "all" deliberately includes projectors/surfaces added after this show is saved.
        "target": {"mode": "all"},
        "recycle_min": 1.5, "recycle_max": 2.0,
        "gap_min": gap[0], "gap_max": gap[1],
        "rotation_gap_min": rotation[0], "rotation_gap_max": rotation[1],
        "active_characters": 0, "surface_buffer": 3,
        "fit": "contain",
        "transition": {"type": "dip", "duration": 1.2, "easing": "ease-in-out"},
    }


shows = {
    "Clowns — After-Dark Mischief": marathon("Clown roster", [
        character("Creepster", "d05866d27800", "e96a66010ff4"),
        character("Jester", "23935e9472ec", "f88a6533e88c"),
        character("Terrifier", "41e8c2add42c", "9daa269fe38e"),
    ]),
    "Ghostly Apparitions — The House Is Occupied": marathon("Ghostly Apparitions I + II", [
        character("Beckoning Beauty", "8084cb001c7f", "ace9914fcc1c"),
        character("Ghoulish Girl", "8705c57a620a"),
        character("Head of Household", "92000d967f3e"),
        character("Wrathful Wraith", "a7b396d60b62"),
        character("GA2 — Shocking Shade", "6730e8f1f26c", "32e1d963f23d"),
        character("GA2 — Slender Specter", "8b691163b659", "91152d471401"),
    ], gap=(30, 120), rotation=(90, 360)),
    "Friendly Singing Pumpkins — Window Jamboree": marathon("Singing pumpkin roster", [
        character("Singing Pumpkin Trio", "58904cb40c75", "9a0411b95b01"),
        character("Solo Singing Pumpkin", "e1ae21779c0d"),
        character("Funny Faces", "b7744d0c2c91", "7cdfd549b676"),
    ], gap=(35, 130), rotation=(120, 420)),
}

program_ids = {
    "Clowns — After-Dark Mischief": "2373e35524a8",
    "Ghostly Apparitions — The House Is Occupied": "abbd7049fb25",
    "Friendly Singing Pumpkins — Window Jamboree": "6d8a14d28648",
}
existing = {program["id"]: program for program in request("GET", "/programs")}
result = []
for name, scheduler in shows.items():
    body = {"name": name, "loop_mode": "loop", "seed": 20261020, "items": [scheduler]}
    known_id = program_ids.get(name)
    if known_id in existing:
        program = request("PUT", f"/programs/{known_id}", body)
    else:
        program = request("POST", "/programs", body)
    preview = request("GET", f"/programs/{program['id']}/preview?max_minutes=180")
    preflight = request("GET", f"/programs/{program['id']}/preflight")
    result.append({"id": program["id"], "name": name, "rotation_ms": preview["duration_ms"],
                   "appearances": len(preview["slots"]), "preflight": preflight})

print(json.dumps(result, indent=2))
