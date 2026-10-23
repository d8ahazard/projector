import itertools

import pytest

from app.compiler import (
    AssetInfo,
    Layout,
    SurfaceInfo,
    compile_preview,
    iter_program,
    resolve_surface_variant,
    required_assets,
    slot_to_events,
)
from app.geometry import Rect


@pytest.fixture
def layout():
    # Three 48x72 windows, 24" apart (2 ft gaps).
    surfaces = {
        "L": SurfaceInfo("L", Rect(0, 0, 48, 72), ["left", "front"], 0),
        "M": SurfaceInfo("M", Rect(72, 0, 48, 72), ["front"], 1),
        "R": SurfaceInfo("R", Rect(144, 0, 48, 72), ["right", "front"], 2),
    }
    assets = {
        "ghost": AssetInfo("ghost", 10_000, 1280, 720, "g", "/g.mp4", ["ghost"], "halloween"),
        "skull": AssetInfo("skull", 4_000, 1280, 720, "s", "/s.mp4", ["skeleton"], "halloween"),
        "bats": AssetInfo("bats", 6_000, 1280, 720, "b", "/b.mp4", ["bats", "ghost"], "halloween"),
        "snow": AssetInfo("snow", 8_000, 1280, 720, "n", "/n.mp4", ["snow"], "christmas"),
    }
    return Layout(surfaces, {"L": ["devL"], "M": ["devM"], "R": ["devR"]}, assets)


def cue(**kw):
    base = {"type": "cue", "id": kw.pop("id", "c"), "clips": ["ghost"], "pattern": "together",
            "transition": {"type": "cut", "duration": 0}}
    base.update(kw)
    return base


def program(items, loop_mode="once", seed=1):
    return {"items": items, "loop_mode": loop_mode, "seed": seed}


def first(prog, layout):
    return next(iter_program(prog, layout))


def test_together_all_start_at_once(layout):
    inst = first(program([cue()]), layout)
    assert {s.surface_id for s in inst.slots} == {"L", "M", "R"}
    assert all(s.start == 0 and s.end == 10_000 for s in inst.slots)
    assert inst.end == 10_000


def test_regular_cue_exposes_live_roster_and_movement_labels(layout):
    inst = first(program([cue(name="Window ghost", source_program="Ghost show")]), layout)
    assert inst.details["characters"] == [{
        "id": "c", "name": "Window ghost", "source_program": "Ghost show", "appearances_total": 3,
    }]
    assert all(slot.character_id == "c" for slot in inst.slots)
    assert all(slot.character_name == "Window ghost" for slot in inst.slots)
    assert all(slot.source_program == "Ghost show" for slot in inst.slots)


def test_each_assigns_different_clips(layout):
    inst = first(program([cue(clips=["ghost", "skull", "bats"], pattern="each")]), layout)
    by_surface = {s.surface_id: s.asset_id for s in inst.slots}
    assert by_surface == {"L": "ghost", "M": "skull", "R": "bats"}
    assert inst.end == 10_000


def test_sequence_after_previous_with_gap(layout):
    c = cue(pattern="sequence", spacing={"mode": "after", "gap": -1.0}, clips=["skull"])
    inst = first(program([c]), layout)
    starts = {s.surface_id: s.start for s in inst.slots}
    assert starts == {"L": 0, "M": 3_000, "R": 6_000}


def test_sequence_right_to_left_stagger(layout):
    c = cue(pattern="sequence", direction="right-left", spacing={"mode": "stagger", "stagger": 2})
    inst = first(program([c]), layout)
    starts = {s.surface_id: s.start for s in inst.slots}
    assert starts == {"R": 0, "M": 2_000, "L": 4_000}


def test_sequence_travel_timing(layout):
    # Ghost exits at 3s, gap is 24" = 2 ft at 4 ft/s = 0.5s.
    c = cue(pattern="sequence", spacing={"mode": "travel", "speed": 4, "exit_at": 3, "enter_at": 0.5})
    inst = first(program([c]), layout)
    starts = {s.surface_id: s.start for s in inst.slots}
    assert starts["L"] == 0
    assert starts["M"] == 3_000 + 500 - 500
    assert starts["R"] == starts["M"] + 3_000


