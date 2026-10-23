import io
import zipfile
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import library
from app.media.roster import select_actions
from app.models import SourceFile


def _video(path: str, collection: str, character: str) -> SourceFile:
    return SourceFile(relative_path=path, collection=collection, scene_key=path, title=character,
                      status="indexed", classification={"title": character, "character": character,
                      "action": character, "targets": ["window"], "orientation": "horizontal",
                      "season": "halloween", "buffer": False})


def test_pack_paths_are_safe_and_pack_roster_includes_named_collections():
    assert library.pack_slug("Ghostly Apparitions II") == "ghostly-apparitions-ii"
    assert library.safe_pack_path("Collection/Window/ghost.mp4").as_posix() == "Collection/Window/ghost.mp4"
    try:
        library.safe_pack_path("../private.mp4")
        assert False, "traversal should be rejected"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400

    # Pack-specific shows include every supplied collection. The exclusions used
    # by the owner's Everything Else program must not silently remove a pack.
    rows = [
        _video("@packs/pumpkins/Pumpkins/sing.mp4", "Jack-O-Lantern Jamboree", "Singing Pumpkins"),
        _video("@packs/ghosts/Ghosts/float.mp4", "Ghostly Apparitions", "Wraith"),
    ]
    assert len(select_actions(rows)) == 1
    assert len(select_actions(rows, include_all=True)) == 2


def test_zip_pack_import_preserves_folders_and_starts_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "PACKS_DIR", tmp_path)
    monkeypatch.setattr(library.worker, "submit", lambda kind, detail=None: SimpleNamespace(id="scan-1"))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Ghosts/Window/Ghost_Win_H.mp4", b"fake-mp4")
        zf.writestr("Ghosts/readme.txt", b"ignored")
    archive.seek(0)
    app = FastAPI()
    app.include_router(library.router)
    with TestClient(app) as client:
        response = client.post("/api/library/packs/import", data={"pack_name": "My Ghost Pack"},
                               files={"files": ("ghosts.zip", archive.getvalue(), "application/zip")})
    assert response.status_code == 200, response.text
    assert response.json()["queued"] == 1
    assert (tmp_path / "my-ghost-pack" / "Ghosts" / "Window" / "Ghost_Win_H.mp4").read_bytes() == b"fake-mp4"
    assert (tmp_path / "my-ghost-pack" / ".projector-pack.json").is_file()
