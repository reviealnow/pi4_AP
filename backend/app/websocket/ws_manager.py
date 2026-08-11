"""Browser fan-out for the single ``/ws`` stream.

Ported from DUT_browser's ``app/websocket/ws_manager.py``. Added for M1: the
ring-buffer replay a newly connected client receives (SPEC §3.1), and a lock
that keeps that replay ordered ahead of live broadcasts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Serialises connect-time replay against live broadcasts so a new client
        # never sees a live batch land ahead of its backlog.
        self._send_lock = asyncio.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket, replay: Callable[[], list[str]] | None = None) -> None:
        """Accept a client and replay the console ring buffer to it.

        The replay is sent as an ordinary ``console_line_batch`` so the client
        needs no special-case handling and the DUT_browser event contract is
        unchanged.

        Known, accepted race: a batch that has already been appended to the ring
        but whose broadcast task has not yet run will reach this client twice
        (once in the replay, once live). The window is a single scheduling turn
        and it only costs a repeated batch on screen — the durable record is the
        raw log, so nothing is lost or corrupted. Closing it properly needs
        per-batch sequence numbers, i.e. a change to the DUT_browser event
        contract, which M1 does not get to make.
        """
        await ws.accept()
        async with self._send_lock:
            self._clients.add(ws)
            if replay is None:
                return
            lines = replay()
            if not lines:
                return
            try:
                await ws.send_json({"type": "console_line_batch", "lines": lines})
            except Exception:
                self.disconnect(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        async with self._send_lock:
            dead: list[WebSocket] = []
            for client in list(self._clients):
                try:
                    await client.send_json(event)
                except Exception:
                    dead.append(client)
            for ws in dead:
                self.disconnect(ws)

    def emit_from_thread(self, event: dict[str, Any]) -> None:
        """Schedule a broadcast from the SerialWorker thread onto the loop."""
        loop = self._loop
        if loop is None or not self._clients:
            # No listeners: skip the coroutine/task allocation entirely. The ring
            # buffer already holds the lines, so a client connecting later still
            # gets them.
            return
        coro = self.broadcast(event)
        try:
            loop.call_soon_threadsafe(asyncio.create_task, coro)
        except RuntimeError:
            # Loop shutting down; dropping a console batch is harmless. Close the
            # orphaned coroutine so it does not warn.
            coro.close()
