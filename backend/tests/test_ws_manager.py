from __future__ import annotations

import asyncio

from app.websocket.ws_manager import WebSocketManager


class SlowWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, _event: dict) -> None:
        self.send_started.set()
        await self.release_send.wait()


def test_slow_client_uses_one_bounded_broadcaster_and_counts_drops():
    async def scenario() -> None:
        manager = WebSocketManager()
        manager.bind_loop(asyncio.get_running_loop())
        client = SlowWebSocket()
        await manager.connect(client)  # type: ignore[arg-type]

        manager.emit_from_thread({"type": "console_line_batch", "lines": ["first"]})
        await asyncio.wait_for(client.send_started.wait(), timeout=1)

        for index in range(1000):
            manager.emit_from_thread({"type": "console_line_batch", "lines": [str(index)]})
        await asyncio.sleep(0)

        assert manager._queue is not None
        assert manager._queue.qsize() <= manager._QUEUE_MAX
        assert len(manager._ingress) <= manager._QUEUE_MAX
        assert manager.dropped_event_count > 0
        assert manager._broadcast_task is not None
        assert not manager._broadcast_task.done()

        client.release_send.set()
        await asyncio.sleep(0)
        await manager.close()

    asyncio.run(scenario())
