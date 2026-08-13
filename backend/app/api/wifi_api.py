"""Read-only Wi-Fi pages and the serial-only M4 site survey trigger."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request

from app.services.wifi import normalize_clients, parse_site_survey

router = APIRouter(prefix="/api/wifi", tags=["wifi"])
COMMANDS_PATH = Path(__file__).resolve().parents[3] / "config" / "dut_commands.yaml"


def _commands() -> dict[str, str]:
    with COMMANDS_PATH.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream) or {}
    return {str(key): str(value) for key, value in loaded.items()}


@router.get("/clients")
def wifi_clients(request: Request) -> dict:
    return {"clients": normalize_clients(request.app.state.snapshot_store.latest())}


@router.get("/capabilities")
def ssid_capabilities(request: Request) -> dict:
    return {"capabilities": request.app.state.parser.ssid_capabilities}


@router.post("/capabilities/refresh")
def refresh_ssid_capabilities(request: Request) -> dict:
    try:
        request.app.state.serial_worker.capture_command(_commands()["ssid_capability"], timeout=12)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ssid_capabilities(request)


@router.get("/survey")
def last_survey(request: Request) -> dict:
    return request.app.state.site_survey


@router.post("/survey")
def run_survey(request: Request) -> dict:
    try:
        output = request.app.state.serial_worker.capture_command(_commands()["site_survey"], timeout=70)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    request.app.state.site_survey = {
        "timestamp": datetime.now(UTC).isoformat(),
        "results": parse_site_survey(output),
    }
    return request.app.state.site_survey
