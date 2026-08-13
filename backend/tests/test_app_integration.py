"""End-to-end: a real PTY through SerialWorker -> raw log -> ring -> /ws.

Uses ``os.openpty()`` rather than a mocked port, so this exercises the same code
path the M1 soak acceptance test (``scripts/soak_test.sh``) drives — just for a
second instead of thirty minutes, and with no hardware.
"""

from __future__ import annotations

import os
import select
import socket
import threading
import time

import pytest
import serial
from fastapi.testclient import TestClient

from app import main as main_module
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
    assert client.get("/health").json() == {"ok": True, "milestone": "M4"}
    assert isinstance(client.get("/api/serial/ports").json()["ports"], list)


def test_occupied_bridge_port_does_not_abort_node_startup(tmp_path, monkeypatch):
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(serial_worker_module, "LOG_DIR", log_dir)
    monkeypatch.setattr(serial_api_module, "LOG_DIR", log_dir)
    monkeypatch.setattr(main_module, "TCP_BRIDGE_ENABLED", True)
    monkeypatch.setattr(main_module, "TCP_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setattr(main_module, "TCP_BRIDGE_PORT", occupied.getsockname()[1])
    try:
        with TestClient(app) as test_client:
            assert test_client.get("/health").json()["ok"] is True
            bridge = test_client.get("/api/serial/status").json()["bridge"]
            assert bridge["enabled"] is False
            assert "Address already in use" in bridge["last_error"]
    finally:
        occupied.close()


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
    assert efficiency["ws_dropped_batches"] == 0

    # A client connecting now is replayed the ring buffer as a normal batch.
    with client.websocket_connect("/ws") as ws:
        replay = ws.receive_json()
    assert replay["type"] == "console_line_batch"
    assert replay["lines"] == [f"dut boot line {i}" for i in range(40)]

    # Current and historical raw log downloads are both available in M2.
    downloaded = client.get("/api/serial/log")
    assert downloaded.status_code == 200
    assert downloaded.content == payload
    assert f'filename="{log_name}"' in downloaded.headers["content-disposition"]
    listed = client.get("/api/serial/logs").json()["logs"]
    assert [item["name"] for item in listed] == [log_name]
    assert client.get(f"/api/serial/logs/{log_name}").content == payload

    assert client.post("/api/serial/close").json()["connected"] is False


def test_site_survey_capture_keeps_raw_log_and_console(client, pty_pair):
    master_fd, slave_name = pty_pair
    opened = client.post("/api/serial/open", json={"port": slave_name, "baudrate": 115200}).json()
    response_bytes = (
        b"Cell 01 - Address: 02:11:22:33:44:55\n"
        b" Channel:6\n Quality=60/70 Signal level=-49 dBm\n ESSID:\"Lab\"\n"
    )

    def dut_shell():
        command = os.read(master_fd, 4096).decode()
        sentinel = command.split("echo ", 1)[1].strip()
        os.write(master_fd, response_bytes + sentinel.encode() + b"\n")

    thread = threading.Thread(target=dut_shell)
    thread.start()
    surveyed = client.post("/api/wifi/survey")
    thread.join(timeout=2)
    assert surveyed.status_code == 200, surveyed.text
    assert surveyed.json()["results"][0]["ssid"] == "Lab"
    assert wait_for(lambda: client.get("/api/console/efficiency").json()["ring_lines"] >= 5)
    downloaded = client.get(f"/api/serial/logs/{opened['log_name']}").content
    assert downloaded == response_bytes + downloaded[-len(downloaded.splitlines()[-1]) - 1 :]
    assert response_bytes in downloaded
    client.post("/api/serial/close")


def test_survey_fails_immediately_when_port_is_released(client, pty_pair):
    _master_fd, slave_name = pty_pair
    client.post("/api/serial/open", json={"port": slave_name, "baudrate": 115200})
    client.post("/api/serial/release")
    response = client.post("/api/wifi/survey")
    assert response.status_code == 409
    assert response.json()["detail"] == "Port released to external terminal"


def test_client_detail_refresh_runs_configured_serial_commands(client, monkeypatch):
    worker = client.app.state.serial_worker
    outputs = iter([
        'ath16     IEEE 802.11axa  ESSID:"Lab-5"\n          Mode:Master',
        "02:11:22:33:44:55 1 36 866M 780M -48 00:12:03 IEEE80211_MODE_11AXA_HE80 2 2",
        "Average Tx Rate (kbps) = 900000\nAverage Rx Rate (kbps) = 700000",
    ])
    commands: list[str] = []

    def capture(command: str, timeout: float) -> str:
        commands.append(command)
        return next(outputs)

    monkeypatch.setattr(worker, "capture_command", capture)
    response = client.post("/api/wifi/clients/refresh")
    assert response.status_code == 200, response.text
    row = response.json()["clients"][0]
    assert (row["tx_rate"], row["rx_rate"], row["ssid"]) == ("900.0M", "700.0M", "Lab-5")
    assert commands == ["iwconfig", "wlanconfig ath16 list", "apstats -s -m 02:11:22:33:44:55"]
    assert client.get("/api/wifi/clients").json()["timestamp"] is not None


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


def test_reconnecting_client_is_replayed_a_snapshot_baseline(client, pty_pair):
    """M3 review finding: the browser folds snapshot_delta onto a per-connection
    baseline that REST backfill cannot reach. Without a snapshot_update at
    connect time, a reconnecting client drops every delta until the DUT's next
    Test Time (~70 s) and its charts sit stale."""
    master_fd, slave_name = pty_pair
    assert client.post("/api/serial/open", json={"port": slave_name, "baudrate": 115200}).status_code == 200

    os.write(
        master_fd,
        b"= Test Time: 1, 2026-06-09 03:45:34 =\n"
        b"CPU0:   0.0% usr   4.9% sys   0.0% nic  86.4% idle   0.0% io   1.0% irq   7.8%% sirq\n"
        b"CPU1:   1.0% usr   0.0% sys   0.0% nic  98.0% idle   0.0% io   0.0% irq   1.0%% sirq\n"
        b"MemTotal:         843132 kB\n"
        b"MemAvailable:     475472 kB\n"
        b'{"data":{"model_name":"AP6 840E","uptime":"1 day 00:00:01",'
        b'"firmware_version":"1.10.336","workload":{"cpu_load":4}},"error_code":0}\n',
    )
    assert wait_for(lambda: client.get("/api/snapshots").json()["snapshots"])

    # A client connecting *now* stands in for a reconnect after a drop. The
    # baseline is replayed first, so reading one event is enough to catch a
    # regression — no blocking on an event that never arrives.
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot_update", f"baseline not replayed first, got {first['type']}"
        replay = [first, ws.receive_json(), ws.receive_json()]

    by_type = {event["type"]: event for event in replay}
    snapshot = by_type["snapshot_update"]["snapshot"]
    # The baseline must be the *accumulated* state, not just the first core.
    assert sorted(snapshot["cpu"]) == ["0", "1"]
    assert snapshot["memory"]["MemAvailable"] == 475472
    assert by_type["dut_identity"]["identity"]["model"] == "AP6 840E"
    assert "console_line_batch" in by_type

    client.post("/api/serial/close")


def test_backfill_carries_wifi_client_counts(client, pty_pair):
    """M3 review finding: a reloaded page seeds its Wi-Fi client KPI from REST
    history, so the snapshots that history serves must carry the parsed
    per-radio counts — otherwise Overview reports nothing after a reload even
    though the node parsed a client block."""
    master_fd, slave_name = pty_pair
    assert client.post("/api/serial/open", json={"port": slave_name, "baudrate": 115200}).status_code == 200

    os.write(
        master_fd,
        b"= Test Time: 1, 2026-02-26 09:46:01 =\n"
        b"CPU0: 1.9% usr 2.9% sys 0.0% nic 80.6% idle 0.0% io 1.9% irq 12.6% sirq\n"
        b"--- CLIENTS Radio=5G ---\n"
        b'{"data": {"total_size": 2, "client_list": ['
        b'{"mac":"AA:BB:CC:00:11:22","rssi":-42},{"mac":"AA:BB:CC:00:11:33","rssi":-55}]}}\n',
    )

    def snapshot_with_clients():
        served = client.get("/api/snapshots").json()["snapshots"]
        return served if served and served[-1].get("wifi_clients") else None

    snapshots = wait_for(snapshot_with_clients)
    assert snapshots is not None, "snapshot history never carried the client block"
    assert snapshots[-1]["wifi_clients"]["5G"]["total_size"] == 2
    assert len(snapshots[-1]["wifi_clients"]["5G"]["clients"]) == 2

    # The same counts must also reach a reconnecting client's replay baseline.
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
    assert first["type"] == "snapshot_update"
    assert first["snapshot"]["wifi_clients"]["5G"]["total_size"] == 2

    client.post("/api/serial/close")


def test_replay_is_empty_before_any_dut_output(client):
    """No snapshot yet must not fabricate one — a client just gets nothing."""
    with client.websocket_connect("/ws") as ws:
        ws.send_text("ping")  # keeps the socket open long enough to observe
    assert client.get("/api/snapshots").json()["snapshots"] == []


def test_current_log_download_is_404_before_any_session(client):
    assert client.get("/api/serial/log").status_code == 404


def test_release_external_terminal_reacquire_and_send(client, pty_pair):
    master_fd, slave_name = pty_pair
    assert client.post("/api/serial/open", json={"port": slave_name, "baudrate": 115200}).status_code == 200

    released = client.post("/api/serial/release").json()
    assert released["released"] is True
    assert released["connected"] is False

    with serial.Serial(slave_name, 115200, timeout=1) as external:
        os.write(master_fd, b"external terminal owns this\n")
        assert external.read_until(b"\n") == b"external terminal owns this\n"

    reacquired = client.post("/api/serial/reacquire").json()
    assert reacquired["released"] is False
    assert reacquired["connected"] is True

    sent = client.post("/api/serial/send", json={"text": "show status"})
    assert sent.status_code == 200, sent.text
    readable, _, _ = select.select([master_fd], [], [], 2)
    assert readable
    assert os.read(master_fd, 1024) == b"show status\n"
    client.post("/api/serial/close")