def test_group_targeting(layout):
    inst = first(program([cue(target={"mode": "group", "group": "right"})]), layout)
    assert [s.surface_id for s in inst.slots] == ["R"]


def test_span_crops_each_surface(layout):
    c = cue(pattern="span", fit="stretch")
    inst = first(program([c]), layout)
    rects = {s.surface_id: s.rect[0] for s in inst.slots}
    # World bounds 0..192 wide, each surface is 48 wide.
    assert rects["L"]["x"] == pytest.approx(0)
    assert rects["L"]["w"] == pytest.approx(192 / 48)
    assert rects["M"]["x"] == pytest.approx(-72 / 48)
    assert rects["R"]["x"] == pytest.approx(-144 / 48)


def test_span_keyframes(layout):
    kfs = [{"t": 0, "x": -0.5, "y": 0, "w": 0.5, "h": 1}, {"t": 5, "x": 1, "y": 0, "w": 0.5, "h": 1}]
    inst = first(program([cue(pattern="span", keyframes=kfs)]), layout)
    r = next(s for s in inst.slots if s.surface_id == "L").rect
    assert len(r) == 2 and r[1]["t"] == 5_000
    assert r[0]["x"] == pytest.approx(-96 / 48)


def test_ping_pong_does_not_double_turnaround(layout):
    c = cue(pattern="ping-pong", passes=2, clips=["skull"], spacing={"mode": "after"})
    inst = first(program([c]), layout)
    order = [s.surface_id for s in sorted(inst.slots, key=lambda s: s.start)]
    assert order == ["L", "M", "R", "M", "L"]


def test_random_one_never_repeats_immediately(layout):
    c = cue(pattern="random-one", passes=20, clips=["skull"])
    inst = first(program([c]), layout)
    order = [s.surface_id for s in sorted(inst.slots, key=lambda s: s.start)]
    assert len(order) == 20
    assert all(a != b for a, b in zip(order, order[1:]))


def test_seeded_randomness_reproducible(layout):
    c = cue(pattern="random-one", passes=10, clips=[], pool={"season": "halloween"})
    a = [(s.surface_id, s.asset_id) for s in first(program([c], seed=7), layout).slots]
    b = [(s.surface_id, s.asset_id) for s in first(program([c], seed=7), layout).slots]
    assert a == b


def test_pool_by_tag(layout):
    c = cue(clips=[], pool={"tags": ["ghost"]}, pattern="each")
    inst = first(program([c]), layout)
    assert {s.asset_id for s in inst.slots} <= {"ghost", "bats"}
    assert required_assets(program([c]), layout) == {"ghost", "bats"}


def test_scene_choice_resolves_orientation_per_calibrated_surface():
    surfaces = {
        "wall": SurfaceInfo("wall", Rect(0, 0, 96, 54), [], 0, "horizontal"),
        "window": SurfaceInfo("window", Rect(120, 0, 48, 72), [], 1, "vertical"),
    }
    assets = {
        "ghost-h": AssetInfo("ghost-h", 5000, 1280, 720, tags=["ghost"], scene_key="same-ghost", orientation="horizontal"),
        "ghost-v": AssetInfo("ghost-v", 5000, 720, 1280, tags=["ghost"], scene_key="same-ghost", orientation="vertical"),
    }
    calibrated = Layout(surfaces, {"wall": ["p1"], "window": ["p2"]}, assets)
    show = program([cue(clips=["ghost-h"], pattern="together")])
    inst = first(show, calibrated)
    assert {slot.surface_id: slot.asset_id for slot in inst.slots} == {"wall": "ghost-h", "window": "ghost-v"}
    assert required_assets(show, calibrated) == {"ghost-h", "ghost-v"}
    assert required_assets(show, calibrated, {"wall"}) == {"ghost-h"}
    assert required_assets(show, calibrated, {"window"}) == {"ghost-v"}


def test_scene_choice_ignores_superseded_ready_sibling():
    surface = SurfaceInfo("window", Rect(0, 0, 48, 72), orientation="vertical")
    assets = {
        "ghost-h": AssetInfo("ghost-h", 5000, 1280, 720, tags=["ghost"], scene_key="same-ghost", orientation="horizontal"),
        "ghost-v-old": AssetInfo("ghost-v-old", 5000, 720, 1280, tags=["ghost"], scene_key="same-ghost", orientation="vertical", current_variant=False),
        "ghost-v-current": AssetInfo("ghost-v-current", 5000, 720, 1280, tags=["ghost"], scene_key="same-ghost", orientation="vertical", current_variant=True),
    }
    calibrated = Layout({"window": surface}, {"window": ["p1"]}, assets)
    assert resolve_surface_variant("ghost-h", surface, calibrated) == "ghost-v-current"


