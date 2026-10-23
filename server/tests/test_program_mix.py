from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.api import programs
from app.models import Program


def _scheduler(name, character, clip):
    return {"type":"scheduler", "id":name.lower(), "name":name, "target":{"mode":"all"},
            "roster":[{"id":character.lower(), "name":character, "clips":[clip]}],
            "recycle_min":1.5, "recycle_max":2, "gap_min":4, "gap_max":12,
            "rotation_gap_min":10, "rotation_gap_max":25, "surface_buffer":1}


def test_mix_programs_can_merge_characters_or_shuffle_complete_sets():
    engine = create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        clown = Program(id="clowns", name="Clowns", items=[_scheduler("Clowns", "Creepster", "clown-clip")])
        ghosts = Program(id="ghosts", name="Ghosts", items=[_scheduler("Ghosts", "Wraith", "ghost-clip")])
        db.add(clown); db.add(ghosts); db.commit()
        app = FastAPI(); app.include_router(programs.router)
        app.dependency_overrides[programs.get_session] = lambda: db
        with TestClient(app) as client:
            chars = client.post("/api/programs/mix", json={"name":"Everything", "program_ids":["clowns","ghosts"], "mode":"characters"})
            assert chars.status_code == 200
            item = chars.json()["items"][0]
            assert item["type"] == "scheduler"
            assert {c["name"] for c in item["roster"]} == {"Creepster", "Wraith"}
            assert {c["source_program"] for c in item["roster"]} == {"Clowns", "Ghosts"}
            assert {g["name"] for g in item["groups"]} == {"Clowns", "Ghosts"}
            assert all(len(g["character_ids"]) == 1 for g in item["groups"])
            assert item["motion_mode"] == "auto"

            shows = client.post("/api/programs/mix", json={"name":"Show shuffle", "program_ids":["clowns","ghosts"], "mode":"shows"})
            assert shows.status_code == 200
            assert shows.json()["loop_mode"] == "shuffle_blocks"
            assert len(shows.json()["items"]) == 2
            assert all(i["id"].startswith(("clowns-", "ghosts-")) for i in shows.json()["items"])
            assert {i["source_program"] for i in shows.json()["items"]} == {"Clowns", "Ghosts"}
    engine.dispose()


def test_mix_programs_preserves_existing_families():
    engine = create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        undead = _scheduler("Undead", "Zombie", "zombie-clip")
        undead["roster"].append({"id":"pirate", "name":"Pirate", "clips":["pirate-clip"]})
        undead["groups"] = [
            {"id":"zombies", "name":"Zombies", "character_ids":["zombie"]},
            {"id":"pirates", "name":"Skeleton pirates", "character_ids":["pirate"]},
        ]
        clown = Program(id="clowns", name="Clowns", items=[_scheduler("Clowns", "Creepster", "clown-clip")])
        dead = Program(id="dead", name="Undead", items=[undead])
        db.add(clown); db.add(dead); db.commit()
        app = FastAPI(); app.include_router(programs.router)
        app.dependency_overrides[programs.get_session] = lambda: db
        with TestClient(app) as client:
            response = client.post("/api/programs/mix", json={"name":"Mix", "program_ids":["clowns","dead"], "mode":"characters"})
            assert response.status_code == 200
            scheduler = response.json()["items"][0]
            by_name = {g["name"]: g for g in scheduler["groups"]}
            assert set(by_name) == {"Clowns", "Zombies", "Skeleton pirates"}
            names = {c["id"]: c["name"] for c in scheduler["roster"]}
            assert {names[cid] for cid in by_name["Zombies"]["character_ids"]} == {"Zombie"}
            assert {names[cid] for cid in by_name["Skeleton pirates"]["character_ids"]} == {"Pirate"}
    engine.dispose()
