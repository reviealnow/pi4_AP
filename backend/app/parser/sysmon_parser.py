"""DUT sysmon output parser.

Ported from DUT_browser's ``app/parser/sysmon_parser.py`` (branch ``CPU_Plots``).
Kept verbatim: the snapshot/CPU/meminfo/clients regexes, the snapshot
accumulation, and the full-vs-delta emission logic including
``_build_snapshot_delta`` — that is the event contract the mother server already
speaks, and M3 extends it rather than forking it.

Cut: the console line queueing and flush timer. In DUT_browser that machinery is
entangled with this class; pi4_AP lifted it into
``app/services/console_batcher.py`` back in M1, and the console stream no longer
runs through the parser at all. That matters for P0: a parser failure cannot
cost the console a line, because the console never depends on the parser.

Added for M3 (not present in DUT_browser — see PR): ``dut_identity``. The DUT
emits a JSON status blob carrying model, firmware, uptime and a workload
summary; DUT_browser ignores it entirely, but SPEC §3.2 requires Overview to
show "DUT identity (model/FW from parser)" and uptime, so it is parsed here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy


def parse_uptime_seconds(text: str) -> int | None:
    """Convert the DUT's uptime string to seconds.

    Seen in real captures as ``"6 days 18:10:52 "``; also tolerates a bare
    ``"18:10:52"`` and a singular ``"1 day"``. Returns None when unparseable —
    SPEC §3.3's fleet payload wants ``uptime_s``, and a wrong number there is
    worse than no number.
    """
    match = re.match(r"^\s*(?:(\d+)\s*days?\s+)?(\d+):(\d{2}):(\d{2})\s*$", text)
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)


class SysMonParser:
    """Turns DUT console lines into snapshot / clients / identity events."""

    SNAPSHOT_RE = re.compile(r"^= Test Time:\s*(\d+),\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*=*\s*$")
    # Real captures print the sirq column as "7.8%% sirq" (doubled percent);
    # DUT_browser's replay sample prints "12.6% sirq". The optional second % in
    # the original regex covers both, so it is kept exactly as-is.
    CPU_RE = re.compile(
        r"^CPU(\d+):\s*([\d.]+)% usr\s+([\d.]+)% sys\s+([\d.]+)% nic\s+"
        r"([\d.]+)% idle\s+([\d.]+)% io\s+([\d.]+)% irq\s+([\d.]+)%%?\s+sirq\s*$"
    )
    CLIENT_MARKER_RE = re.compile(r"^--- CLIENTS Radio=(2G|5G|6G) ---\s*$")
    # A /proc/meminfo line, e.g. "MemAvailable:     475472 kB". Streamed inside
    # the snapshot block, so it is parsed live like CPU per-core.
    MEMINFO_RE = re.compile(r"^(\w+):\s+(\d+)\s*kB\s*$")
    # Only the keys the dashboard charts need (mirrors DUT_browser).
    MEM_KEYS = frozenset(
        {"MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "Slab", "SReclaimable", "SUnreclaim"}
    )
    # Cheap pre-filter before attempting json.loads on a console line.
    IDENTITY_HINT = '"model_name"'
    SSID_CAPABILITY_MARKER = "--- SSID CAPABILITY ---"

    def __init__(self, on_event: Callable[[dict], None]) -> None:
        self.on_event = on_event
        self._current_snapshot: dict | None = None
        self._last_emitted_snapshot: dict | None = None
        self._pending_clients_radio: str | None = None
        self._identity: dict | None = None
        self._ssid_capabilities: list[dict] = []
        self._pending_ssid_capability = False
        self._snapshot_full_count = 0
        self._snapshot_delta_count = 0

    def reset(self) -> None:
        self._current_snapshot = None
        self._last_emitted_snapshot = None
        self._pending_clients_radio = None
        self._identity = None
        self._ssid_capabilities = []
        self._pending_ssid_capability = False
        self._snapshot_full_count = 0
        self._snapshot_delta_count = 0

    @property
    def identity(self) -> dict | None:
        """Latest DUT identity, for REST backfill on page load."""
        return self._identity

    @property
    def ssid_capabilities(self) -> list[dict]:
        return deepcopy(self._ssid_capabilities)

    def efficiency_report(self) -> dict:
        delta_full_ratio = (
            self._snapshot_delta_count / self._snapshot_full_count if self._snapshot_full_count > 0 else 0.0
        )
        return {
            "snapshot_delta_count": self._snapshot_delta_count,
            "snapshot_full_count": self._snapshot_full_count,
            "delta_full_ratio": round(delta_full_ratio, 3),
        }

    def feed(self, line: str) -> None:
        text = line.rstrip("\r\n")

        if text == self.SSID_CAPABILITY_MARKER:
            self._pending_ssid_capability = True
            return
        if self._pending_ssid_capability:
            self._pending_ssid_capability = False
            self._consume_ssid_capability_json(text)
            return

        snap_match = self.SNAPSHOT_RE.match(text)
        if snap_match:
            self._emit_current_snapshot()
            self._current_snapshot = {
                "test_count": int(snap_match.group(1)),
                "device_ts": snap_match.group(2),
                "cpu": {},
                "memory": {},
                "wifi_clients": {},
            }
            self._pending_clients_radio = None
            return

        marker_match = self.CLIENT_MARKER_RE.match(text)
        if marker_match:
            self._pending_clients_radio = marker_match.group(1)
            return

        if self._pending_clients_radio is not None and text.lstrip().startswith("{"):
            self._consume_clients_json(text)
            return

        # The identity blob arrives as a bare JSON line outside any snapshot
        # block, so it is checked before the snapshot-scoped branches below.
        if self.IDENTITY_HINT in text:
            self._consume_identity_json(text)
            return

        if self._current_snapshot is None:
            return

        cpu_match = self.CPU_RE.match(text)
        if not cpu_match:
            mem_match = self.MEMINFO_RE.match(text)
            if mem_match and mem_match.group(1) in self.MEM_KEYS:
                self._current_snapshot["memory"][mem_match.group(1)] = int(mem_match.group(2))
                self._emit_snapshot_update()
            return

        core_id = cpu_match.group(1)
        self._current_snapshot["cpu"][core_id] = {
            "usr": float(cpu_match.group(2)),
            "sys": float(cpu_match.group(3)),
            "nic": float(cpu_match.group(4)),
            "idle": float(cpu_match.group(5)),
            "io": float(cpu_match.group(6)),
            "irq": float(cpu_match.group(7)),
            "sirq": float(cpu_match.group(8)),
        }
        self._emit_snapshot_update()

    def flush(self) -> None:
        self._emit_current_snapshot()

    # ------------------------------------------------------------- identity

    def _consume_ssid_capability_json(self, text: str) -> None:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return
        rows = parsed.get("data") if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            return
        self._ssid_capabilities = [row for row in rows if isinstance(row, dict)]
        self.on_event({"type": "ssid_capability_update", "capabilities": deepcopy(self._ssid_capabilities)})

    def _consume_identity_json(self, text: str) -> None:
        """Parse the DUT's JSON status blob into a ``dut_identity`` event."""
        start = text.find("{")
        if start < 0:
            return
        try:
            parsed = json.loads(text[start:])
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(parsed, dict):
            return
        data = parsed.get("data")
        if not isinstance(data, dict) or "model_name" not in data:
            return

        workload = data.get("workload")
        workload = workload if isinstance(workload, dict) else {}
        uptime_text = str(data.get("uptime", "")).strip()

        identity = {
            "model": data.get("model_name"),
            "product": data.get("product_name"),
            "firmware": data.get("firmware_version"),
            "mac": data.get("mac_address"),
            "serial": data.get("sn_number"),
            "ip": data.get("ip_address"),
            "uptime": uptime_text or None,
            "uptime_s": parse_uptime_seconds(uptime_text),
            "device_ts": data.get("system_time"),
            "cpu_load": _as_number(workload.get("cpu_load")),
            "mem_load": _as_number(workload.get("mem_load")),
            "connected_clients": _as_number(workload.get("connected_clients")),
        }
        # Identity is republished periodically; only emit on an actual change so
        # a quiet DUT does not generate WebSocket traffic every cycle.
        if identity == self._identity:
            return
        self._identity = identity
        self.on_event({"type": "dut_identity", "identity": identity})

    # -------------------------------------------------------------- clients

    def _consume_clients_json(self, text: str) -> None:
        radio = self._pending_clients_radio
        self._pending_clients_radio = None
        if radio is None:
            return

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(parsed, dict):
            return
        data = parsed.get("data")
        if not isinstance(data, dict):
            return

        clients_raw = data.get("client_list")
        clients = clients_raw if isinstance(clients_raw, list) else []
        try:
            total_size = int(data.get("total_size"))
        except (TypeError, ValueError):
            total_size = len(clients)

        self.on_event(
            {
                "type": "wifi_clients_update",
                "radio": radio,
                "total_size": total_size,
                "clients": clients,
            }
        )

        if self._current_snapshot is not None:
            self._current_snapshot["wifi_clients"][radio] = {
                "total_size": total_size,
                "clients": clients,
            }
            # Publish the updated snapshot too, not just the live
            # wifi_clients_update. DUT_browser folds the counts in silently and
            # relies on a later CPU/meminfo line to push them out; when the
            # CLIENTS block ends the snapshot — which is where DUTs tend to put
            # it — nothing ever does, so the counts never reach the snapshot
            # store and a reloaded page reports no clients. M3 review finding.
            self._emit_snapshot_update()

    # ------------------------------------------------------------ snapshots

    def _emit_current_snapshot(self) -> None:
        if self._current_snapshot is None:
            return
        self._emit_snapshot_update()
        self._current_snapshot = None

    def _emit_snapshot_update(self) -> None:
        if self._current_snapshot is None:
            return
        current_snapshot = deepcopy(self._current_snapshot)
        previous_snapshot = self._last_emitted_snapshot
        if previous_snapshot is None or self._is_snapshot_boundary(previous_snapshot, current_snapshot):
            self.on_event({"type": "snapshot_update", "snapshot": current_snapshot})
            self._last_emitted_snapshot = current_snapshot
            self._snapshot_full_count += 1
            return

        delta = self._build_snapshot_delta(previous_snapshot, current_snapshot)
        if delta:
            self.on_event({"type": "snapshot_delta", "delta": delta})
            self._last_emitted_snapshot = current_snapshot
            self._snapshot_delta_count += 1

    def _build_snapshot_delta(self, previous: dict, current: dict) -> dict:
        delta: dict = {}
        if previous.get("test_count") != current.get("test_count"):
            delta["test_count"] = current.get("test_count")
        if previous.get("device_ts") != current.get("device_ts"):
            delta["device_ts"] = current.get("device_ts")

        previous_cpu = previous.get("cpu") if isinstance(previous.get("cpu"), dict) else {}
        current_cpu = current.get("cpu") if isinstance(current.get("cpu"), dict) else {}
        changed_cpu = {
            core: metrics for core, metrics in current_cpu.items() if previous_cpu.get(core) != metrics
        }
        if changed_cpu:
            delta["cpu"] = changed_cpu
        removed_cpu = sorted(set(previous_cpu.keys()) - set(current_cpu.keys()))
        if removed_cpu:
            delta["cpu_removed"] = removed_cpu

        # Memory keys are a fixed set that only changes value, never disappears,
        # so a "removed" list (like cpu_removed) is unnecessary.
        previous_mem = previous.get("memory") if isinstance(previous.get("memory"), dict) else {}
        current_mem = current.get("memory") if isinstance(current.get("memory"), dict) else {}
        changed_mem = {key: value for key, value in current_mem.items() if previous_mem.get(key) != value}
        if changed_mem:
            delta["memory"] = changed_mem

        previous_wifi = previous.get("wifi_clients") if isinstance(previous.get("wifi_clients"), dict) else {}
        current_wifi = current.get("wifi_clients") if isinstance(current.get("wifi_clients"), dict) else {}
        changed_wifi = {
            radio: payload for radio, payload in current_wifi.items() if previous_wifi.get(radio) != payload
        }
        if changed_wifi:
            delta["wifi_clients"] = changed_wifi
        removed_wifi = sorted(set(previous_wifi.keys()) - set(current_wifi.keys()))
        if removed_wifi:
            delta["wifi_clients_removed"] = removed_wifi

        return delta

    def _is_snapshot_boundary(self, previous: dict, current: dict) -> bool:
        return (
            previous.get("test_count") != current.get("test_count")
            or previous.get("device_ts") != current.get("device_ts")
        )


def _as_number(value) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