def test_side_mounted_surface_fits_portrait_video_to_full_rotated_canvas():
    surface = SurfaceInfo("window", Rect(0, 0, 16, 9), [], 0, "vertical", 9 / 16)
    assets = {
        "ghost-v": AssetInfo("ghost-v", 5000, 1080, 1920, orientation="vertical"),
    }
    calibrated = Layout({"window": surface}, {"window": ["p1"]}, assets)
    slot = first(program([cue(clips=["ghost-v"], fit="contain")]), calibrated).slots[0]
    assert slot.rect == [{"t": 0, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "opacity": 1.0}]


def test_side_mounted_surface_center_crops_when_no_portrait_variant_exists():
    surface = SurfaceInfo("window", Rect(0, 0, 16, 9), [], 0, "vertical", 9 / 16)
    assets = {
        "pumpkin-h": AssetInfo("pumpkin-h", 5000, 1920, 1080, orientation="horizontal"),
    }
    calibrated = Layout({"window": surface}, {"window": ["p1"]}, assets)
    slot = first(program([cue(clips=["pumpkin-h"], fit="contain")]), calibrated).slots[0]
    rect = slot.rect[0]
    assert rect["h"] == pytest.approx(1.0)
    assert rect["w"] > 1.0 and rect["x"] < 0.0


def test_marathon_scheduler_uses_every_surface_and_cycles_clips_before_reuse(layout):
    scheduler = {
        "type": "scheduler", "id": "night", "name": "Night haunt", "target": {"mode": "all"},
        "roster": [{"id": "specter", "name": "Specter", "clips": ["ghost", "skull", "bats"]}],
        "recycle_min": 2, "recycle_max": 2, "gap_min": 10, "gap_max": 10,
        "rotation_gap_min": 30, "rotation_gap_max": 30,
    }
    inst = first(program([scheduler], seed=19), layout)
    assert len(inst.slots) == 6
    assert {s.surface_id for s in inst.slots} == {"L", "M", "R"}
    assert {s.asset_id for s in inst.slots[:3]} == {"ghost", "skull", "bats"}
    assert {s.asset_id for s in inst.slots[3:]} == {"ghost", "skull", "bats"}
    assert all(a.surface_id != b.surface_id for a, b in zip(inst.slots, inst.slots[1:]))
    assert all(a.end <= b.start for a, b in zip(inst.slots, inst.slots[1:]))
    assert inst.next_start == inst.end + 30_000
    assert required_assets(program([scheduler]), layout) == {"ghost", "skull", "bats"}


def test_marathon_scheduler_rotates_characters_and_is_seeded(layout):
    scheduler = {
        "type": "scheduler", "id": "night", "target": {"mode": "all"},
        "roster": [
            {"id": "ghost", "name": "Ghost", "clips": ["ghost"]},
            {"id": "bones", "name": "Bones", "clips": ["skull"]},
        ],
        "active_characters": 1,
        "recycle_min": 1, "recycle_max": 1, "gap_min": 0, "gap_max": 0,
        "rotation_gap_min": 0, "rotation_gap_max": 0,
    }
    a = list(iter_program(program([scheduler], loop_mode="loop", seed=77), layout, max_iterations=4))
    b = list(iter_program(program([scheduler], loop_mode="loop", seed=77), layout, max_iterations=4))
    assert [(i.name, i.slots[0].surface_id) for i in a] == [(i.name, i.slots[0].surface_id) for i in b]
    assert all(x.name != y.name for x, y in zip(a, a[1:]))


