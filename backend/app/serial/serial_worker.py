"""Background serial reader with always-on raw logging.

Ported from DUT_browser's ``app/serial/serial_worker.py``. Cut: the sysmon
parser hand-off, interactive terminal mode, ``capture_command`` and writes to
the DUT (M2/M3/M4 territory — M1 is read-only, the console page has no send
box). Kept: the thread/lock/stop-event lifecycle, the session-log handling and
the periodic ``fsync`` policy.

Two deliberate changes from the DUT_browser original, both in service of the M1
acceptance test (SPEC §5: a 30-minute soak whose raw log must ``diff`` clean
against the source):

1. The log is opened in **binary, unbuffered** mode and every byte read is
   written **verbatim**. DUT_browser's ``_write_log_line`` appends a ``"\\n"``
   when a chunk does not end in one, which mutates the byte stream and would
   make a byte-exact diff impossible.
2. The reader uses chunked ``read(in_waiting or 1)`` rather than ``readline()``,
   so a partial line sitting in the driver buffer is logged immediately instead
   of waiting for its terminator. Line assembly for the console stream happens
   *after* the raw write.

The P0 ordering guarantee — raw log before anything else — is enforced in
:meth:`_read_loop`: the write happens first, and every downstream consumer is
called inside a ``try/except`` so no batcher, ring-buffer or WebSocket failure
can ever interrupt logging.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import serial

from app.config import DEFAULT_BAUDRATE, LOG_DIR


class SerialWorker:
    _FSYNC_INTERVAL_SEC = 180
    # Cap on a single read so one burst cannot balloon the buffer; the loop just
    # comes straight back round for the rest.
    _MAX_READ_BYTES = 65536

    def __init__(self, on_line: Callable[[str], None] | None = None) -> None:
        # ``on_line`` receives each assembled console line (newline stripped).
        # It is called *after* the raw log write and is never allowed to raise
        # into the reader.
        self._on_line = on_line
        self._serial: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._log_fp = None
        self._log_path: Path | None = None
        self._last_fsync_monotonic: float = 0.0
        self._pending: bytes = b""
        self._port: str | None = None
        self._baudrate: int = DEFAULT_BAUDRATE
        self._opened_at: str | None = None
        self._bytes_written: int = 0
        self._last_rx_monotonic: float = 0.0
        self._last_error: str | None = None

    # ------------------------------------------------------------------ state

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def current_log_path(self) -> str | None:
        return str(self._log_path) if self._log_path is not None else None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def status(self) -> dict:
        """Everything the UI's connection card and status pill need."""
        with self._lock:
            last_rx = self._last_rx_monotonic
            return {
                "connected": self._serial is not None and self._serial.is_open,
                "port": self._port,
                "baudrate": self._baudrate,
                "opened_at": self._opened_at,
                "log_path": str(self._log_path) if self._log_path is not None else None,
                "log_name": self._log_path.name if self._log_path is not None else None,
                "bytes_written": self._bytes_written,
                "last_rx_age_s": None if last_rx == 0.0 else round(time.monotonic() - last_rx, 1),
                "last_error": self._last_error,
            }

    # -------------------------------------------------------------- lifecycle

    def open(self, port: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Open ``port`` and start the reader thread on a fresh session log."""
        self.close()

        with self._lock:
            self._stop_event.clear()
            self._pending = b""
            self._last_error = None
            self._serial = serial.Serial(port=port, baudrate=baudrate, timeout=1)
            self._port = port
            self._baudrate = baudrate
            self._opened_at = datetime.now().isoformat(timespec="seconds")
            self._bytes_written = 0
            self._last_rx_monotonic = 0.0
            try:
                self._start_log_session_locked()
            except Exception:
                # A serial session without its P0 raw log must never remain
                # active. Opening the tty happens first because the log name is
                # session-scoped, so unwind the fd if log creation/fsync fails.
                try:
                    self._serial.close()
                finally:
                    self._serial = None
                    self._opened_at = None
                raise
            self._thread = threading.Thread(target=self._read_loop, name="serial-reader", daemon=True)
            self._thread.start()

    def close(self) -> None:
        """Stop the reader, close the port, and close the session log."""
        old_thread: threading.Thread | None = None
        with self._lock:
            self._stop_event.set()
            if self._serial is not None:
                try:
                    if self._serial.is_open:
                        self._serial.close()
                finally:
                    self._serial = None
            old_thread = self._thread
            self._thread = None

        if (
            old_thread is not None
            and old_thread.is_alive()
            and old_thread is not threading.current_thread()
        ):
            old_thread.join(timeout=2.0)

        self._flush_pending_line()
        self._close_log_session()

    # ------------------------------------------------------------ reader loop

    def _read_loop(self) -> None:
        while not self._stop_event.is_set():
            ser = self._serial
            if ser is None or not ser.is_open:
                break

            try:
                waiting = ser.in_waiting
            except Exception:
                waiting = 0
            try:
                # A blocking read of 1 byte when nothing is waiting: this is what
                # paces the loop (timeout=1s), so there is no busy-wait.
                data = ser.read(min(waiting, self._MAX_READ_BYTES) or 1)
            except Exception as exc:  # port yanked, USB re-enumerated, ...
                # close() closes the fd out from under this blocking read, so an
                # exception during an intentional stop is expected, not a fault.
                # Only a failure while we are still meant to be running is worth
                # showing the engineer.
                if not self._stop_event.is_set():
                    self._last_error = f"serial read failed: {exc}"
                break
            if not data:
                continue

            # ---- P0: the raw log write happens first, and nothing below it is
            # ---- allowed to raise back into this loop.
            try:
                self._write_log_raw(data)
            except OSError as exc:
                # Nowhere left to put the bytes (disk full, log unlinked). Stop
                # rather than pretend to capture, and say so loudly — a console
                # that just goes quiet is the worst possible failure here.
                self._last_error = f"raw log write failed: {exc}"
                break

            try:
                self._dispatch_lines(data)
            except Exception:
                # A batcher / ring / WebSocket failure must never stop logging.
                continue

    def _dispatch_lines(self, data: bytes) -> None:
        """Assemble complete lines out of the byte stream and hand them upward."""
        if self._on_line is None:
            return
        buffer = self._pending + data
        parts = buffer.split(b"\n")
        self._pending = parts.pop()
        for raw in parts:
            self._on_line(raw.rstrip(b"\r").decode("utf-8", errors="ignore"))

    def _flush_pending_line(self) -> None:
        """Emit a trailing unterminated line when the session ends."""
        pending = self._pending
        self._pending = b""
        if not pending or self._on_line is None:
            return
        try:
            self._on_line(pending.rstrip(b"\r").decode("utf-8", errors="ignore"))
        except Exception:
            return

    # ------------------------------------------------------------ raw logging

    def _start_log_session_locked(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # Binary + unbuffered: one write(2) per chunk read, immediately visible
        # to a concurrent log download. No header line is written — the log holds
        # DUT bytes and nothing else, so it diffs byte-for-byte against a replay
        # fixture (session metadata lives in GET /api/serial/status instead).
        #
        # Exclusive create, with a counter suffix on collision: reconnecting
        # inside the same second must not append a second session onto the first
        # one's file, which would silently interleave two DUT captures.
        for suffix in ("", *(f"-{n}" for n in range(2, 100))):
            candidate = LOG_DIR / f"dut-{timestamp}{suffix}.log"
            try:
                self._log_fp = open(candidate, "xb", buffering=0)
            except FileExistsError:
                continue
            self._log_path = candidate
            break
        else:
            raise RuntimeError(f"could not create a session log in {LOG_DIR}")
        self._last_fsync_monotonic = time.monotonic()
        os.fsync(self._log_fp.fileno())

    def _write_log_raw(self, data: bytes) -> None:
        with self._lock:
            if self._log_fp is None:
                return
            self._log_fp.write(data)
            self._bytes_written += len(data)
            self._last_rx_monotonic = time.monotonic()
        self._maybe_force_sync()

    def _maybe_force_sync(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._log_fp is None:
                return
            if now - self._last_fsync_monotonic < self._FSYNC_INTERVAL_SEC:
                return
            os.fsync(self._log_fp.fileno())
            self._last_fsync_monotonic = now

    def _close_log_session(self) -> None:
        with self._lock:
            if self._log_fp is not None:
                try:
                    os.fsync(self._log_fp.fileno())
                finally:
                    self._log_fp.close()
                    self._log_fp = None
