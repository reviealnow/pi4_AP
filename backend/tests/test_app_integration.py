"""End-to-end: a real PTY through SerialWorker -> raw log -> ring -> /ws.

Uses ``os.openpty()`` rather than a mocked port, so this exercises the same code
path the M1 soak acceptance test (``scripts/soak_test.sh``) drives — just for a
second instead of thirty minutes, and with no hardware.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from app.api import serial_api as serial_api_module
from app.main import app
from app.serial import serial_worker as serial_worker_module


@pytest.fixture
def pty_pair():
    master_fd, slave_fd = os.openpty()
    slave_name = os.ttyname(slave_fd)
    try:
        yield master_fd, slave_name
    finally:
        os.close(slave_fd)
        os.close(master_fd)


@pytest.fixture
def client(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(serial_worker_module, "LOG_DIR", log_dir)
    monkeypatch.setattr(serial_api_module, "LOG_DIR", log_dir)
    with TestClient(app) as test_client:
        yield test_client


def wait_for(predicate, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    return None


def test_health_and_port_listing(client):
    assert client.get("/health").json() == {"ok": True, "milestone": "M1"}
    assert isinstance(client.get("/api/serial/ports").json()["ports"], list)


def test_open_requires_a_port(client):
    response = client.post("/api/serial/open", json={"port": "", "baudrate": 115200})
    assert response.status_code == 400


def test_open_reports_a_bad_device(client):
    response = client.post("/api/serial/open", json={"port": "/dev/nope-not-here", "baudrate": 115200})
    assert response.status_code == 400


def test_capture_stream_replay_and_download(client, pty_pair):
    master_fd, slave_name = pty_pair

    opened = client.post("/api/serial/open", json={"port": slave_name, "baudrate": 115200})
    assert opened.status_code == 200, opened.text
    status = opened.json()
    assert status["connected"] is True
    assert status["port"] == slave_name
    log_name = status["log_name"]

    payload = b"".join(f"dut boot line {i}\n".encode() for i in range(40))
    os.write(master_fd, payload)

    # ...the bytes land in the raw log,
    assert wait_for(lambda: client.get("/api/serial/status").json()["bytes_written"] == len(payload))
    # ...and they are coalesced into batches rather than one send per line.
    def efficiency_when_full():
        report = client.get("/api/console/efficiency").json()
        return report if report["ring_lines"] == 40 else None

    efficiency = wait_for(efficiency_when_full)
    assert efficiency is not None, "ring never filled"
    assert efficiency["console_batch_count"] < 40
    assert efficiency["average_batch_size"] > 1.0

    # A client connecting now is replayed the ring buffer as a normal batch.
    with client.websocket_connect("/ws") as ws:
        replay = ws.receive_json()
    assert replay["type"] == "console_line_batch"
    assert replay["lines"] == [f"dut boot line {i}" for i in range(40)]

    # The raw log downloads verbatim.
    listed = client.get("/api/serial/logs").json()["logs"]
    assert [item["name"] for item in listed] == [log_name]

    downloaded = client.get(f"/api/serial/logs/{log_name}")
    assert downloaded.status_code == 200
    assert downloaded.content == payload

    assert client.post("/api/serial/close").json()["connected"] is False


def test_live_lines_reach_a_connected_client(client, pty_pair):
    master_fd, slave_name = pty_pair
    assert client.post("/api/serial/open", json={"port": slave_name, "baudrate": 115200}).status_code == 200

    with client.websocket_connect("/ws") as ws:
        os.write(master_fd, b"live-one\nlive-two\n")
        event = ws.receive_json()
        assert event["type"] == "console_line_batch"
        assert event["lines"] == ["live-one", "live-two"]

    client.post("/api/serial/close")


def test_reopening_does_not_replay_the_previous_session(client, pty_pair):
    """A new session starts the console view empty — the old DUT's lines must
    not leak into the new one's replay."""
    master_fd, slave_name = pty_pair
    client.post("/api/serial/open", json={"port": slave_name, "baudrate": 115200})
    os.write(master_fd, b"old-session-line\n")
    assert wait_for(lambda: client.get("/api/console/efficiency").json()["ring_lines"] >= 1)

    client.post("/api/serial/open", json={"port": slave_name, "baudrate": 115200})
    assert client.get("/api/console/efficiency").json()["ring_lines"] == 0

    os.write(master_fd, b"new-session-line\n")
    assert wait_for(lambda: client.get("/api/console/efficiency").json()["ring_lines"] == 1)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["lines"] == ["new-session-line"]

    client.post("/api/serial/close")


def test_log_download_rejects_traversal_and_unknown_names(client):
    assert client.get("/api/serial/logs/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)
    assert client.get("/api/serial/logs/notes.txt").status_code == 400
    assert client.get("/api/serial/logs/dut-19700101-000000.log").status_code == 404
