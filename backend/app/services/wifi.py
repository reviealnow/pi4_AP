"""Format-faithful parsers for M4 on-demand DUT Wi-Fi captures."""

from __future__ import annotations

import re

_CELL = re.compile(r"Cell \d+ - Address: ([0-9A-Fa-f:]{17})")
_CHANNEL = re.compile(r"Channel[:=](\d+)")
_ESSID = re.compile(r'ESSID:"(.*)"')
_SIGNAL = re.compile(r"Signal level[=:](-?\d+)\s*dBm")


def parse_site_survey(text: str) -> list[dict]:
    """Parse BusyBox/wireless-tools ``iwlist scan`` cell blocks."""
    results: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        if match := _CELL.search(line):
            if current:
                results.append(current)
            current = {"ssid": None, "bssid": match.group(1).lower(), "channel": None,
                       "rssi": None, "security": "Open"}
            continue
        if current is None:
            continue
        if match := _CHANNEL.search(line):
            current["channel"] = int(match.group(1))
        if match := _ESSID.search(line):
            current["ssid"] = match.group(1)
        if match := _SIGNAL.search(line):
            current["rssi"] = int(match.group(1))
        if "Encryption key:on" in line:
            current["security"] = "Encrypted"
        if "WPA3" in line or "SAE" in line:
            current["security"] = "WPA3"
        elif "WPA2" in line or "IEEE 802.11i" in line:
            current["security"] = "WPA2"
    if current:
        results.append(current)
    return results


def normalize_clients(snapshot: dict | None) -> list[dict]:
    """Flatten the existing wifi_clients_update/snapshot contract for REST."""
    rows: list[dict] = []
    for radio, payload in (snapshot or {}).get("wifi_clients", {}).items():
        for raw in payload.get("clients", []):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row.setdefault("band", radio)
            row.setdefault("bss", row.get("iface") or row.get("ssid"))
            row.setdefault("hostname", row.get("host_name") or row.get("name"))
            row.setdefault("tx_rate", row.get("txrate") or row.get("tx_rate_mbps"))
            row.setdefault("rx_rate", row.get("rxrate") or row.get("rx_rate_mbps"))
            row.setdefault("airtime", row.get("airtime_pct"))
            rows.append(row)
    return rows
