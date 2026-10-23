"""Connection registry for players (TV sticks) and UIs (browsers / phone)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import WebSocket
from sqlmodel import select

from ..db import session_scope
from ..models import Device, Surface

log = logging.getLogger(__name__)


def server_now() -> int:
    return int(time.time() * 1000)


def device_config(d: Device, surface: Optional[Surface]) -> dict:
    """Everything the player needs to render. Mask URL changes when the mask does."""
    mask_ver = abs(hash((json.dumps(d.mask), round(d.feather, 4)))) % 10_000_000
    return {
        "id": d.id,
        "name": d.name,
        "surface_id": d.surface_id,
        "surface": {"w": surface.width, "h": surface.height} if surface else None,
        "mirror_h": d.mirror_h,
        "mirror_v": d.mirror_v,
        "corners": d.corners,
        "mask_url": f"/api/devices/{d.id}/mask.png?v={mask_ver}",
        "brightness": d.brightness,
        "gamma": d.gamma,
        "gain": [d.gain_r, d.gain_g, d.gain_b],
        "volume": d.volume,
        "audio": d.audio,
        "cache_budget_mb": d.cache_budget_mb,
    }


class Hub:
    def __init__(self) -> None:
        self.players: dict[str, WebSocket] = {}
        self.uis: set[WebSocket] = set()
        self.status: dict[str, dict] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._devices_dirty = False
        self._devices_task: Optional[asyncio.Task] = None

    def online(self, device_id: str) -> bool:
        return device_id in self.players

    async def _send(self, ws: WebSocket, msg: dict) -> bool:
        lock = self._locks.setdefault(id(ws), asyncio.Lock())
        try:
            async with lock:
                await ws.send_text(json.dumps(msg, separators=(",", ":")))
            return True
        except Exception:
            return False

    async def send_player(self, device_id: str, msg: dict) -> bool:
        ws = self.players.get(device_id)
        if not ws:
            return False
        return await self._send(ws, msg)

    async def send_players(self, msg: dict, device_ids: Optional[list[str]] = None) -> None:
        targets = device_ids if device_ids is not None else list(self.players)
        await asyncio.gather(*(self.send_player(d, msg) for d in targets))

    async def broadcast_ui(self, msg: dict) -> None:
        dead = []
        for ws in list(self.uis):
            if not await self._send(ws, msg):
                dead.append(ws)
        for ws in dead:
            self.uis.discard(ws)

    async def changed(self, topic: str) -> None:
        await self.broadcast_ui({"type": "changed", "topic": topic})

    async def toast(self, message: str, error: bool = False) -> None:
        await self.broadcast_ui({"type": "toast", "message": message, "error": error})

    # -------------------------------------------------------------- devices

    def device_list(self) -> list[dict]:
        with session_scope() as s:
            devices = s.exec(select(Device)).all()
            out = []
            for d in devices:
                row = d.model_dump()
                row["status"] = {**(d.status or {}), **self.status.get(d.id, {})}
                row["online"] = self.online(d.id)
                out.append(row)
            return sorted(out, key=lambda r: (r["role"], r["name"]))

    def mark_devices_dirty(self) -> None:
        """Coalesce device-list broadcasts to at most ~2/s."""
        self._devices_dirty = True
        if self._devices_task is None or self._devices_task.done():
            self._devices_task = asyncio.create_task(self._flush_devices())

    async def _flush_devices(self) -> None:
        await asyncio.sleep(0.5)
        if self._devices_dirty:
            self._devices_dirty = False
            await self.broadcast_ui({"type": "devices", "devices": self.device_list()})

    async def push_config(self, device_id: str) -> None:
        with session_scope() as s:
            d = s.get(Device, device_id)
            if not d:
                return
            surface = s.get(Surface, d.surface_id) if d.surface_id else None
            cfg = device_config(d, surface)
        await self.send_player(device_id, {"type": "config", "config": cfg})

    async def push_all_configs(self) -> None:
        for dev in list(self.players):
            await self.push_config(dev)

    async def guide(self, device_id: str, pattern: Optional[str], params: Optional[dict[str, Any]] = None) -> bool:
        if pattern is None:
            return await self.send_player(device_id, {"type": "guide", "pattern": None})
        return await self.send_player(device_id, {"type": "guide", "pattern": pattern, "params": params or {}})


hub = Hub()
