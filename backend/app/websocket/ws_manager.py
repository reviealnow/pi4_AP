"""Browser fan-out for the single ``/ws`` stream.

Ported from DUT_browser's ``app/websocket/ws_manager.py``. Added for M1: the
ring-buffer replay a newly connected client receives (SPEC §3.1), and a lock
that keeps that replay ordered ahead of live broadcasts.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket


class WebSocketManager:
    _QUEUE_MAX = 64

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._broadcast_task: asyncio.Task | None = None
        # Coalesce cross-thread submissions into one scheduled loop callback.
        # Both this ingress deque and the asyncio queue are bounded, so a slow
        # browser can never create an unbounded task/callback backlog.
        self._ingress: deque[dict[str, Any]] = deque()
        self._ingress_lock = threading.Lock()
        self._drain_scheduled = False
        self._dropped_events = 0
        # Serialises connect-time replay against live broadcasts so a new client
        # never sees a live batch land ahead of its backlog.
        self._send_lock = asyncio.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue = asyncio.Queue(maxsize=self._QUEUE_MAX)
        self._broadcast_task = loop.create_task(self._broadcast_loop())

    async def close(self) -> None:
        task = self._broadcast_task
        self._broadcast_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def dropped_event_count(self) -> int:
        with self._ingress_lock:
            return self._dropped_events

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

    async def _broadcast_loop(self) -> None:
        queue = self._queue
        if queue is None:
            return
        while True:
            event = await queue.get()
            try:
                await self.broadcast(event)
            finally:
                queue.task_done()

    def emit_from_thread(self, event: dict[str, Any]) -> None:
        """Schedule a broadcast from the SerialWorker thread onto the loop."""
        loop = self._loop
        if loop is None or not self._clients:
            # No listeners: skip the coroutine/task allocation entirely. The ring
            # buffer already holds the lines, so a client connecting later still
            # gets them.
            return
        should_schedule = False
        with self._ingress_lock:
            if len(self._ingress) >= self._QUEUE_MAX:
                self._ingress.popleft()
                self._dropped_events += 1
            self._ingress.append(event)
            if not self._drain_scheduled:
                self._drain_scheduled = True
                should_schedule = True
        if not should_schedule:
            return
        try:
            loop.call_soon_threadsafe(self._drain_ingress)
        except RuntimeError:
            # Loop shutting down; UI delivery is best-effort and the ring still
            # has the replay. Leave no submission marked as scheduled forever.
            with self._ingress_lock:
                self._dropped_events += len(self._ingress)
                self._ingress.clear()
                self._drain_scheduled = False

    def _drain_ingress(self) -> None:
        with self._ingress_lock:
            events = list(self._ingress)
            self._ingress.clear()
            self._drain_scheduled = False
        queue = self._queue
        if queue is None:
            with self._ingress_lock:
                self._dropped_events += len(events)
            return
        for event in events:
            if queue.full():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    pass
                with self._ingress_lock:
                    self._dropped_events += 1
            queue.put_nowait(event)
