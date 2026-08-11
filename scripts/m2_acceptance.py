#!/usr/bin/env python3
"""Hardware-free M2 acceptance harness for handoff, rotation and TCP bridge."""

from __future__ import annotations

import argparse
import json
import os
import pty
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import serial

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_json(base_url: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def download(base_url: str, path: str) -> bytes:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=10) as response:
        return response.read()


def wait_for(predicate, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError("timed out waiting for node state")


class Node:
    def __init__(self, extra_env: dict[str, str] | None = None) -> None:
        self.http_port = free_port()
        self.log_dir = Path(tempfile.mkdtemp(prefix="pi4ap-m2-logs-"))
        env = os.environ.copy()
        env["PI4AP_LOG_DIR"] = str(self.log_dir)
        env.update(extra_env or {})
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.http_port),
            ],
            cwd=BACKEND_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.base_url = f"http://127.0.0.1:{self.http_port}"
        wait_for(self._healthy, timeout=20)

    def _healthy(self) -> bool:
        try:
            return http_json(self.base_url, "/health").get("ok", False)
        except (OSError, urllib.error.URLError):
            return False

    def close(self) -> None:
        self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        shutil.rmtree(self.log_dir, ignore_errors=True)


def open_pty() -> tuple[int, int, str]:
    master_fd, slave_fd = pty.openpty()
    return master_fd, slave_fd, os.ttyname(slave_fd)


def read_fd(fd: int, expected: int, timeout: float = 5.0) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + timeout
    while len(output) < expected and time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.25)
        if readable:
            output.extend(os.read(fd, expected - len(output)))
    return bytes(output)


def recv_exact(sock: socket.socket, expected: int) -> bytes:
    output = bytearray()
    while len(output) < expected:
        chunk = sock.recv(expected - len(output))
        if not chunk:
            break
        output.extend(chunk)
    return bytes(output)


def handoff() -> None:
    node = Node()
    master_fd, slave_fd, slave_name = open_pty()
    try:
        opened = http_json(node.base_url, "/api/serial/open", {"port": slave_name, "baudrate": 115200})
        assert opened["connected"] and not opened["released"]
        os.write(master_fd, b"before release\n")
        wait_for(lambda: http_json(node.base_url, "/api/serial/status")["bytes_written"] >= 15)

        released = http_json(node.base_url, "/api/serial/release", {})
        assert released["released"] and not released["connected"]
        with serial.Serial(slave_name, 115200, timeout=2) as terminal:
            os.write(master_fd, b"external terminal round trip\n")
            assert terminal.read_until(b"\n") == b"external terminal round trip\n"

        reacquired = http_json(node.base_url, "/api/serial/reacquire", {})
        assert reacquired["connected"] and not reacquired["released"]
        os.write(master_fd, b"after reacquire\n")
        wait_for(lambda: http_json(node.base_url, "/api/serial/status")["bytes_written"] >= 16)
        assert download(node.base_url, "/api/serial/log") == b"after reacquire\n"
        print(f"released device : {slave_name}")
        print("external terminal: read round-trip OK")
        print("reacquire logging: 16/16 bytes captured")
        print("PASS: release -> external terminal -> reacquire round-trip works.")
    finally:
        os.close(slave_fd)
        os.close(master_fd)
        node.close()


def _segment_number(name: str) -> int:
    marker = "-part"
    return int(name.rsplit(marker, 1)[1].removesuffix(".log")) if marker in name else 1


def rotation() -> None:
    segment = 4096
    total_cap = 12288
    node = Node(
        {
            "PI4AP_LOG_SEGMENT_BYTES": str(segment),
            "PI4AP_LOG_TOTAL_BYTES": str(total_cap),
        }
    )
    master_fd, slave_fd, slave_name = open_pty()
    payload = bytes(range(256)) * 120
    try:
        http_json(node.base_url, "/api/serial/open", {"port": slave_name, "baudrate": 115200})
        os.write(master_fd, payload)
        wait_for(lambda: http_json(node.base_url, "/api/serial/status")["bytes_written"] == len(payload))
        http_json(node.base_url, "/api/serial/close", {})
        logs = http_json(node.base_url, "/api/serial/logs")["logs"]
        logs.sort(key=lambda item: _segment_number(item["name"]))
        retained = b"".join(download(node.base_url, f"/api/serial/logs/{item['name']}") for item in logs)
        total = sum(item["size"] for item in logs)
        assert total <= total_cap
        assert retained == payload[-total:]
        print(f"source bytes  : {len(payload)}")
        print(f"retained bytes: {total}")
        print(f"log files     : {len(logs)}")
        print(f"configured cap: {total_cap}")
        print("PASS: rotation preserves the retained byte tail and caps disk usage.")
    finally:
        os.close(slave_fd)
        os.close(master_fd)
        node.close()


def bridge() -> None:
    bridge_port = free_port()
    node = Node(
        {
            "PI4AP_BRIDGE_ENABLED": "1",
            "PI4AP_BRIDGE_HOST": "127.0.0.1",
            "PI4AP_BRIDGE_PORT": str(bridge_port),
        }
    )
    master_fd, slave_fd, slave_name = open_pty()
    try:
        http_json(node.base_url, "/api/serial/open", {"port": slave_name, "baudrate": 115200})
        with socket.create_connection(("127.0.0.1", bridge_port), timeout=5) as client:
            wait_for(lambda: http_json(node.base_url, "/api/serial/status")["bridge"]["clients"] == 1)
            command = b"command over tcp\n"
            client.sendall(command)
            assert read_fd(master_fd, len(command)) == command

            response = b"dut response tee\n"
            os.write(master_fd, response)
            assert recv_exact(client, len(response)) == response
            wait_for(lambda: http_json(node.base_url, "/api/serial/status")["bytes_written"] == len(response))
            assert download(node.base_url, "/api/serial/log") == response
        print(f"bridge endpoint: 127.0.0.1:{bridge_port}")
        print("TCP -> serial : byte-exact")
        print("serial -> TCP : byte-exact")
        print("raw log tee   : byte-exact")
        print("PASS: in-process TCP bridge is bidirectional and keeps raw logging.")
    finally:
        os.close(slave_fd)
        os.close(master_fd)
        node.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("handoff", "rotation", "bridge"))
    args = parser.parse_args()
    {"handoff": handoff, "rotation": rotation, "bridge": bridge}[args.mode]()


if __name__ == "__main__":
    main()