def test_marathon_character_bag_uses_every_character_before_reuse(layout):
    one = Layout({"L": layout.surfaces["L"]}, {"L": ["devL"]}, layout.assets)
    scheduler = {
        "type":"scheduler", "id":"bag", "target":{"mode":"all"}, "active_characters":1,
        "roster":[
            {"id":"a","name":"A","clips":["ghost"]},
            {"id":"b","name":"B","clips":["skull"]},
            {"id":"c","name":"C","clips":["bats"]},
        ],
        "recycle_min":1, "recycle_max":1, "gap_min":0, "gap_max":0,
        "rotation_gap_min":0, "rotation_gap_max":0,
    }
    rotations = list(itertools.islice(iter_program(program([scheduler], loop_mode="loop", seed=31), one), 4))
    first_cycle = [inst.details["characters"][0]["id"] for inst in rotations[:3]]
    assert len(set(first_cycle)) == 3
    assert rotations[3].details["characters"][0]["id"] != rotations[2].details["characters"][0]["id"]


def test_marathon_collection_group_finishes_its_cast_before_switching(layout):
    one = Layout({"L": layout.surfaces["L"]}, {"L": ["devL"]}, layout.assets)
    scheduler = {
        "type":"scheduler", "id":"grouped", "target":{"mode":"all"},
        "roster":[
            {"id":"clown-a","name":"Clown A","clips":["ghost"]},
            {"id":"clown-b","name":"Clown B","clips":["skull"]},
            {"id":"ghost-a","name":"Ghost A","clips":["bats"]},
            {"id":"ghost-b","name":"Ghost B","clips":["ghost"]},
        ],
        "groups":[
            {"id":"clowns","name":"Clowns","character_ids":["clown-a","clown-b"],"active_characters":1},
            {"id":"ghosts","name":"Ghosts","character_ids":["ghost-a","ghost-b"],"active_characters":1},
        ],
        "recycle_min":1,"recycle_max":1,"gap_min":0,"gap_max":0,
        "rotation_gap_min":0,"rotation_gap_max":0,"group_gap_min":20,"group_gap_max":20,
    }
    rotations = list(itertools.islice(iter_program(program([scheduler], loop_mode="loop", seed=41), one), 3))
    groups = [inst.details["collection_group"]["id"] for inst in rotations]
    chars = [inst.details["characters"][0]["id"] for inst in rotations]
    assert groups[0] == groups[1] and groups[2] != groups[1]
    assert chars[0] != chars[1]
    assert rotations[2].start - rotations[1].end == 20_000


def test_odd_collection_group_keeps_two_compatible_lanes_active(layout):
    scheduler = {
        "type":"scheduler", "id":"odd-group", "target":{"mode":"all"},
        "roster":[
            {"id":"a","name":"A","clips":["ghost"]},
            {"id":"b","name":"B","clips":["skull"]},
            {"id":"c","name":"C","clips":["bats"]},
        ],
        "groups":[{"id":"family","name":"Family","character_ids":["a","b","c"],"active_characters":2}],
        "recycle_min":1,"recycle_max":1,"gap_min":0,"gap_max":0,
        "rotation_gap_min":0,"rotation_gap_max":0,
    }
    rotations = list(itertools.islice(iter_program(program([scheduler], loop_mode="loop", seed=8), layout), 2))
    assert [len(x.details["characters"]) for x in rotations] == [2, 2]
    first_ids = {x["id"] for x in rotations[0].details["characters"]}
    second_ids = {x["id"] for x in rotations[1].details["characters"]}
    assert len(first_ids | second_ids) == 3
    assert rotations[1].details["collection_group"]["complete"] is True


def test_single_clip_character_appears_once_even_with_high_recycle_ratio(layout):
    scheduler = {
        "type":"scheduler", "id":"strict", "target":{"mode":"all"},
        "roster":[{"id":"hands","name":"Hands","clips":["ghost"]}],
        "recycle_min":3,"recycle_max":3,"gap_min":0,"gap_max":0,
        "rotation_gap_min":0,"rotation_gap_max":0,
    }
    inst = first(program([scheduler], seed=5), layout)
    assert {slot.character_id for slot in inst.slots} == {"hands"}
    assert len(inst.slots) == 1
    assert inst.name == "Hands"
    assert inst.details["characters"] == [{"id":"hands","name":"Hands","source_program":"","appearances_total":1}]


