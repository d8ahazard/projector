"""WebSocket endpoints.

Player protocol (JSON text frames):

  player -> server
    hello        {device_id, name, model, app_version, capabilities: {dual_decoder, max_decoders, width, height, storage_free_mb}}
    ping         {t0}                                  clock sync probe (player monotonic ms)
    heartbeat    {offset_ms, rtt_ms, drift_ms, event_id, asset_id}
    media_status {ready, have: [sha], missing, progress, storage_free_mb, error}

  server -> player
    welcome      {config, server_time}
    pong         {t0, ts}                              ts = server wall clock ms
    config       {config}                              live tuning parameters
    media_sync   {assets: [{id, sha256, url, size}]}   everything the current program needs
    schedule     {events: [...]}                       append events (absolute server ms)
    clear        {after}                               drop events starting at/after `after`
    guide        {pattern, params}                     projection guide, pattern=null hides
"""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import select

from ..db import session_scope
from ..models import Device, Surface, now_ms
from .hub import device_config, hub
from .runner import runner

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/player")
async def player_ws(ws: WebSocket) -> None:
    await ws.accept()
    device_id = None
    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=15))
        if hello.get("type") != "hello" or not hello.get("device_id"):
            await ws.close(code=4000)
            return
        device_id = str(hello["device_id"])
        caps = hello.get("capabilities") or {}
        caps["model"] = hello.get("model", "")
        caps["app_version"] = hello.get("app_version", "")
        with session_scope() as s:
            d = s.get(Device, device_id)
            if d is None:
                count = len(s.exec(select(Device)).all())
                d = Device(id=device_id, name=hello.get("name") or f"Player {count + 1}")
                # Auto-assign the first free surface so a fresh setup "just works".
                used = {x.surface_id for x in s.exec(select(Device)).all() if x.surface_id}
                free = [x for x in s.exec(select(Surface).order_by(Surface.sort)).all() if x.id not in used]
                if free:
                    d.surface_id = free[0].id
            d.role = "player"
            d.capabilities = caps
            d.last_seen = now_ms()
            s.add(d)
            s.commit()
            s.refresh(d)
            surface = s.get(Surface, d.surface_id) if d.surface_id else None
            cfg = device_config(d, surface)

        old = hub.players.get(device_id)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass
        hub.players[device_id] = ws
        hub.status.setdefault(device_id, {})
        await hub._send(ws, {"type": "welcome", "config": cfg, "server_time": int(time.time() * 1000)})
        hub.mark_devices_dirty()
        await runner.on_player_connected(device_id)
        log.info("player %s connected", device_id)

        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("type")
            if t == "ping":
                await hub._send(ws, {"type": "pong", "t0": msg.get("t0"), "ts": time.time() * 1000.0})
            elif t == "heartbeat":
                st = hub.status.setdefault(device_id, {})
                for k in ("offset_ms", "rtt_ms", "drift_ms", "event_id", "asset_id", "storage_free_mb", "management"):
                    if k in msg:
                        st[k] = msg[k]
                hub.mark_devices_dirty()
            elif t == "command_result":
                st = hub.status.setdefault(device_id, {})
                previous = st.get("command", {})
                if msg.get("id") == previous.get("id"):
                    st["command"] = {**previous, "state": str(msg.get("state", "unknown"))[:80],
                                     "message": str(msg.get("message", ""))[:500], "at": now_ms()}
                    with session_scope() as s:
                        d = s.get(Device, device_id)
                        if d:
                            d.status = {**d.status, "command": st["command"]}; s.add(d); s.commit()
                    hub.mark_devices_dirty()
            elif t == "media_status":
                st = hub.status.setdefault(device_id, {})
                st["cache_ready"] = bool(msg.get("ready"))
                st["cache_progress"] = msg.get("progress", 0)
                st["cache_missing"] = msg.get("missing", 0)
                if "storage_free_mb" in msg:
                    st["storage_free_mb"] = msg["storage_free_mb"]
                await runner.on_media_status(device_id, msg)
                hub.mark_devices_dirty()
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError):
        pass
    except Exception:
        log.exception("player socket error")
    finally:
        if device_id and hub.players.get(device_id) is ws:
            del hub.players[device_id]
            with session_scope() as s:
                d = s.get(Device, device_id)
                if d:
                    d.last_seen = now_ms()
                    d.status = hub.status.get(device_id, {})
                    s.add(d)
                    s.commit()
            hub.mark_devices_dirty()
            log.info("player %s disconnected", device_id)


@router.websocket("/ws/ui")
async def ui_ws(ws: WebSocket) -> None:
    await ws.accept()
    hub.uis.add(ws)
    try:
        await hub._send(ws, {"type": "devices", "devices": hub.device_list()})
        await hub._send(ws, {"type": "runner", "state": runner.public_state()})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.uis.discard(ws)
