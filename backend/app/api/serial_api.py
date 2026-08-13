"""REST surface for the M2 Serial Console and port handoff.

Ported from DUT_browser's ``app/api/serial_api.py``: the ``/ports`` listing and
the open/close/send shape. Cut: replay mode, terminal enter/exit/resize, the
Wi-Fi kick route, and the whole analyzer-bundle download workflow (that route
runs ``analyzer3.py`` and zips a session — pi4_AP downloads the plain raw log).
M2 restores send, Release/Reacquire, and raw-log list/download while leaving
parsers, monitoring commands and analyzer bundles out of scope.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from serial.tools import list_ports

from app.config import DEFAULT_BAUDRATE, LOG_DIR

router = APIRouter(prefix="/api/serial", tags=["serial"])


class SerialOpenRequest(BaseModel):
    port: str
    baudrate: int = DEFAULT_BAUDRATE


class SerialSendRequest(BaseModel):
    text: str


@router.get("/ports")
def list_serial_ports() -> dict:
    ports = []
    for info in list_ports.comports():
        ports.append(
            {
                "device": info.device,
                "description": info.description or "",
                "hwid": info.hwid or "",
            }
        )
    return {"ports": ports}


@router.get("/status")
def serial_status(request: Request) -> dict:
    status = request.app.state.serial_worker.status()
    status["bridge"] = request.app.state.tcp_bridge.status()
    return status


@router.post("/open")
def open_serial(body: SerialOpenRequest, request: Request) -> dict:
    worker = request.app.state.serial_worker
    if not body.port:
        raise HTTPException(status_code=400, detail="A serial port is required")

    # Retire the previous session before the new one starts, in this order:
    # stop the reader, drain its trailing lines into the ring, then empty the
    # ring. Clearing after open() instead would race the new session's first
    # lines and wipe them from the console view.
    worker.close()
    request.app.state.console_batcher.flush()
    request.app.state.console_ring.clear()
    # A new DUT session starts with no history: the previous DUT's snapshots and
    # identity must not linger in the charts or on Overview.
    request.app.state.parser.reset()
    request.app.state.snapshot_store.clear()

    try:
        worker.open(port=body.port, baudrate=body.baudrate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **serial_status(request)}


@router.post("/close")
def close_serial(request: Request) -> dict:
    request.app.state.serial_worker.close()
    return {"ok": True, **serial_status(request)}


@router.post("/release")
def release_serial(request: Request) -> dict:
    try:
        request.app.state.serial_worker.release()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **serial_status(request)}


@router.post("/reacquire")
def reacquire_serial(request: Request) -> dict:
    try:
        request.app.state.serial_worker.reacquire()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **serial_status(request)}


@router.post("/send")
def send_serial(body: SerialSendRequest, request: Request) -> dict:
    try:
        request.app.state.serial_worker.send(body.text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/log")
def download_current_log(request: Request) -> FileResponse:
    """Download only the current (most recently opened) raw session log."""
    current = request.app.state.serial_worker.current_log_path
    if current is None:
        raise HTTPException(status_code=404, detail="No current log")
    log_path = Path(current)
    if not log_path.exists() or not log_path.is_file():
        raise HTTPException(status_code=404, detail="Current log not found")
    return FileResponse(path=log_path, filename=log_path.name, media_type="text/plain")


@router.get("/logs")
def list_logs() -> dict:
    logs = []
    if LOG_DIR.is_dir():
        for path in LOG_DIR.glob("dut-*.log"):
            try:
                stat = path.stat()
            except OSError:
                continue
            logs.append({"name": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    logs.sort(key=lambda item: (item["mtime_ns"], item["name"]), reverse=True)
    return {"logs": logs}


@router.get("/logs/{file_name}")
def download_log(file_name: str) -> FileResponse:
    safe_name = Path(file_name).name
    if safe_name != file_name or not (safe_name.startswith("dut-") and safe_name.endswith(".log")):
        raise HTTPException(status_code=400, detail="Invalid log name")
    path = LOG_DIR / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(path=path, filename=safe_name, media_type="text/plain")
