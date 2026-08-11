#!/usr/bin/env python3
"""M1 acceptance harness: sustained-rate serial soak with a byte-exact verdict.

SPEC §5 M1: *30-minute soak at 115200 with zero lost lines vs a reference
capture.* With no DUT attached this stands in a pseudo-terminal for the wire:

    fixture ──write at 11520 B/s──> PTY master
                                    PTY slave ──> SerialWorker ──> raw log
                                                            └────> /ws client

The whole node is exercised — uvicorn, the REST control surface, the console
batcher, the ring buffer and a live WebSocket subscriber all run for the full
duration — so the verdict covers the P0 claim that *nothing downstream can cost
the raw log a byte*, not just the writer in isolation.

Verdict: the raw log is downloaded back over REST and compared to the source
fixture byte for byte (SHA-256 + line count). Any difference fails.

Normally invoked through ``scripts/soak_test.sh``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pty
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

# 115200 8N1 = 10 bits per byte on the wire = 11520 payload bytes/second.
BYTES_PER_SECOND_115200 = 11520
WRITE_TICK_SEC = 0.1


# --------------------------------------------------------------------- fixture


def build_fixture(path: Path, total_bytes: int) -> tuple[int, str]:
    """Write a numbered, DUT-shaped fixture of ~``total_bytes``.

    Every line carries its own index so a dropped line is identifiable by eye,
    not just by a hash mismatch.
    """
    digest = hashlib.sha256()
    written = 0
    lines = 0
    with path.open("wb") as handle:
        while written < total_bytes:
            line = (
                f"[{lines:08d}] sysmon: cpu0 12.5% usr 3.1% sys "
                f"94.2% idle  mem 475472 kB  seq={lines}\n"
            ).encode()
            handle.write(line)
            digest.update(line)
            written += len(line)
            lines += 1
    return lines, digest.hexdigest()


def file_digest(path: Path) -> tuple[int, int, str]:
    """Return (bytes, newline-terminated line count, sha256) for ``path``."""
    digest = hashlib.sha256()
    size = 0
    lines = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            lines += chunk.count(b"\n")
    return size, lines, digest.hexdigest()


# ------------------------------------------------------------------- PTY setup


class PtyPair:
    """A serial-port stand-in: ``write_fd`` feeds the device at ``slave_name``."""

    def __init__(self, write_fd: int, slave_name: str, cleanup) -> None:
        self.write_fd = write_fd
        self.slave_name = slave_name
        self._cleanup = cleanup

    def close(self) -> None:
        self._cleanup()


def open_pty_stdlib() -> PtyPair:
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)

    def cleanup() -> None:
        for fd in (slave_fd, master_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    return PtyPair(master_fd, slave_name, cleanup)


def open_pty_socat() -> PtyPair:
    """PTY pair via ``socat -d -d pty,raw,echo=0 pty,raw,echo=0``.

    socat prints the two device paths on stderr; we write to the first and point
    the node at the second. Used when socat is installed so the run matches the
    command in the SPEC/PR verbatim; otherwise :func:`open_pty_stdlib` gives an
    identical topology with no external dependency.
    """
    proc = subprocess.Popen(
        ["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"],
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
    )
    devices: list[str] = []
    deadline = time.monotonic() + 10
    assert proc.stderr is not None
    while len(devices) < 2 and time.monotonic() < deadline:
        line = proc.stderr.readline()
        if not line:
            break
        marker = "PTY is "
        if marker in line:
            devices.append(line.split(marker, 1)[1].strip())
    if len(devices) < 2:
        proc.terminate()
        raise RuntimeError("socat did not report two PTY devices")

    write_fd = os.open(devices[0], os.O_RDWR | os.O_NOCTTY)

    def cleanup() -> None:
        try:
            os.close(write_fd)
        except OSError:
            pass
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return PtyPair(write_fd, devices[1], cleanup)


# ------------------------------------------------------------------ node under test


def http_json(url: str, payload: dict | None = None, timeout: float = 10.0) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if http_json(f"{base_url}/health", timeout=2.0).get("ok"):
                return
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.25)
    raise RuntimeError(f"backend did not become healthy at {base_url}")


class WebSocketCounter(threading.Thread):
    """A live console subscriber, so the WS fan-out is loaded for the whole soak."""

    def __init__(self, ws_url: str) -> None:
        super().__init__(daemon=True)
        self.ws_url = ws_url
        self.lines = 0
        self.batches = 0
        self.error: str | None = None
        self._stop = threading.Event()

    def run(self) -> None:
        try:
            from websockets.sync.client import connect
        except Exception as exc:  # pragma: no cover - env without websockets
            self.error = f"websockets client unavailable: {exc}"
            return
        try:
            with connect(self.ws_url, open_timeout=10) as socket:
                while not self._stop.is_set():
                    try:
                        message = socket.recv(timeout=1.0)
                    except TimeoutError:
                        continue
                    event = json.loads(message)
                    if event.get("type") == "console_line_batch":
                        self.batches += 1
                        self.lines += len(event.get("lines", []))
        except Exception as exc:
            if not self._stop.is_set():
                self.error = f"{type(exc).__name__}: {exc}"

    def stop(self) -> None:
        self._stop.set()


# ------------------------------------------------------------------------ soak


def paced_write(fd: int, fixture: Path, bytes_per_second: int, progress_every: float = 60.0) -> int:
    """Stream ``fixture`` into ``fd`` at a sustained ``bytes_per_second``."""
    chunk_size = max(1, int(bytes_per_second * WRITE_TICK_SEC))
    started = time.monotonic()
    next_progress = started + progress_every
    sent = 0
    with fixture.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                offset += os.write(fd, chunk[offset:])
            sent += len(chunk)

            now = time.monotonic()
            target = started + sent / bytes_per_second
            if target > now:
                time.sleep(target - now)
            if now >= next_progress:
                elapsed = now - started
                print(
                    f"  [{elapsed / 60:5.1f} min] sent {sent / 1_048_576:6.2f} MiB "
                    f"({sent / elapsed:7.0f} B/s)",
                    flush=True,
                )
                next_progress = now + progress_every
    return sent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=1800, help="soak seconds (default 1800 = 30 min)")
    parser.add_argument("--baud", type=int, default=115200, help="baud rate to sustain (default 115200)")
    parser.add_argument("--port", type=int, default=8080, help="node HTTP port (default 8080)")
    parser.add_argument("--workdir", default="", help="where to keep the fixture/log (default: a temp dir)")
    parser.add_argument("--no-socat", action="store_true", help="always use the stdlib PTY pair")
    args = parser.parse_args()

    bytes_per_second = args.baud // 10
    total_bytes = bytes_per_second * args.duration

    workdir = Path(args.workdir) if args.workdir else Path(os.environ.get("TMPDIR", "/tmp")) / "pi4ap-soak"
    workdir.mkdir(parents=True, exist_ok=True)
    fixture = workdir / "fixture.log"
    captured = workdir / "captured.log"

    print("pi4_AP M1 soak — SPEC §5: zero lost lines at sustained baud")
    print(f"  duration     : {args.duration}s ({args.duration / 60:.1f} min)")
    print(f"  baud         : {args.baud} -> {bytes_per_second} B/s")
    print(f"  fixture size : {total_bytes / 1_048_576:.2f} MiB")
    print(f"  workdir      : {workdir}")

    print("Building fixture...", flush=True)
    fixture_lines, _ = build_fixture(fixture, total_bytes)
    fixture_bytes, fixture_newlines, fixture_sha = file_digest(fixture)
    print(f"  {fixture_lines} lines, {fixture_bytes} bytes, sha256={fixture_sha}", flush=True)

    use_socat = shutil.which("socat") is not None and not args.no_socat
    pair = open_pty_socat() if use_socat else open_pty_stdlib()
    print(f"  PTY source   : {'socat' if use_socat else 'python stdlib pty.openpty()'}")
    print(f"  device       : {pair.slave_name}", flush=True)

    base_url = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
        cwd=BACKEND_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    counter = WebSocketCounter(f"ws://127.0.0.1:{args.port}/ws")
    failures: list[str] = []
    try:
        wait_for_health(base_url)
        counter.start()
        time.sleep(0.5)

        opened = http_json(f"{base_url}/api/serial/open", {"port": pair.slave_name, "baudrate": args.baud})
        log_name = opened["log_name"]
        print(f"  raw log      : {log_name}", flush=True)

        print("Streaming...", flush=True)
        started = time.monotonic()
        sent = paced_write(pair.write_fd, fixture, bytes_per_second)
        elapsed = time.monotonic() - started
        print(f"  sent {sent} bytes in {elapsed:.1f}s ({sent / elapsed:.0f} B/s)", flush=True)

        # Let the reader drain whatever is still in the tty buffer.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if http_json(f"{base_url}/api/serial/status")["bytes_written"] >= sent:
                break
            time.sleep(0.5)

        status = http_json(f"{base_url}/api/serial/status")
        efficiency = http_json(f"{base_url}/api/console/efficiency")
        http_json(f"{base_url}/api/serial/close", {})
        counter.stop()

        with urllib.request.urlopen(f"{base_url}/api/serial/logs/{log_name}", timeout=120) as response:
            with captured.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    finally:
        counter.stop()
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        pair.close()

    captured_bytes, captured_newlines, captured_sha = file_digest(captured)

    print()
    print("=== RESULT ===")
    print(f"  source  : {fixture_bytes} bytes, {fixture_newlines} lines, sha256={fixture_sha}")
    print(f"  captured: {captured_bytes} bytes, {captured_newlines} lines, sha256={captured_sha}")
    print(f"  lost lines            : {fixture_newlines - captured_newlines}")
    print(f"  worker bytes_written  : {status['bytes_written']}")
    print(f"  ws batches / lines    : {counter.batches} / {counter.lines}")
    print(f"  avg batch size        : {efficiency['average_batch_size']}")
    print(f"  console batch count   : {efficiency['console_batch_count']}")
    if counter.error:
        print(f"  ws client error       : {counter.error}")

    if captured_sha != fixture_sha:
        failures.append("raw log does not match the source fixture byte for byte")
    if captured_newlines != fixture_newlines:
        failures.append(f"lost {fixture_newlines - captured_newlines} lines")
    if efficiency["console_batch_count"] >= efficiency["console_line_count"]:
        failures.append("WebSocket sent one batch per line (batching is not working)")
    if counter.error:
        failures.append(f"WebSocket subscriber failed: {counter.error}")

    print()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        print(f"To inspect: diff {fixture} {captured}")
        return 1
    print("PASS: zero lost lines; raw log is byte-identical to the source fixture.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
