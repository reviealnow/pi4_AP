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

from datetime import datetime
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
    try:
        worker.open(port=body.port, baudrate=body.baudrate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.console_ring.clear()
    return {"ok": True, **worker.status()}


@router.post("/close")
def close_serial(request: Request) -> dict:
    request.app.state.serial_worker.close()
    return {"ok": True, **request.app.state.serial_worker.status()}


@router.get("/logs")
def list_logs() -> dict:
    """Raw session logs, newest first. Rotation itself lands in M2."""
    items: list[dict] = []
    if LOG_DIR.is_dir():
        for path in LOG_DIR.glob("dut-*.log"):
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
            except OSError:
                continue
            items.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                }
            )
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return {"logs": items}


@router.get("/logs/{file_name}")
def download_log(file_name: str) -> FileResponse:
    """Download one raw session log verbatim — no analysis, no zipping."""
    safe_name = Path(file_name).name
    if safe_name != file_name or not (safe_name.startswith("dut-") and safe_name.endswith(".log")):
        raise HTTPException(status_code=400, detail="Invalid log name")

    log_path = LOG_DIR / safe_name
    if not log_path.exists() or not log_path.is_file():
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(path=log_path, filename=safe_name, media_type="text/plain")
