import asyncio
import json
import logging
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSManager:
    def __init__(self):
        self._connections: list[WebSocket] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self._connections.remove(ws) if ws in self._connections else None

    async def broadcast(self, msg: dict):
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_sync(self, msg: dict):
        """Thread-safe broadcast from non-async context (pipeline thread)."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(msg), self._loop)

    def send_log(self, text: str):
        self.broadcast_sync({"type": "log", "data": text})

    def send_run_status(self, run_id: int, status: str, nav_eur=None, rag=None):
        self.broadcast_sync({
            "type": "run_status",
            "data": {"run_id": run_id, "status": status, "nav_eur": nav_eur, "rag": rag},
        })

    def send_prices(self, snapshots: list[dict]):
        self.broadcast_sync({"type": "price_update", "data": snapshots})


manager = WSManager()
