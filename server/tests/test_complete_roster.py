from app.media.roster import build_scheduler, eligible, select_actions
from app.media.library import classify
from app.models import SourceFile


def source(path, collection, scene, character, action, *, orientation="horizontal", target="window", buffer=False):
    return SourceFile(relative_path=path, collection=collection, scene_key=scene, title=action,
                      classification={"title": action, "character": character, "action": action,
                                      "orientation": orientation, "targets": [target], "buffer": buffer,
                                      "season": "halloween"})


def test_catalog_roster_excludes_forbidden_sets_and_buffers():
    assert not eligible(source("Pumpkins/a.mp4", "Jack-O-Lantern Jamboree", "a", "Pumpkin", "Sing"))
    assert not eligible(source("Inferno/a.mp4", "Inferno", "a", "Demon", "Burn"))
    assert not eligible(source("Trick R Treat/a.mp4", "Trick R Treat", "a", "Sam", "Walk"))
    assert not eligible(source("Easter/a.mp4", "Easter", "a", "Bunny", "Hop"))
    assert not eligible(source("Ghost/buffer.mp4", "Ghosts", "a", "Ghost", "Float", buffer=True))
    manually_excluded = source("Night Stalkers/Endless.mp4", "Night Stalkers", "n", "Endless Nightmare", "Excerpt")
    manually_excluded.overrides = {"roster_excluded": True}
    assert not eligible(manually_excluded)


def test_catalog_classifies_promotional_and_setup_videos_as_excluded():
    for name in ("Digital Decoration Trailer.mp4", "Projector Setup Instructions.mp4", "How-To Walkthrough.mp4"):
        assert classify(f"AtmosFX/Window/{name}")["roster_excluded"] is True
    assert classify("AtmosFX/Window/Ghostly Encounter.mp4")["roster_excluded"] is False


def test_catalog_roster_deduplicates_variants_but_keeps_distinct_actions():
    rows = [
        source("Macabre/MM - Evil Twins 1/Window - Horizontal/MM_Twins_DoubleTrouble_Win_H.mp4", "AtmosFX Macabre Manor", "mm", "MM - Evil Twins 1", "MM Twins Double Trouble"),
        source("Macabre/MM - Evil Twins 1/Window - Vertical/MM_Twins_DoubleTrouble_Win_V.mp4", "AtmosFX Macabre Manor", "mm", "MM - Evil Twins 1", "MM Twins Double Trouble", orientation="vertical"),
        source("Macabre/MM - Evil Twins 1/Window - Horizontal/Twins - DoubleTrouble (Window).mp4", "AtmosFX Macabre Manor", "legacy", "MM - Evil Twins 1", "Twins Double Trouble (Window)"),
        source("Macabre/MM - Evil Twins 1/Window - Horizontal/MM_Twins_KidsGames_Win_H.mp4", "AtmosFX Macabre Manor", "kids", "MM - Evil Twins 1", "MM Twins Kids Games"),
        source("Blood/Wall/Bloody Massacre Wall - Lightning.mp4", "AtmosFX Blood Walls", "blood", "Bloody Massacre Wall - Lightning", "Bloody Massacre Lightning", target="wall"),
    ]
    actions = select_actions(rows)
    assert len(actions) == 3
    assert {(x.family, x.character, x.action) for x in actions} == {
        ("Macabre Manor", "Evil Twins", "Double Trouble"),
        ("Macabre Manor", "Evil Twins", "Kids Games"),
        ("Blood Walls", "Bloody Massacre", "Bloody Massacre Lightning"),
    }
    assets = {x.canonical.id: "asset-" + str(i) for i, x in enumerate(actions)}
    scheduler = build_scheduler(actions, assets)
    assert scheduler["catalog_complete"] is True
    assert scheduler["smart_choreography"] is True
    assert {x["name"] for x in scheduler["groups"]} == {"Blood Walls", "Macabre Manor"}
    twins = next(x for x in scheduler["roster"] if x["name"] == "Evil Twins")
    assert len(twins["clips"]) == 2
