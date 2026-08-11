from __future__ import annotations

import socket
import time

from app.serial.tcp_bridge import TcpSerialBridge


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_bridge_is_bidirectional_and_reports_clients():
    serial_writes: list[bytes] = []
    bridge = TcpSerialBridge(serial_writes.append, "127.0.0.1", 0)
    bridge.start()
    try:
        with socket.create_connection(("127.0.0.1", bridge.bound_port), timeout=2) as client:
            assert wait_for(lambda: bridge.status()["clients"] == 1)
            client.sendall(b"command from tcp\n")
            assert wait_for(lambda: serial_writes == [b"command from tcp\n"])

            bridge.publish(b"logged dut output\n")
            assert client.recv(1024) == b"logged dut output\n"
    finally:
        bridge.close()

    assert bridge.status()["enabled"] is False


def test_slow_tcp_client_never_blocks_raw_publish():
    bridge = TcpSerialBridge(lambda _data: None, "127.0.0.1", 0)
    bridge.start()
    try:
        with socket.create_connection(("127.0.0.1", bridge.bound_port), timeout=2):
            assert wait_for(lambda: bridge.status()["clients"] == 1)
            started = time.monotonic()
            for _ in range(1000):
                bridge.publish(b"x" * 65536)
            elapsed = time.monotonic() - started
            assert elapsed < 0.5
            assert bridge.status()["dropped_output_chunks"] > 0
    finally:
        bridge.close()
