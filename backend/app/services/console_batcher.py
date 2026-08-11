from __future__ import annotations

import threading
from collections.abc import Callable


class ConsoleBatcher:
    """Coalesces console lines into ``console_line_batch`` events.

    Lifted verbatim from the batching half of DUT_browser's ``SysMonParser``
    (``_queue_console_line`` / ``_flush_console_lines`` / the flush timer,
    ``app/parser/sysmon_parser.py``). In DUT_browser that machinery is entangled
    with snapshot parsing; M1 has no parser, so it lives on its own here. The
    emitted event name and shape are unchanged — ``{"type":
    "console_line_batch", "lines": [...]}`` — so the DUT_browser event contract
    still holds.

    A batch is emitted when either ``BATCH_SIZE`` lines have accumulated or
    ``BATCH_MAX_LATENCY_SEC`` has elapsed since the first pending line,
    whichever comes first. One send per line never happens.

    Thread-safety: ``feed`` runs on the SerialWorker thread and the flush timer
    runs on its own thread; both are serialised on ``_state_lock``. ``on_event``
    is always called outside the lock.
    """

    BATCH_SIZE = 20
    BATCH_MAX_LATENCY_SEC = 0.05

    def __init__(self, on_event: Callable[[dict], None]) -> None:
        self.on_event = on_event
        self._pending: list[str] = []
        self._flush_timer: threading.Timer | None = None
        self._state_lock = threading.Lock()
        self._line_count = 0
        self._batch_count = 0

    def feed(self, text: str) -> None:
        """Queue one console line (no trailing newline) for the next batch."""
        should_flush_now = False
        with self._state_lock:
            self._pending.append(text)
            if len(self._pending) >= self.BATCH_SIZE:
                should_flush_now = True
                self._cancel_timer_locked()
            else:
                self._ensure_timer_locked()
        if should_flush_now:
            self.flush()

    def flush(self) -> None:
        """Emit whatever is pending right now (no-op when nothing is queued)."""
        with self._state_lock:
            if not self._pending:
                self._cancel_timer_locked()
                return
            lines = self._pending
            self._pending = []
            self._cancel_timer_locked()
        self.on_event({"type": "console_line_batch", "lines": lines})
        self._line_count += len(lines)
        self._batch_count += 1

    def reset(self) -> None:
        with self._state_lock:
            self._cancel_timer_locked()
            self._pending = []
            self._line_count = 0
            self._batch_count = 0

    def efficiency_report(self) -> dict:
        """Batching stats — the reviewer's evidence that WS sends are coalesced."""
        average_batch_size = self._line_count / self._batch_count if self._batch_count > 0 else 0.0
        return {
            "console_line_count": self._line_count,
            "console_batch_count": self._batch_count,
            "average_batch_size": round(average_batch_size, 3),
        }

    def _ensure_timer_locked(self) -> None:
        if self._flush_timer is not None:
            return
        timer = threading.Timer(self.BATCH_MAX_LATENCY_SEC, self.flush)
        timer.daemon = True
        self._flush_timer = timer
        timer.start()

    def _cancel_timer_locked(self) -> None:
        timer = self._flush_timer
        self._flush_timer = None
        if timer is not None:
            timer.cancel()