def test_multiple_clip_character_does_not_repeat_an_action_back_to_back(layout):
    scheduler = {
        "type":"scheduler", "id":"varied", "target":{"mode":"all"},
        "roster":[{"id":"ghost","name":"Ghost","clips":["ghost","skull"]}],
        "recycle_min":2,"recycle_max":2,"gap_min":0,"gap_max":0,
        "rotation_gap_min":0,"rotation_gap_max":0,
    }
    inst = first(program([scheduler], seed=3), layout)
    ids = [slot.asset_id for slot in sorted(inst.slots, key=lambda slot: slot.start)]
    assert len(ids) == 4
    assert all(left != right for left, right in zip(ids, ids[1:]))


def test_marathon_auto_motion_orders_surfaces_and_flips_opposite_entrance(layout):
    layout.assets["ghost"].motion = {
        "status": "ready", "enter_edge": "right", "exit_edge": "right",
        "travel": "right-left", "confidence": 0.91,
    }
    layout.assets["skull"].motion = dict(layout.assets["ghost"].motion)
    scheduler = {
        "type":"scheduler", "id":"motion", "target":{"mode":"all"}, "active_characters":1,
        "motion_mode":"auto", "roster":[{"id":"ghost","name":"Ghost","clips":["ghost","skull"]}],
        "recycle_min":1.5, "recycle_max":1.5, "gap_min":0, "gap_max":0,
        "rotation_gap_min":0, "rotation_gap_max":0,
    }
    inst = first(program([scheduler], seed=7), layout)
    assert len(inst.slots) == 3
    xs = [layout.surfaces[slot.surface_id].rect.x for slot in inst.slots]
    assert len(set(xs)) == 3
    for previous, current in zip(inst.slots, inst.slots[1:]):
        desired_right = layout.surfaces[current.surface_id].rect.x > layout.surfaces[previous.surface_id].rect.x
        # Native entrance is from the right (leftward), so rightward room travel flips it.
        assert current.flip_h is desired_right
        event = slot_to_events(current, 1000, layout)[0]
        assert event["flip_h"] is current.flip_h
        assert "aligned" in event["motion_reason"]


def test_smart_choreography_prefers_approved_compatible_beats(layout):
    layout.assets["ghost"].motion = {"approval":"approved", "enter_edge":"left", "exit_edge":"right", "dx":.8,
                                       "travel":"left-right", "confidence":.95, "mirror_safe":True}
    layout.assets["skull"].motion = {"approval":"approved", "enter_edge":"left", "exit_edge":"right", "dx":.7,
                                       "travel":"left-right", "confidence":.92, "mirror_safe":True}
    layout.assets["bats"].motion = {"approval":"review", "enter_edge":"none", "exit_edge":"none", "dx":0,
                                      "travel":"stationary", "confidence":.2, "mirror_safe":True}
    scheduler = {"type":"scheduler", "id":"smart", "target":{"mode":"all"}, "active_characters":1,
                 "smart_choreography":True, "roster":[{"id":"specter", "name":"Specter", "clips":["ghost","skull","bats"]}],
                 "recycle_min":1, "recycle_max":1, "gap_min":0, "gap_max":0, "rotation_gap_min":0, "rotation_gap_max":0}
    inst = first(program([scheduler], seed=13), layout)
    assert len(inst.slots) == 3
    assert any("linked beat" in slot.motion_reason for slot in inst.slots[1:])
    assert inst.details["smart_choreography"] is True


def test_marathon_scheduler_has_no_two_window_limit(layout):
    surfaces = {f"S{i}": SurfaceInfo(f"S{i}", Rect(i * 60, 0, 48, 72), [], i) for i in range(12)}
    many = Layout(surfaces, {sid: [f"dev-{sid}"] for sid in surfaces}, layout.assets)
    scheduler = {
        "type": "scheduler", "id": "many", "target": {"mode": "all"},
        "roster": [{"id": "ghost", "name": "Ghost", "clips": ["ghost", "skull", "bats"]}],
        "recycle_min": 4, "recycle_max": 4, "gap_min": 0, "gap_max": 0,
        "rotation_gap_min": 0, "rotation_gap_max": 0,
    }
    inst = first(program([scheduler], seed=9), many)
    assert len(inst.slots) == 12
    assert {slot.surface_id for slot in inst.slots} == set(surfaces)


