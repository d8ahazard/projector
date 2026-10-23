"""Minimal ComfyUI HTTP/WebSocket client."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx
import websockets


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        if not self.base.startswith("http"):
            self.base = "http://" + self.base
        self.client_id = uuid.uuid4().hex

    def _http(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base, timeout=timeout)

    async def stats(self) -> dict:
        async with self._http(5) as c:
            r = await c.get("/system_stats")
            r.raise_for_status()
            return r.json()

    async def upload_image(self, data: bytes, filename: str) -> str:
        async with self._http(60) as c:
            r = await c.post("/upload/image", files={"image": (filename, data)}, data={"overwrite": "true"})
            r.raise_for_status()
            j = r.json()
            return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]

    async def queue(self, prompt: dict) -> str:
        async with self._http() as c:
            r = await c.post("/prompt", json={"prompt": prompt, "client_id": self.client_id})
            if r.status_code >= 400:
                try:
                    j = r.json()
                    err = j.get("error", {}).get("message", "") or r.text
                    nodes = j.get("node_errors") or {}
                    details = "; ".join(f"node {k}: {e.get('message', '')} {e.get('details', '')}".strip() for k, v in nodes.items() for e in v.get("errors", []))
                    raise ComfyError(f"{err} {details}".strip())
                except ValueError:
                    raise ComfyError(r.text)
            return r.json()["prompt_id"]

    async def history(self, prompt_id: str) -> Optional[dict]:
        async with self._http() as c:
            r = await c.get(f"/history/{prompt_id}")
            r.raise_for_status()
            return r.json().get(prompt_id)

    async def cancel(self, prompt_id: str) -> None:
        async with self._http() as c:
            await c.post("/queue", json={"delete": [prompt_id]})
            q = (await c.get("/queue")).json()
            running = [item[1] for item in q.get("queue_running", [])]
            if prompt_id in running:
                await c.post("/interrupt")

    async def download(self, file: dict, dst: Path) -> None:
        params = {"filename": file["filename"], "subfolder": file.get("subfolder", ""), "type": file.get("type", "output")}
        async with self._http(300) as c:
            async with c.stream("GET", "/view", params=params) as r:
                r.raise_for_status()
                with open(dst, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        f.write(chunk)

    async def events(self) -> AsyncIterator[dict]:
        """Yields ComfyUI websocket JSON messages for this client id."""
        ws_url = self.base.replace("http", "ws", 1) + f"/ws?clientId={self.client_id}"
        async with websockets.connect(ws_url, max_size=None, open_timeout=10) as ws:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue  # preview images
                try:
                    yield json.loads(raw)
                except ValueError:
                    continue


OUTPUT_KEYS = ("videos", "gifs", "images", "audio")


def output_files(history: dict) -> list[dict]:
    """All saved output files from a history entry (SaveVideo reports under 'images')."""
    files = []
    for node_out in (history.get("outputs") or {}).values():
        for key in OUTPUT_KEYS:
            for f in node_out.get(key, []) or []:
                if isinstance(f, dict) and f.get("filename") and f.get("type", "output") == "output":
                    files.append(f)
    return files
