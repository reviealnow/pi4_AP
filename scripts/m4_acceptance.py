#!/usr/bin/env python3
"""M4 acceptance: replay + live serial capture populate all Wi-Fi pages."""

from __future__ import annotations

import hashlib
import json
import os
import pty
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
CLIENTS = BACKEND / "tests/fixtures/dut-clients-sample.log"
CAPS = BACKEND / "tests/fixtures/ssid-capability-synthetic.log"
SURVEY = BACKEND / "tests/fixtures/site-survey-synthetic.log"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); return sock.getsockname()[1]


def request(base: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=80) as response:
        return json.loads(response.read())


def wait_for(fn, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = fn()
            if value: return value
        except OSError:
            pass
        time.sleep(.05)
    return None


def main() -> int:
    replay = CLIENTS.read_bytes() + CAPS.read_bytes()
    survey = SURVEY.read_bytes()
    logs = Path(tempfile.mkdtemp(prefix="pi4ap-m4-logs-"))
    master, slave = pty.openpty(); slave_name = os.ttyname(slave)
    port = free_port(); base = f"http://127.0.0.1:{port}"
    env = os.environ.copy(); env["PI4AP_LOG_DIR"] = str(logs)
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures: list[str] = []; live_bytes = bytearray()

    def check(label: str, ok: bool, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'} {label}{' — ' + detail if detail else ''}")
        if not ok: failures.append(label)

    try:
        assert wait_for(lambda: request(base, "/health").get("ok"), 30), "node did not start"
        opened = request(base, "/api/serial/open", {"port": slave_name, "baudrate": 115200})
        os.write(master, replay)
        assert wait_for(lambda: request(base, "/api/serial/status")["bytes_written"] >= len(replay))

        def dut_scan():
            command = os.read(master, 4096).decode()
            sentinel = command.split("echo ", 1)[1].strip()
            payload = survey + sentinel.encode() + b"\n"
            live_bytes.extend(payload); os.write(master, payload)

        shell = threading.Thread(target=dut_scan); shell.start()
        scanned = request(base, "/api/wifi/survey", {}); shell.join(5)
        clients = request(base, "/api/wifi/clients")["clients"]
        caps = request(base, "/api/wifi/capabilities")["capabilities"]
        expected = replay + bytes(live_bytes)
        assert wait_for(lambda: request(base, "/api/serial/status")["bytes_written"] == len(expected))
        raw = (logs / opened["log_name"]).read_bytes()

        print("pi4_AP M4 acceptance (synthetic format-faithful fixtures disclosed)")
        check("Wi-Fi clients populated from replay", len(clients) > 0, f"{len(clients)} rows")
        check("SSID capability populated from parser event", len(caps) == 2, f"{len(caps)} BSSes")
        check("site survey populated from live serial command", len(scanned["results"]) == 2)
        check("survey cached with timestamp", bool(request(base, "/api/wifi/survey")["timestamp"]))
        check("raw log byte-identical across capture", raw == expected,
              f"{hashlib.sha256(raw).hexdigest()[:12]} vs {hashlib.sha256(expected).hexdigest()[:12]}")
        request(base, "/api/serial/close", {})
    finally:
        server.send_signal(signal.SIGTERM)
        try: server.wait(10)
        except subprocess.TimeoutExpired: server.kill()
        os.close(slave); os.close(master); shutil.rmtree(logs, ignore_errors=True)
    print("PASS" if not failures else "FAIL: " + ", ".join(failures))
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
