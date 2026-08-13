"""Read-only Wi-Fi pages and the serial-only M4 site survey trigger."""

from __future__ import annotations

from datetime import UTC, datetime

import yaml
from fastapi import APIRouter, HTTPException, Request

from app.config import COMMANDS_PATH
from app.services.wifi import normalize_clients, parse_site_survey
from app.services.wifi_clients import discover_vaps, parse_apstats, parse_wlanconfig_list

router = APIRouter(prefix="/api/wifi", tags=["wifi"])


def _commands() -> dict[str, str]:
    with COMMANDS_PATH.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream) or {}
    return {str(key): str(value) for key, value in loaded.items()}


@router.get("/clients")
def wifi_clients(request: Request) -> dict:
    cached = request.app.state.wifi_client_scan
    if cached["timestamp"] is not None:
        return cached
    return {
        "clients": normalize_clients(request.app.state.snapshot_store.latest()),
        "timestamp": None,
    }


@router.post("/clients/refresh")
def refresh_wifi_clients(request: Request) -> dict:
    """Explicit on-demand serial enrichment; page loads never send commands."""
    worker = request.app.state.serial_worker
    commands = _commands()
    snapshot_clients = normalize_clients(request.app.state.snapshot_store.latest())
    by_mac = {str(row.get("mac", "")).lower(): row for row in snapshot_clients if row.get("mac")}
    try:
        vaps = discover_vaps(worker.capture_command(commands["wifi_interfaces"], timeout=8))
        for vap in vaps:
            command = commands["wifi_client_list"].format(iface=vap["iface"])
            for detail in parse_wlanconfig_list(worker.capture_command(command, timeout=8), vap["iface"]):
                detail["ssid"] = vap["ssid"]
                detail["bss"] = vap["iface"]
                mac = detail["mac"].lower()
                merged = {**by_mac.get(mac, {}), **detail}
                stats_command = commands["wifi_client_stats"].format(mac=mac)
                try:
                    stats = parse_apstats(worker.capture_command(stats_command, timeout=8))
                except (RuntimeError, TimeoutError):
                    stats = {}
                merged["stats"] = stats
                if stats.get("avg_tx_kbps") is not None:
                    merged["tx_rate"] = f"{stats['avg_tx_kbps'] / 1000:.1f}M"
                if stats.get("avg_rx_kbps") is not None:
                    merged["rx_rate"] = f"{stats['avg_rx_kbps'] / 1000:.1f}M"
                by_mac[mac] = merged
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result = {
        "clients": list(by_mac.values()),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    request.app.state.wifi_client_scan = result
    return result


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
