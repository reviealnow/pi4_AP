from __future__ import annotations

import threading

import pytest

from app.serial import serial_worker as serial_worker_module


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Point session logs at a tmp dir so tests never touch backend/logs/."""
    target = tmp_path / "logs"
    monkeypatch.setattr(serial_worker_module, "LOG_DIR", target)
    return target


class FakeSerial:
    """Minimal stand-in for ``serial.Serial`` driven by a scripted byte stream.

    Chunks are handed out one ``read()`` at a time; once exhausted, ``read``
    blocks on an event until the test closes the port, which is how a real
    ``timeout=1`` port behaves when the DUT goes quiet (and proves the reader
    does not busy-wait).
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.is_open = True
        self.drained = threading.Event()
        self._closed = threading.Event()

    @property
    def in_waiting(self) -> int:
        return len(self._chunks[0]) if self._chunks else 0

    def read(self, size: int = 1) -> bytes:
        if self._chunks:
            chunk = self._chunks.pop(0)
            if not self._chunks:
                self.drained.set()
            return chunk[:size]
        self.drained.set()
        self._closed.wait(timeout=1.0)
        return b""

    def close(self) -> None:
        self.is_open = False
        self._closed.set()


@pytest.fixture
def fake_serial(monkeypatch):
    """Install a FakeSerial factory; returns a getter for the built instance."""
    holder: dict[str, FakeSerial] = {}

    def install(chunks: list[bytes]) -> FakeSerial:
        instance = FakeSerial(chunks)
        holder["instance"] = instance

        def factory(*args, **kwargs):
            return instance

        monkeypatch.setattr(serial_worker_module.serial, "Serial", factory)
        return instance

    return install
