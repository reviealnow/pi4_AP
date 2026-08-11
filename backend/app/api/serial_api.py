"""REST surface for M1 — only what the Serial Console page needs.

Ported from DUT_browser's ``app/api/serial_api.py``: the ``/ports`` listing and
the open/close/send shape. Cut: replay mode, terminal enter/exit/resize, the
Wi-Fi kick route, and the whole analyzer-bundle download workflow (that route
runs ``analyzer3.py`` and zips a session — pi4_AP downloads the plain raw log).
Also cut: ``POST /send``. Sending a line to the DUT belongs to the console-UX
milestone (SPEC §3.1 lists send-line there); M1's page has no send box, so the
route would be an endpoint with no caller.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from serial.tools import list_ports

from app.config import DEFAULT_BAUDRATE

router = APIRouter(prefix="/api/serial", tags=["serial"])


class SerialOpenRequest(BaseModel):
    port: str
    baudrate: int = DEFAULT_BAUDRATE


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
    return request.app.state.serial_worker.status()


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

    try:
        worker.open(port=body.port, baudrate=body.baudrate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **worker.status()}


@router.post("/close")
def close_serial(request: Request) -> dict:
    request.app.state.serial_worker.close()
    return {"ok": True, **request.app.state.serial_worker.status()}


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
