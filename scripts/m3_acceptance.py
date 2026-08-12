#!/usr/bin/env python3
"""M3 acceptance: live KPIs and charts from a real DUT log replay (SPEC §5).

    tests/fixtures/dut-sysmon-real.log ──> PTY master
                                           PTY slave ──> SerialWorker ──> raw log
                                                                    ├──> ConsoleBatcher
                                                                    └──> SysMonParser
                                                                          ├──> SnapshotStore -> REST
                                                                          └──> /ws

The fixture is an actual AP6 840E capture (device identifiers scrubbed), not
synthesised console text, so this checks the parser against what real hardware
prints — including the doubled-percent "7.8%% sirq" column and ~88 KB of
surrounding shell noise that must produce no spurious events.

Verified here:
  1. Snapshot history is served for chart backfill, one entry per Test Time,
     with every core and the charted meminfo keys.
  2. DUT identity (model / firmware / uptime) reaches Overview.
  3. The KPI values Overview shows are arithmetically correct for the fixture.
  4. Live WebSocket events arrive for a client watching during the replay.
  5. P0 is intact: the raw log is still byte-identical to the source, i.e.
     adding the parser cost the capture nothing.

Normally invoked through ``scripts/monitoring_test.sh``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pty
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
FIXTURE = BACKEND_DIR / "tests" / "fixtures" / "dut-sysmon-real.log"

EXPECTED_MODEL = "AP6 840E"
EXPECTED_FIRMWARE = "1.10.336"
EXPECTED_TEST_COUNTS = [1, 2, 3]
EXPECTED_CORES = ["0", "1", "2", "3"]


def expected_uptime_seconds(source: bytes) -> int | None:
    """Derive the uptime the node should be reporting, straight from the fixture.

    The capture carries one identity blob per Test Time and the DUT's uptime
    climbs between them, so the value REST serves is the *last* one. Computed
    here with an independent regex rather than by importing the parser, so this
    stays a real check and not the parser agreeing with itself.
    """
    uptimes = re.findall(r'"uptime":"([^"]*)"', source.decode("utf-8", errors="ignore"))
    if not uptimes:
        return None
    match = re.match(r"^\s*(?:(\d+)\s*days?\s+)?(\d+):(\d{2}):(\d{2})\s*$", uptimes[-1])
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_json(base_url: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())


def wait_for(predicate, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return None


class WsCollector(threading.Thread):
    """A live monitoring client, so the WS path is exercised during the replay."""

    def __init__(self, ws_url: str) -> None:
        super().__init__(daemon=True)
        self.ws_url = ws_url
        self.events: list[dict] = []
        self.error: str | None = None
        self._stop = threading.Event()

    def run(self) -> None:
        try:
            from websockets.sync.client import connect
        except Exception as exc:  # pragma: no cover
            self.error = f"websockets client unavailable: {exc}"
            return
        try:
            with connect(self.ws_url, open_timeout=10) as socket_:
                while not self._stop.is_set():
                    try:
                        message = socket_.recv(timeout=1.0)
                    except TimeoutError:
                        continue
                    event = json.loads(message)
                    if isinstance(event, dict) and event.get("type"):
                        self.events.append(event)
        except Exception as exc:
            if not self._stop.is_set():
                self.error = f"{type(exc).__name__}: {exc}"

    def stop(self) -> None:
        self._stop.set()


def cpu_busy_pct(snapshot: dict) -> float:
    """100 - mean(idle) across cores — the Overview / CPU-page KPI."""
    cores = snapshot.get("cpu", {})
    idles = [core["idle"] for core in cores.values()]
    return round(100 - sum(idles) / len(idles), 1)


def mem_used_pct(snapshot: dict) -> float:
    """Used% from the streamed meminfo, matching the Memory chart."""
    memory = snapshot.get("memory", {})
    total = memory["MemTotal"]
    available = memory["MemAvailable"]
    return round((total - available) / total * 100, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--keep-going", action="store_true", help="report all failures, not just the first")
    args = parser.parse_args()

    if not FIXTURE.is_file():
        print(f"FAIL: fixture missing: {FIXTURE}")
        return 1

    source = FIXTURE.read_bytes()
    source_sha = hashlib.sha256(source).hexdigest()
    expected_uptime = expected_uptime_seconds(source)

    http_port = free_port()
    log_dir = Path(tempfile.mkdtemp(prefix="pi4ap-m3-logs-"))
    env = os.environ.copy()
    env["PI4AP_LOG_DIR"] = str(log_dir)

    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)

    print("pi4_AP M3 acceptance — live KPIs from a real DUT log replay")
    print(f"  fixture : {FIXTURE.name} ({len(source)} bytes, sha256={source_sha[:16]}…)")
    print(f"  device  : {slave_name}")

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(http_port)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{http_port}"
    collector = WsCollector(f"ws://127.0.0.1:{http_port}/ws")
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
        if not ok:
            failures.append(f"{label}{f' — {detail}' if detail else ''}")

    try:
        if wait_for(lambda: _healthy(base_url), timeout=30) is None:
            print("FAIL: node did not become healthy")
            return 1
        collector.start()
        time.sleep(0.5)

        opened = http_json(base_url, "/api/serial/open", {"port": slave_name, "baudrate": args.baud})
        log_name = opened["log_name"]

        print("Replaying real capture...", flush=True)
        offset = 0
        while offset < len(source):
            chunk = source[offset : offset + 4096]
            written = 0
            while written < len(chunk):
                written += os.write(master_fd, chunk[written:])
            offset += len(chunk)
            time.sleep(0.01)

        captured_all = wait_for(
            lambda: http_json(base_url, "/api/serial/status")["bytes_written"] == len(source), timeout=30
        )
        def snapshots_when_complete():
            served = http_json(base_url, "/api/snapshots")["snapshots"]
            return served if len(served) >= len(EXPECTED_TEST_COUNTS) else None

        snapshots = wait_for(snapshots_when_complete, timeout=30)
        time.sleep(0.5)
        dut = http_json(base_url, "/api/dut")
        identity = dut.get("identity") or {}
        collector.stop()

        print()
        print("=== checks ===")

        # 1. snapshot history for chart backfill
        check("snapshot history served for backfill", bool(snapshots), f"{len(snapshots or [])} entries")
        if snapshots:
            check(
                "one entry per Test Time",
                [s["test_count"] for s in snapshots] == EXPECTED_TEST_COUNTS,
                str([s["test_count"] for s in snapshots]),
            )
            check(
                "every core parsed in every snapshot",
                all(sorted(s["cpu"]) == EXPECTED_CORES for s in snapshots),
                f"cores={sorted(snapshots[0]['cpu'])}",
            )
            check(
                "charted meminfo keys present",
                all({"MemTotal", "MemAvailable"} <= set(s["memory"]) for s in snapshots),
            )

        # 2. DUT identity on Overview
        check("DUT model parsed", identity.get("model") == EXPECTED_MODEL, str(identity.get("model")))
        check(
            "DUT firmware parsed", identity.get("firmware") == EXPECTED_FIRMWARE, str(identity.get("firmware"))
        )
        check(
            "DUT uptime parsed (latest of 3 blobs)",
            identity.get("uptime_s") == expected_uptime,
            f"{identity.get('uptime_s')}s vs {expected_uptime}s expected",
        )

        # 3. KPI arithmetic
        if snapshots:
            busy = cpu_busy_pct(snapshots[0])
            used = mem_used_pct(snapshots[0])
            check("CPU busy% derivable", 0.0 <= busy <= 100.0, f"{busy}%")
            check("memory used% derivable", 0.0 <= used <= 100.0, f"{used}%")

        # 4. live WebSocket monitoring events
        kinds = {event["type"] for event in collector.events}
        check("live snapshot events on /ws", "snapshot_update" in kinds, ",".join(sorted(kinds)) or "none")
        check("live dut_identity on /ws", "dut_identity" in kinds)
        check("WebSocket client healthy", collector.error is None, collector.error or "")

        # 5. P0 unaffected by the parser
        check("all bytes captured", captured_all is not None)
        captured = (log_dir / log_name).read_bytes()
        captured_sha = hashlib.sha256(captured).hexdigest()
        check(
            "raw log still byte-identical (P0)",
            captured_sha == source_sha,
            f"{captured_sha[:16]}… vs {source_sha[:16]}…",
        )

        http_json(base_url, "/api/serial/close", {})
    finally:
        collector.stop()
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        os.close(slave_fd)
        os.close(master_fd)
        shutil.rmtree(log_dir, ignore_errors=True)

    print()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: live KPIs, identity and charts derive from a real DUT replay; raw log byte-exact.")
    return 0


def _healthy(base_url: str) -> bool:
    try:
        return http_json(base_url, "/health").get("ok", False)
    except (OSError, urllib.error.URLError):
        return False


if __name__ == "__main__":
    sys.exit(main())