def test_marathon_scheduler_packs_concurrent_characters_without_surface_collisions(layout):
    scheduler = {
        "type": "scheduler", "id": "busy", "target": {"mode": "all"},
        "roster": [
            {"id": "ghost", "name": "Ghost", "clips": ["ghost"]},
            {"id": "bones", "name": "Bones", "clips": ["skull"]},
            {"id": "bats", "name": "Bats", "clips": ["bats"]},
        ],
        "recycle_min": 1, "recycle_max": 1, "gap_min": 0, "gap_max": 0,
        "rotation_gap_min": 0, "rotation_gap_max": 0, "surface_buffer": 2,
    }
    inst = first(program([scheduler], seed=11), layout)
    assert len(inst.slots) == 3
    assert len({slot.surface_id for slot in inst.slots}) == 3
    assert {slot.start for slot in inst.slots} == {0}


def test_wipe_transition_delays_and_overlap(layout):
    c1 = cue(id="a")
    c2 = cue(id="b", clips=["skull"], transition={"type": "wipe", "duration": 1.0, "direction": "right", "spread": 2})
    insts = list(iter_program(program([c1, c2]), layout))
    second = insts[1]
    starts = {s.surface_id: s.start for s in second.slots}
    # Wipe overlaps the previous cue by its duration, then adds spatial delay.
    assert starts["L"] == 9_000
    assert starts["M"] == 10_000
    assert starts["R"] == 11_000
    assert all(s.transition["type"] == "wipe" for s in second.slots)


def test_cascade_uses_local_transition(layout):
    c = cue(transition={"type": "cascade", "duration": 0.5, "spread": 1, "local": "crossfade"})
    inst = first(program([c]), layout)
    assert {s.transition["type"] for s in inst.slots} == {"crossfade"}


def test_duration_rules(layout):
    inst = first(program([cue(duration={"mode": "fixed", "seconds": 25})]), layout)
    assert inst.end == 25_000 and inst.slots[0].loop
    inst = first(program([cue(duration={"mode": "loops", "loops": 3})]), layout)
    assert inst.end == 30_000


def test_hold_and_repeat(layout):
    prog = program([cue(clips=["skull"], repeat=3, hold=1)])
    insts = list(iter_program(prog, layout))
    assert [i.start for i in insts] == [0, 5_000, 10_000]


def test_blocks_repeat_and_loop(layout):
    block = {"type": "block", "id": "b", "name": "B", "repeat": 2,
             "items": [cue(id="x", clips=["skull"]), cue(id="y", clips=["bats"])]}
    insts = list(iter_program(program([block]), layout))
    assert [i.cue_id for i in insts] == ["x", "y", "x", "y"]
    looping = iter_program(program([block], loop_mode="loop"), layout)
    got = [i.cue_id for i in itertools.islice(looping, 9)]
    assert got == ["x", "y"] * 4 + ["x"]


def test_shuffle_blocks_covers_all(layout):
    items = [cue(id=str(i), clips=["skull"]) for i in range(5)]
    insts = list(iter_program(program(items, loop_mode="shuffle_blocks"), layout, max_iterations=1))
    assert sorted(i.cue_id for i in insts) == ["0", "1", "2", "3", "4"]


def test_start_path_jumps(layout):
    items = [cue(id="a"), cue(id="b"), cue(id="c")]
    insts = list(iter_program(program(items), layout, t0=1_000, start_path=[1]))
    assert [i.cue_id for i in insts] == ["b", "c"]
    assert insts[0].start == 1_000


def test_empty_program_terminates(layout):
    assert list(iter_program(program([], loop_mode="loop"), layout)) == []
    assert list(iter_program(program([cue(clips=["missing"])], loop_mode="loop"), layout)) == []


def test_offsets_override(layout):
    inst = first(program([cue(offsets={"R": 1.5})]), layout)
    assert {s.surface_id: s.start for s in inst.slots}["R"] == 1_500


def test_preview_and_events(layout):
    prev = compile_preview(program([cue(), cue(id="d", clips=["skull"])], loop_mode="loop"), layout)
    assert prev["duration_ms"] == 14_000
    assert len(prev["cues"]) == 2
    slot = first(program([cue()]), layout).slots[0]
    evs = slot_to_events(slot, 1_000_000, layout)
    assert evs[0]["t"] == 1_000_000 and evs[0]["sha256"] == "g" and evs[0]["device_id"] == "devL"
