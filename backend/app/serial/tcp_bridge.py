"""Small in-process raw TCP bridge for the single DUT serial port (M2/D4)."""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from queue import Empty, Full, Queue


class TcpSerialBridge:
    """Forward TCP client bytes to serial and tee logged DUT bytes back out."""

    def __init__(self, write_serial: Callable[[bytes], None], host: str, port: int) -> None:
        self._write_serial = write_serial
        self._host = host
        self._port = port
        self._server: socket.socket | None = None
        self._clients: dict[socket.socket, Queue[bytes]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None
        self._dropped_output_chunks = 0

    @property
    def bound_port(self) -> int:
        server = self._server
        return server.getsockname()[1] if server is not None else self._port

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self._server is not None,
                "host": self._host,
                "port": self.bound_port,
                "clients": len(self._clients),
                "last_error": self._last_error,
                "dropped_output_chunks": self._dropped_output_chunks,
            }

    def start(self) -> None:
        if self._server is not None:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((self._host, self._port))
        except OSError:
            server.close()
            raise
        server.listen(4)
        server.settimeout(0.5)
        self._server = server
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="serial-tcp-bridge", daemon=True)
        self._thread.start()

    def disable_with_error(self, exc: OSError) -> None:
        """Record a startup failure without making the optional bridge fatal."""
        with self._lock:
            self._last_error = f"bridge start failed: {exc}"

    def close(self) -> None:
        self._stop.set()
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except OSError:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def publish(self, data: bytes) -> None:
        """Tee bytes only after SerialWorker has committed them to the raw log."""
        with self._lock:
            clients = list(self._clients.items())
        for _client, output_queue in clients:
            try:
                output_queue.put_nowait(data)
            except Full:
                # Bridge delivery is best-effort and strictly downstream of the
                # raw log. Drop the oldest queued chunk; never block SerialWorker.
                try:
                    output_queue.get_nowait()
                    output_queue.task_done()
                except Empty:
                    pass
                try:
                    output_queue.put_nowait(data)
                except Full:
                    pass
                with self._lock:
                    self._dropped_output_chunks += 1

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            server = self._server
            if server is None:
                return
            try:
                client, _address = server.accept()
            except TimeoutError:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self._last_error = f"bridge accept failed: {exc}"
                return
            client.settimeout(0.5)
            output_queue: Queue[bytes] = Queue(maxsize=64)
            with self._lock:
                self._clients[client] = output_queue
            threading.Thread(
                target=self._client_loop,
                args=(client,),
                name="serial-tcp-client",
                daemon=True,
            ).start()
            threading.Thread(
                target=self._client_writer_loop,
                args=(client, output_queue),
                name="serial-tcp-writer",
                daemon=True,
            ).start()

    def _client_loop(self, client: socket.socket) -> None:
        try:
            while not self._stop.is_set():
                try:
                    data = client.recv(65536)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                try:
                    self._write_serial(data)
                except Exception as exc:
                    self._last_error = f"bridge serial write failed: {exc}"
                    return
        finally:
            self._drop_client(client)

    def _client_writer_loop(self, client: socket.socket, output_queue: Queue[bytes]) -> None:
        while not self._stop.is_set():
            try:
                data = output_queue.get(timeout=0.5)
            except Empty:
                continue
            try:
                client.sendall(data)
            except OSError:
                self._drop_client(client)
                return
            finally:
                output_queue.task_done()

    def _drop_client(self, client: socket.socket) -> None:
        with self._lock:
            self._clients.pop(client, None)
        try:
            client.close()
        except OSError:
            pass
