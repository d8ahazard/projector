"""Program runner: syncs media to players, compiles a rolling schedule, handles live control."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import logging
import random
import secrets
from typing import Iterator, Optional

from sqlmodel import select

from ..compiler import CueInstance, Layout, expand_cue, iter_program, required_assets, slot_to_events, _State
from ..config import SCHEDULE_HORIZON_MS, START_LEAD_MS
from ..db import session_scope
from ..loader import load_layout
from ..models import MediaAsset, Program, Schedule, Setting
from .hub import hub, server_now

log = logging.getLogger(__name__)
SWITCH_LEAD_MS = 1200
CACHE_HORIZON_MS = 4 * 60 * 1000


class Runner:
    def __init__(self) -> None:
        self.state = "idle"
        self.program: Optional[dict] = None
        self.layout: Optional[Layout] = None
        self.gen: Optional[Iterator[CueInstance]] = None
        self.gen_state = _State()
        self.instances: list[CueInstance] = []
        self.exhausted = False
        self.required: list[dict] = []
        self.required_by_device: dict[str, list[dict]] = {}
        self.asset_records: dict[str, dict] = {}
        self.rolling_cache = False
        self._cache_signature: dict[str, tuple[str, ...]] = {}
        self.ready: dict[str, bool] = {}
        self.progress: dict[str, float] = {}
        self.pending_path: Optional[list[int]] = None
        self.started_at = 0
        self.by_schedule: Optional[str] = None
        self.message = ""
        self.runtime_seed: Optional[int] = None
        self._sent_until: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._preview_lock = asyncio.Lock()
        self._preview_waiters: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------ state

    def public_state(self) -> dict:
        now = server_now()
        cur = nxt = None
        for inst in self.instances:
            if inst.start <= now < inst.next_start:
                cur = inst
            elif inst.start > now and nxt is None:
                nxt = inst
        return {
            "state": self.state,
            "program_id": self.program["id"] if self.program else None,
            "program_name": self.program["name"] if self.program else None,
            "started_at": self.started_at,
            "now": now,
            "current": {"cue_id": cur.cue_id, "name": cur.name, "start": cur.start, "end": cur.end, "item_path": cur.item_path, "details": cur.details} if cur else None,
            "next": {"cue_id": nxt.cue_id, "name": nxt.name, "start": nxt.start, "item_path": nxt.item_path, "details": nxt.details} if nxt else None,
            "sync_progress": self.progress if self.state == "syncing" else {},
            "schedule_id": self.by_schedule,
            "message": self.message,
            "runtime_seed": self.runtime_seed,
            "run_sheet": self._run_sheet(now, cur),
        }

    def _run_sheet(self, now: int, current: Optional[CueInstance]) -> dict:
        if not self.program or not self.layout:
            return {"kind": "idle", "roster": [], "active_characters": [], "movements": [], "upcoming_selections": []}

        def walk(items):
            for item in items:
                if item.get("type") == "block":
                    yield from walk(item.get("items", []))
                else:
                    yield item

        schedulers = [item for item in walk(self.program.get("items", [])) if item.get("type") == "scheduler"]
        kind = "marathon" if schedulers else "shuffle" if self.program.get("loop_mode") == "shuffle_blocks" else "program"
        roster = []
        source_programs = []
        for scheduler in schedulers:
            for character in scheduler.get("roster", []):
                source = character.get("source_program", "")
                if source and source not in source_programs:
                    source_programs.append(source)
                roster.append({
                    "id": str(character.get("id", character.get("name", "character"))),
                    "name": character.get("name") or "Character",
                    "source_program": source,
                    "clip_count": len(character.get("clips", [])),
                })
        if not roster:
            for item in walk(self.program.get("items", [])):
                source = str(item.get("source_program") or "")
                if source and source not in source_programs:
                    source_programs.append(source)
                roster.append({"id": str(item.get("id", "cue")), "name": item.get("name") or item.get("pattern", "Cue"),
                               "source_program": source, "clip_count": len(item.get("clips", [])) + len(item.get("tracks", []))})

        active = copy.deepcopy((current.details if current else {}).get("characters", []))
        for character in active:
            cid = character.get("id")
            character["appearances_done"] = sum(
                1 for slot in (current.slots if current else [])
                if slot.character_id == cid and slot.end <= now
            )

        movements = []
        for inst in self.instances:
            for slot in inst.slots:
                if slot.end <= now:
                    continue
                surface = self.layout.surfaces.get(slot.surface_id)
                asset = self.layout.assets.get(slot.asset_id)
                movements.append({
                    "start": slot.start, "end": slot.end,
                    "state": "playing" if slot.start <= now < slot.end else "upcoming",
                    "surface_id": slot.surface_id, "surface_name": (surface.name if surface else "") or slot.surface_id,
                    "asset_id": slot.asset_id, "clip_name": (asset.title if asset else "") or slot.asset_id,
                    "character_id": slot.character_id,
                    "character_name": slot.character_name or inst.name,
                    "source_program": slot.source_program,
                    "cue_name": inst.name,
                    "flip_h": slot.flip_h,
                    "motion_reason": slot.motion_reason,
                })
        movements.sort(key=lambda row: (row["start"], row["surface_name"]))
        upcoming = []
        for inst in self.instances:
            if inst.next_start <= now:
                continue
            row = {"name": inst.name, "start": inst.start, "end": inst.end, "type": inst.details.get("type", "cue")}
            if row not in upcoming:
                upcoming.append(row)
            if len(upcoming) >= 12:
                break
        return {
            "kind": kind,
            "source_programs": source_programs,
            "roster": roster,
            "active_characters": active,
            "collection_group": (current.details if current else {}).get("collection_group"),
            "character_bag_remaining": (current.details if current else {}).get("character_bag_remaining", []),
            "next_rotation": current.next_start if current and current.details.get("type") == "scheduler" else None,
            "movements": movements[:40],
            "upcoming_selections": upcoming,
        }

    async def broadcast(self) -> None:
        await hub.broadcast_ui({"type": "runner", "state": self.public_state()})

    def target_devices(self) -> list[str]:
        if not self.layout:
            return []
        return sorted({d for devs in self.layout.devices_by_surface.values() for d in devs})

    def remember(self, enabled: bool) -> None:
        # Scheduled shows are re-evaluated against the current time after boot.
        value = {"program_id": self.program["id"], "runtime_seed": self.runtime_seed} if enabled and self.program and not self.by_schedule else {}
        with session_scope() as s:
            setting = s.get(Setting, "resume_program") or Setting(key="resume_program")
            setting.value = value; s.add(setting); s.commit()

    async def restore(self) -> None:
        with session_scope() as s:
            setting = s.get(Setting, "resume_program")
            saved = (setting.value or {}) if setting else {}
            pid = saved.get("program_id")
        if pid:
            try:
                if saved.get("runtime_seed") is None:
                    await self.start(pid)
                else:
                    await self.start(pid, runtime_seed=saved["runtime_seed"])
            except Exception:
                log.exception("Could not resume saved program %s", pid)
                self.message = "Saved program could not restart; inspect playback readiness"

    # ------------------------------------------------------------ control

    def _load(self, program_id: str) -> None:
        from ..loader import asset_url
        with session_scope() as s:
            p = s.get(Program, program_id)
            if not p:
                raise ValueError("program not found")
            self.program = p.model_dump()
            self.layout = load_layout(s)
            req_ids = required_assets(self.program, self.layout)
            assets = s.exec(select(MediaAsset).where(MediaAsset.id.in_(req_ids))).all() if req_ids else []
            records = {a.id: {"id": a.id, "sha256": a.sha256, "url": asset_url(a), "size": a.size} for a in assets}
            self.asset_records = records
            self.required = list(records.values())
            self.required_by_device = {}
            for surface_id, device_ids in self.layout.devices_by_surface.items():
                resolved = required_assets(self.program, self.layout, {surface_id})
                for device_id in device_ids:
                    self.required_by_device[device_id] = [records[aid] for aid in sorted(resolved) if aid in records]

    def _reserve_runtime_seed(self, program_id: str) -> int:
        """Return a fresh seed while preserving exact restart recovery.

        Sequential seeds made the first shuffled family look repetitive across
        independent show starts. System entropy gives every new run a genuinely
        different opening; persisting the concrete seed still lets a server
        restart resume the already-planned show exactly.
        """
        key = f"program_runtime_seed:{program_id}"
        with session_scope() as s:
            setting = s.get(Setting, key) or Setting(key=key, value=0)
            previous = int(setting.value or 0)
            value = secrets.randbits(53) or 1
            while value == previous:
                value = secrets.randbits(53) or 1
            setting.value = value
            s.add(setting)
            s.commit()
        return value

    async def start(self, program_id: str, start_path: Optional[list[int]] = None, by_schedule: Optional[str] = None,
                    runtime_seed: Optional[int] = None) -> None:
        from ..preflight import preflight
        with session_scope() as s:
            p=s.get(Program,program_id)
            if not p: raise ValueError("Program not found")
            checks=preflight(p.model_dump(),s)
            if checks["errors"]: raise ValueError("; ".join(checks["errors"]))
        async with self._lock:
            await self._clear_players(0)
            self._load(program_id)
            if any(item.get("type") == "scheduler" for item in self.program.get("items", [])):
                self.runtime_seed = int(runtime_seed) if runtime_seed is not None else self._reserve_runtime_seed(program_id)
                self.program["seed"] = self.runtime_seed
            else:
                self.runtime_seed = None
            self.by_schedule = by_schedule
            self.pending_path = start_path
            self.instances = []
            self.ready = {}
            self.progress = {}
            self._cache_signature = {}
            if not self.required:
                self.state = "idle"
                self.message = "Program has no playable clips (check cues and that media finished processing)."
                await self.broadcast()
                return
            self.rolling_cache = any(item.get("type") == "scheduler" for item in self.program.get("items", []))
            self.state = "syncing"
            self.remember(True)
            self.message = "Sending media to players"
            if self.rolling_cache:
                # Compile a deterministic first horizon so sticks cache only
                # the near-term working set. _begin recompiles the same choices
                # at a fresh absolute start once every online player is ready.
                self.gen_state = _State()
                self.gen = iter_program(self.program, self.layout, server_now() + 60_000, self.pending_path, state=self.gen_state)
                horizon = server_now() + SCHEDULE_HORIZON_MS
                while not self.instances or self.instances[-1].start < horizon:
                    try:
                        self.instances.append(next(self.gen))
                    except StopIteration:
                        break
                self._set_window_requirements()
            for dev in self.target_devices():
                await hub.send_player(dev, {"type": "media_sync", "assets": self.required_by_device.get(dev, self.required)})
            await self.broadcast()
            await self._maybe_begin()

    async def force_start(self) -> None:
        async with self._lock:
            if self.state == "syncing":
                await self._begin()

    async def _maybe_begin(self) -> None:
        online = [d for d in self.target_devices() if hub.online(d)]
        if not online:
            self.message = "Waiting for players to connect"
            await self.broadcast()
            return
        if all(self.ready.get(d) for d in online):
            await self._begin()

    async def _begin(self, t0: Optional[int] = None) -> None:
        anchor = t0 if t0 is not None else server_now() + START_LEAD_MS
        self.gen_state = _State()
        self.gen = iter_program(self.program, self.layout, anchor, self.pending_path, state=self.gen_state)
        self.exhausted = False
        self.instances = []
        self._sent_until = {}
        self.started_at = anchor
        self.state = "playing"
        self.message = ""
        await self._fill()
        await self.broadcast()

    async def stop(self) -> None:
        async with self._lock:
            self.remember(False)
            await self._clear_players(0)
            self.state = "idle"
            self.program = None
            self.instances = []
            self.gen = None
            self.by_schedule = None
            self.message = ""
            self.runtime_seed = None
            await self.broadcast()

    async def pause(self) -> None:
        async with self._lock:
            if self.state != "playing":
                return
            cur = self._current()
            self.pending_path = cur.item_path if cur else None
            await self._clear_players(server_now() + 300)
            self.instances = []
            self.gen = None
            self.state = "paused"
            self.remember(False)
            await self.broadcast()

    async def resume(self) -> None:
        async with self._lock:
            if self.state == "paused" and self.program:
                self.remember(True)
                await self._begin()

    async def jump(self, item_path: list[int]) -> None:
        async with self._lock:
            if self.state not in ("playing", "paused") or not self.program:
                return
            t = server_now() + SWITCH_LEAD_MS
            await self._clear_players(t)
            self.pending_path = item_path
            await self._begin(t)

    async def skip(self) -> None:
        nxt = self._next()
        if nxt:
            await self.jump(nxt.item_path)

    async def reload(self) -> None:
        """Re-read program + layout and continue from the current cue."""
        if self.state not in ("playing", "paused") or not self.program:
            return
        cur = self._current()
        async with self._lock:
            self._load(self.program["id"])
            if self.runtime_seed is not None:
                self.program["seed"] = self.runtime_seed
            for dev in self.target_devices():
                await hub.send_player(dev, {"type": "media_sync", "assets": self.required_by_device.get(dev, self.required)})
        if self.state == "playing" and cur:
            await self.jump(cur.item_path)

    async def oneshot(self, cue: dict, immediate: bool) -> None:
        """Insert a cue now (or at the next cue boundary), then continue the program."""
        async with self._lock:
            if self.state != "playing" or not self.layout:
                raise ValueError("start a program first")
            nxt = self._next()
            t = server_now() + SWITCH_LEAD_MS
            if not immediate and nxt:
                t = max(t, nxt.start)
            import random

            inst = expand_cue(cue, t, self.layout, random.Random(server_now()), self.gen_state)
            if inst is None:
                raise ValueError("cue has no playable clips")
            extra = {a for a in (s.asset_id for s in inst.slots)} - {r["id"] for r in self.required}
            if extra:
                raise ValueError("one-shot uses clips not synced to players; add them to the program first")
            resume_path = nxt.item_path if nxt else None
            await self._clear_players(t)
            self.instances = [i for i in self.instances if i.start < t]
            self._sent_until = {}
            self.pending_path = resume_path
            self.gen = iter_program(self.program, self.layout, inst.next_start, resume_path, state=self.gen_state)
            self.exhausted = False
            self.instances.append(inst)
            await self._send_instances([inst])
            await self._fill()
            await self.broadcast()

    async def preview(self, cue: dict, device_ids: list[str]) -> dict:
        """Cache and play one cue on selected players without replacing the show.

        Target players are cleared at the preview boundary. If a show is
        running, its still-relevant events are re-queued around the preview so
        each player returns to the show afterward; idle players return to black.
        """
        async with self._preview_lock:
            if self.state == "syncing":
                raise ValueError("wait for the current show to finish syncing")
            if not device_ids:
                raise ValueError("choose at least one projector")
            if any(not hub.online(d) for d in device_ids):
                raise ValueError("every preview projector must be online")

            from ..loader import asset_url, load_layout
            with session_scope() as s:
                layout = load_layout(s)
            surfaces = []
            for device_id in dict.fromkeys(device_ids):
                surface_id = next((sid for sid, devs in layout.devices_by_surface.items() if device_id in devs), None)
                if not surface_id:
                    raise ValueError(f"{device_id} is not assigned to a projection surface")
                if surface_id not in surfaces:
                    surfaces.append(surface_id)

            targeted = copy.deepcopy(cue)
            targeted.setdefault("id", f"preview-{server_now()}")
            targeted.setdefault("name", "Projector preview")
            if targeted.get("type") == "encounter":
                original = []
                for track in targeted.get("tracks", []):
                    sid = track.get("surface_id")
                    if sid not in original:
                        original.append(sid)
                    track["surface_id"] = surfaces[original.index(sid) % len(surfaces)]
            else:
                targeted["target"] = {"mode": "list", "surfaces": surfaces}

            inst = expand_cue(targeted, 0, layout, random.Random(server_now()), _State())
            if inst is None or not inst.slots:
                raise ValueError("preview has no playable clips")
            requested = set(device_ids)
            needed_ids = {slot.asset_id for slot in inst.slots}
            with session_scope() as s:
                assets = s.exec(select(MediaAsset).where(MediaAsset.id.in_(needed_ids))).all()
                if len(assets) != len(needed_ids):
                    raise ValueError("preview media is missing or not ready")
                extra = [{"id": a.id, "sha256": a.sha256, "url": asset_url(a), "size": a.size} for a in assets]
            waiters = {d: asyncio.Event() for d in requested}
            self._preview_waiters.update(waiters)
            try:
                for device_id in requested:
                    sync = {a["id"]: a for a in (self.required_by_device.get(device_id, self.required) if self.state == "playing" else [])}
                    sync.update({a["id"]: a for a in extra})
                    await hub.send_player(device_id, {"type": "media_sync", "assets": list(sync.values())})
                try:
                    await asyncio.wait_for(asyncio.gather(*(e.wait() for e in waiters.values())), timeout=120)
                except TimeoutError as exc:
                    raise ValueError("projector did not finish caching the preview") from exc
            finally:
                for device_id in requested:
                    self._preview_waiters.pop(device_id, None)

            anchor = server_now() + SWITCH_LEAD_MS
            end = anchor + inst.next_start
            await hub.send_players({"type": "clear", "after": anchor}, list(requested))

            # Re-queue the part of a running show that belongs before/after the
            # preview. Program events that would start during the preview are
            # intentionally skipped on these targets.
            if self.state == "playing" and self.layout:
                per_device: dict[str, list[dict]] = {}
                for existing in self.instances:
                    for slot in existing.slots:
                        for event in slot_to_events(slot, 0, self.layout):
                            if event["device_id"] not in requested or event["end"] <= end:
                                continue
                            if event["t"] < anchor or event["t"] >= end:
                                per_device.setdefault(event["device_id"], []).append(event)
                for device_id, events in per_device.items():
                    events.sort(key=lambda event: event["t"])
                    await hub.send_player(device_id, {"type": "schedule", "events": events})

            stamp = server_now()
            per_device: dict[str, list[dict]] = {}
            for slot in inst.slots:
                for event in slot_to_events(slot, anchor, layout):
                    if event["device_id"] in requested:
                        event["id"] = f"preview:{stamp}:{event['id']}"
                        per_device.setdefault(event["device_id"], []).append(event)
            for device_id, events in per_device.items():
                events.sort(key=lambda event: event["t"])
                await hub.send_player(device_id, {"type": "schedule", "events": events})
            return {"ok": True, "device_ids": sorted(requested), "start": anchor, "end": end, "assets": sorted(needed_ids)}

    # ------------------------------------------------------------ internals

    def _current(self) -> Optional[CueInstance]:
        now = server_now()
        for inst in self.instances:
            if inst.start <= now < inst.next_start:
                return inst
        return self.instances[0] if self.instances else None

    def _next(self) -> Optional[CueInstance]:
        now = server_now()
        return next((i for i in self.instances if i.start > now), None)

    async def _clear_players(self, after: int) -> None:
        await hub.send_players({"type": "clear", "after": after})

    async def _send_instances(self, insts: list[CueInstance], only: Optional[str] = None) -> None:
        per_dev: dict[str, list[dict]] = {}
        for inst in insts:
            for slot in inst.slots:
                for ev in slot_to_events(slot, 0, self.layout):
                    if only and ev["device_id"] != only:
                        continue
                    per_dev.setdefault(ev["device_id"], []).append(ev)
        for dev, events in per_dev.items():
            events.sort(key=lambda e: e["t"])
            await hub.send_player(dev, {"type": "schedule", "events": events})

    def _set_window_requirements(self) -> None:
        if not self.rolling_cache or not self.layout:
            return
        now = server_now()
        cache_until = now + CACHE_HORIZON_MS
        ids_by_device: dict[str, set[str]] = {dev: set() for dev in self.target_devices()}
        for inst in self.instances:
            if inst.end <= now - 5_000:
                continue
            for slot in inst.slots:
                if slot.start > cache_until:
                    continue
                for event in slot_to_events(slot, 0, self.layout):
                    ids_by_device.setdefault(event["device_id"], set()).add(slot.asset_id)
        self.required_by_device = {
            dev: [self.asset_records[aid] for aid in sorted(ids) if aid in self.asset_records]
            for dev, ids in ids_by_device.items()
        }

    async def _sync_window(self) -> None:
        if not self.rolling_cache:
            return
        self._set_window_requirements()
        for dev in self.target_devices():
            assets = self.required_by_device.get(dev, [])
            signature = tuple(a["sha256"] for a in assets)
            if signature != self._cache_signature.get(dev):
                self._cache_signature[dev] = signature
                await hub.send_player(dev, {"type": "media_sync", "assets": assets})

    async def _fill(self) -> None:
        if self.state != "playing" or self.gen is None or self.exhausted:
            return
        horizon = server_now() + SCHEDULE_HORIZON_MS
        new: list[CueInstance] = []
        last_start = self.instances[-1].start if self.instances else 0
        while last_start < horizon:
            try:
                inst = next(self.gen)
            except StopIteration:
                self.exhausted = True
                break
            new.append(inst)
            self.instances.append(inst)
            last_start = inst.start
        if new:
            await self._sync_window()
            await self._send_instances(new)
        cutoff = server_now() - 60_000
        self.instances = [i for i in self.instances if i.next_start > cutoff]

    async def on_player_connected(self, device_id: str) -> None:
        if self.state in ("syncing", "playing") and device_id in self.target_devices():
            await hub.send_player(device_id, {"type": "media_sync", "assets": self.required_by_device.get(device_id, self.required)})
            if self.state == "playing":
                now = server_now()
                await self._send_instances([i for i in self.instances if i.end > now], only=device_id)

    async def on_media_status(self, device_id: str, msg: dict) -> None:
        self.ready[device_id] = bool(msg.get("ready"))
        self.progress[device_id] = float(msg.get("progress") or 0)
        if msg.get("ready") and device_id in self._preview_waiters:
            self._preview_waiters[device_id].set()
        if msg.get("error"):
            self.message = f"{device_id}: {msg['error']}"
        if self.state == "syncing":
            async with self._lock:
                if self.state == "syncing":
                    await self._maybe_begin()
            await self.broadcast()

    # ------------------------------------------------------------ schedules

    def _active_schedule(self) -> Optional[Schedule]:
        now = dt.datetime.now()
        minutes = now.hour * 60 + now.minute
        with session_scope() as s:
            for sch in s.exec(select(Schedule).where(Schedule.enabled == True)).all():  # noqa: E712
                try:
                    sh, sm = map(int, sch.start.split(":"))
                    eh, em = map(int, sch.end.split(":"))
                except ValueError:
                    continue
                start, end = sh * 60 + sm, eh * 60 + em
                day = now.weekday()
                if start <= end:
                    active = day in sch.days and start <= minutes < end
                else:  # overnight window, e.g. 18:00-02:00
                    prev_day = (day - 1) % 7
                    active = (day in sch.days and minutes >= start) or (prev_day in sch.days and minutes < end)
                if active:
                    return sch
        return None

    def _idle_program_id(self) -> Optional[str]:
        with session_scope() as s:
            st = s.get(Setting, "idle_program_id")
            return st.value if st and st.value else None

    async def _check_schedules(self) -> None:
        sch = self._active_schedule()
        if sch:
            if self.state == "idle" or (self.by_schedule and self.by_schedule != sch.id):
                log.info("schedule %s starting program %s", sch.id, sch.program_id)
                await self.start(sch.program_id, by_schedule=sch.id)
            return
        if self.by_schedule and self.by_schedule != "idle":
            idle = self._idle_program_id()
            if idle:
                await self.start(idle, by_schedule="idle")
            else:
                await self.stop()
        elif self.state == "idle" and self.by_schedule is None:
            idle = self._idle_program_id()
            with session_scope() as s:
                has_schedules = bool(s.exec(select(Schedule).where(Schedule.enabled == True)).first())  # noqa: E712
            if idle and has_schedules:
                await self.start(idle, by_schedule="idle")

    async def run(self) -> None:
        await self.restore()
        tick = 0
        while True:
            try:
                await asyncio.sleep(1)
                tick += 1
                if self.state == "playing":
                    async with self._lock:
                        await self._fill()
                        await self._sync_window()
                        if self.exhausted and self.instances and server_now() > max(i.next_start for i in self.instances):
                            self.remember(False)
                            self.state = "idle"
                            self.program = None
                            self.by_schedule = None
                    await self.broadcast()
                elif self.state == "syncing" and tick % 5 == 0:
                    for dev in self.target_devices():
                        if hub.online(dev) and not self.ready.get(dev):
                            await hub.send_player(dev, {"type": "media_sync", "assets": self.required_by_device.get(dev, self.required)})
                if tick % 15 == 0:
                    await self._check_schedules()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("runner tick failed")


runner = Runner()
