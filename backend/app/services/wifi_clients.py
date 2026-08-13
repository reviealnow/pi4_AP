"""Wi-Fi detail parsers ported from DUT_browser branch CPU_Plots."""

from __future__ import annotations

import re

_VAP_RE = re.compile(r'^(ath\d+)\s+IEEE\s+\S+\s+ESSID:"([^"]*)"')
_CHAN_RE = re.compile(r"Frequency:[\d.]+\s*GHz\s*\(Channel\s*(\d+)\)")
_MODE_RE = re.compile(r"Mode:(\w+)")
_MAC_RE = re.compile(r"^([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b")
_RATE_RE = re.compile(r"\b(\d+(?:\.\d+)?[MG])\b")
_NEG_RE = re.compile(r"-\d+")
_ASSOC_RE = re.compile(r"\b(\d{1,2}:\d{2}:\d{2})\b")
_PHYMODE_RE = re.compile(r"IEEE80211_MODE_(\S+)")
_NSS_RE = re.compile(r"IEEE80211_MODE_\S+\s+(\d+)\s+(\d+)")
_SNR_RE = re.compile(r"\bSNR\b\s*[:=]\s*(-?\d+)")


def band_for_iface(iface: str) -> str:
    match = re.match(r"ath(\d+)", iface)
    if not match:
        return "?"
    number = int(match.group(1))
    return "2.4G" if number < 16 else "5G" if number < 32 else "6G"


def discover_vaps(iwconfig_text: str) -> list[dict]:
    vaps: list[dict] = []
    current: dict | None = None
    for line in iwconfig_text.splitlines():
        if head := _VAP_RE.match(line):
            if current:
                vaps.append(current)
            current = {"iface": head.group(1), "ssid": head.group(2),
                       "band": band_for_iface(head.group(1)), "channel": None, "mode": None}
        if current:
            if (channel := _CHAN_RE.search(line)) and current["channel"] is None:
                current["channel"] = int(channel.group(1))
            if (mode := _MODE_RE.search(line)) and current["mode"] is None:
                current["mode"] = mode.group(1)
    if current:
        vaps.append(current)
    return [vap for vap in vaps if (vap["mode"] or "Master") == "Master"]


def parse_wlanconfig_list(text: str, iface: str) -> list[dict]:
    clients: list[dict] = []
    for line in text.splitlines():
        mac_match = _MAC_RE.match(line.strip())
        if mac_match:
            rest = line.strip()[len(mac_match.group(1)):].split()
            rates = _RATE_RE.findall(line)
            negatives = _NEG_RE.findall(line)
            assoc = _ASSOC_RE.search(line)
            phy = _PHYMODE_RE.search(line)
            nss = _NSS_RE.search(line)
            clients.append({
                "iface": iface, "band": band_for_iface(iface), "mac": mac_match.group(1).lower(),
                "aid": int(rest[0]) if rest and rest[0].isdigit() else None,
                "channel": int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None,
                "txrate": rates[0] if rates else None, "rxrate": rates[1] if len(rates) > 1 else None,
                "rssi": int(negatives[0]) if negatives else None,
                "assoc_time": assoc.group(1) if assoc else None,
                "phymode": phy.group(1) if phy else None,
                "rxnss": int(nss.group(1)) if nss else None, "txnss": int(nss.group(2)) if nss else None,
                "snr": None,
            })
        elif clients and (snr := _SNR_RE.search(line)) and clients[-1]["snr"] is None:
            clients[-1]["snr"] = int(snr.group(1))
    return clients


_APSTATS_FIELDS = {
    "tx_bytes": "Tx Data Bytes", "rx_bytes": "Rx Data Bytes",
    "avg_tx_kbps": "Average Tx Rate (kbps)", "avg_rx_kbps": "Average Rx Rate (kbps)",
    "tx_bytes_1s": "Tx bytes for last one second", "rx_bytes_1s": "Rx bytes for last one second",
    "band_width": "Band Width", "rx_rssi": "Rx RSSI", "per": "Last Packet Error Rate (PER)",
}
_APSTATS_NSS_RE = re.compile(r"chainmask\s*\(NSS\)\s+tx\((\d+)\)\s+rx\((\d+)\)")


def parse_apstats(text: str) -> dict:
    stats = {}
    for key, label in _APSTATS_FIELDS.items():
        match = re.search(re.escape(label) + r"\s*=\s*(-?\d+)", text)
        stats[key] = int(match.group(1)) if match else None
    nss = _APSTATS_NSS_RE.search(text)
    stats["tx_nss"] = int(nss.group(1)) if nss else None
    stats["rx_nss"] = int(nss.group(2)) if nss else None
    return stats
