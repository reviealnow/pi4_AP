"""pi4_AP node — FastAPI application (M3: console + handoff + parser/monitoring).

Single process, single port :8080 (SPEC §2 / decision D1). The pipeline is:

    SerialWorker (thread) -> raw log (always on, P0)
                          -> ConsoleBatcher -> ConsoleRing ----> WebSocketManager
                          -> SysMonParser   -> SnapshotStore --/

Structure ported from DUT_browser's ``app/main.py``, cut to one DUT (SPEC §4:
one node = one serial port = one DUT), so the ``DutRegistry`` indirection is
dropped and the components hang off ``app.state`` directly.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app.api.serial_api import router as serial_router
from app.config import (
    FRONTEND_DIST,
    HOST,
    PORT,
    TCP_BRIDGE_ENABLED,
    TCP_BRIDGE_HOST,
    TCP_BRIDGE_PORT,
)
from app.parser.sysmon_parser import SysMonParser
from app.serial.serial_worker import SerialWorker
from app.serial.tcp_bridge import TcpSerialBridge
from app.services.console_batcher import ConsoleBatcher
from app.services.console_ring import ConsoleRing
from app.services.snapshot_store import SnapshotStore
from app.websocket.ws_manager import WebSocketManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()

    ws_manager = WebSocketManager()
    ws_manager.bind_loop(loop)
    console_ring = ConsoleRing()

    snapshot_store = SnapshotStore()

    def on_event(event: dict) -> None:
        # Runs on the SerialWorker (or batch-timer) thread. Stores first so a
        # client connecting mid-flight still finds the data in its backfill.
        console_ring.observe(event)
        snapshot_store.observe(event)
        ws_manager.emit_from_thread(event)

    batcher = ConsoleBatcher(on_event=on_event)
    parser = SysMonParser(on_event=on_event)

    def dispatch_line(line: str) -> None:
        """Fan one console line out to the console stream and the parser.

        Each consumer is isolated. SerialWorker already guards the whole
        dispatch, but that guard aborts the rest of the chunk: a parser blowing
        up on line 3 of 10 would silently drop lines 4-10 from the console. The
        console is fed first and independently, so the parser can never cost it
        a line — the same P0 ordering the raw log gets, one level down.
        """
        try:
            batcher.feed(line)
        except Exception:
            pass
        try:
            parser.feed(line)
        except Exception:
            pass

    serial_worker = SerialWorker(on_line=dispatch_line)
    tcp_bridge = TcpSerialBridge(serial_worker.write_raw, TCP_BRIDGE_HOST, TCP_BRIDGE_PORT)
    serial_worker.set_raw_callback(tcp_bridge.publish)
    if TCP_BRIDGE_ENABLED:
        try:
            tcp_bridge.start()
        except OSError as exc:
            # The bridge is optional. A stale ser2net or another listener must
            # not prevent the node (and therefore P0 raw logging) from booting.
            tcp_bridge.disable_with_error(exc)

    app.state.ws_manager = ws_manager
    app.state.console_ring = console_ring
    app.state.console_batcher = batcher
    app.state.serial_worker = serial_worker
    app.state.tcp_bridge = tcp_bridge
    app.state.parser = parser
    app.state.snapshot_store = snapshot_store

    try:
        yield
    finally:
        serial_worker.close()
        tcp_bridge.close()
        batcher.flush()
        batcher.reset()
        parser.flush()
        await ws_manager.close()


app = FastAPI(title="pi4_AP node", lifespan=lifespan)
app.include_router(serial_router)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "milestone": "M3"}


@app.get("/api/snapshots")
def get_snapshots(limit: int = 240) -> dict:
    """Accumulated snapshot history, so charts populate instantly on page load
    instead of waiting for the DUT's next Test Time (~70 s on real hardware)."""
    limit = max(1, min(limit, 1000))
    return {"snapshots": app.state.snapshot_store.recent(limit)}


@app.get("/api/dut")
def get_dut_identity() -> dict:
    """Latest parsed DUT identity (model / firmware / uptime) for Overview."""
    return {
        "identity": app.state.parser.identity,
        "snapshot": app.state.snapshot_store.latest(),
        "parser": app.state.parser.efficiency_report(),
    }


@app.get("/api/console/efficiency")
def console_efficiency() -> dict:
    """WS batching stats — proof that console lines are coalesced, not sent one
    per line (REVIEW_WORKFLOW checklist: "no per-line WS sends")."""
    report = app.state.console_batcher.efficiency_report()
    report["ring_lines"] = len(app.state.console_ring)
    report["ws_clients"] = app.state.ws_manager.client_count
    report["ws_dropped_batches"] = app.state.ws_manager.dropped_event_count
    return report


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    manager: WebSocketManager = app.state.ws_manager
    console_ring: ConsoleRing = app.state.console_ring
    await manager.connect(ws, replay=console_ring.recent)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# Serve the built frontend (single-port production). Mounted LAST and only when
# the build exists, so /api/* and /ws keep priority and dev mode (no dist/ ->
# Vite serves the UI) is unaffected.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT)
