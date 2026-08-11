from __future__ import annotations

import threading
from collections import deque

from app.config import CONSOLE_RING_MAX


class ConsoleRing:
    """In-memory ring of the most recent console lines, replayed to new clients.

    Ported from DUT_browser's ``app/services/console_buffer.py`` (``ConsoleBuffer``)
    with the capacity raised from 500 to 5000 per SPEC §3.1, and ``recent()``
    returning the whole ring by default so a fresh WebSocket client can be
    seeded in one batch.

    Fed from the ``console_line_batch`` events the batcher emits, so the replay a
    client receives is byte-identical to what a client connected the whole time
    would have seen. Not persisted — the durable record is the raw session log.

    Thread-safety: ``observe`` runs on the SerialWorker thread; ``recent`` runs
    on the asyncio event loop thread. Guarded by one lock.
    """

    def __init__(self, maxlen: int = CONSOLE_RING_MAX) -> None:
        self._lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=maxlen)

    def observe(self, event: dict) -> None:
        try:
            if event.get("type") != "console_line_batch":
                return
            lines = event.get("lines")
            if not isinstance(lines, list):
                return
            incoming = [line for line in lines if isinstance(line, str)]
            if incoming:
                with self._lock:
                    self._lines.extend(incoming)
        except Exception:
            # Never let buffering break the stream (P0: nothing downstream of the
            # raw log may raise into the reader thread).
            return

    def recent(self, limit: int = 0) -> list[str]:
        """Return the ring contents; ``limit > 0`` trims to the newest N lines."""
        with self._lock:
            lines = list(self._lines)
        if limit > 0:
            lines = lines[-limit:]
        return lines

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._lines)
