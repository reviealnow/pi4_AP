"""Parser tests driven by real DUT captures (SPEC §6).

``dut-sysmon-real.log`` is an actual AP6 840E capture, device identifiers
scrubbed. Nothing here is hand-written console text: if the parser stops
understanding what a real DUT emits, these fail.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.parser.sysmon_parser import SysMonParser, parse_uptime_seconds

FIXTURES = Path(__file__).parent / "fixtures"
REAL_LOG = FIXTURES / "dut-sysmon-real.log"
CLIENTS_LOG = FIXTURES / "dut-clients-sample.log"


def replay(path: Path) -> list[dict]:
    """Feed a capture through the parser and collect every emitted event."""
    events: list[dict] = []
    parser = SysMonParser(on_event=events.append)
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            parser.feed(line)
    parser.flush()
    return events


def apply_delta(base: dict, delta: dict) -> dict:
    """Merge a ``snapshot_delta`` the way a browser client must.

    This is the executable statement of the DUT_browser delta contract: a
    ``snapshot_update`` is a baseline and every later change to the same Test
    Time arrives as a delta. The frontend's `applySnapshotDelta` mirrors this.
    """
    merged = deepcopy(base)
    for key in ("test_count", "device_ts"):
        if key in delta:
            merged[key] = delta[key]
    for section, removed_key in (
        ("cpu", "cpu_removed"),
        ("memory", None),
        ("wifi_clients", "wifi_clients_removed"),
    ):
        for gone in delta.get(removed_key or "", []):
            merged[section].pop(gone, None)
        merged[section].update(delta.get(section, {}))
    return merged


def accumulated_snapshots(events: list[dict]) -> list[dict]:
    """Rebuild the per-Test-Time state a client would end up displaying."""
    states: dict[int, dict] = {}
    current: dict | None = None
    for event in events:
        if event["type"] == "snapshot_update":
            current = deepcopy(event["snapshot"])
        elif event["type"] == "snapshot_delta":
            if current is None:
                continue
            current = apply_delta(current, event["delta"])
        else:
            continue
        states[current["test_count"]] = deepcopy(current)
    return [states[key] for key in sorted(states)]


def test_ssid_capability_wait_preserves_interleaved_snapshot_boundary():
    events: list[dict] = []
    parser = SysMonParser(events.append)
    parser.feed("= Test Time: 1, 2026-02-26 09:46:01 =")
    parser.feed("CPU0: 1.9% usr 2.9% sys 0.0% nic 80.6% idle 0.0% io 1.9% irq 12.6% sirq")
    parser.feed("--- SSID CAPABILITY ---")
    parser.feed('{"data":{"model_name":"interleaved identity"}}')
    parser.feed("= Test Time: 2, 2026-02-26 09:46:11 =")
    parser.feed('[{"ssid":"Lab","phy_mode":"11ax"}]')
    parser.flush()

    snapshots = accumulated_snapshots(events)
    capabilities = [event for event in events if event["type"] == "ssid_capability_update"]
    assert [snapshot["test_count"] for snapshot in snapshots] == [1, 2]
    assert capabilities[0]["capabilities"][0]["ssid"] == "Lab"


def test_ssid_capability_wait_expires_when_payload_never_arrives():
    parser = SysMonParser(lambda _event: None)
    parser.feed("--- SSID CAPABILITY ---")
    for _ in range(parser.SSID_CAPABILITY_WAIT_LINES):
        parser.feed("ordinary sysmon noise")
    assert parser._pending_ssid_capability is False


# ----------------------------------------------------------------- fixtures


def test_fixtures_are_present():
    assert REAL_LOG.is_file() and REAL_LOG.stat().st_size > 10_000
    assert CLIENTS_LOG.is_file()


# ------------------------------------------------------------- real capture


def test_real_capture_yields_one_full_snapshot_per_test_time():
    events = replay(REAL_LOG)
    snapshots = accumulated_snapshots(events)
    # The capture contains three "= Test Time:" blocks.
    assert len(snapshots) == 3
    assert [snap["test_count"] for snap in snapshots] == [1, 2, 3]
    assert snapshots[0]["device_ts"] == "2026-06-09 03:45:34"


def test_real_capture_parses_all_four_cores():
    """The sirq column is printed as "7.8%% sirq" on this hardware — the doubled
    percent must not break the match."""
    parser_events = replay(REAL_LOG)
    snapshots = accumulated_snapshots(parser_events)
    for snapshot in snapshots:
        assert sorted(snapshot["cpu"]) == ["0", "1", "2", "3"]
    core0 = snapshots[0]["cpu"]["0"]
    assert core0["idle"] == 86.4
    assert core0["sirq"] == 7.8
    assert core0["sys"] == 4.9


def test_real_capture_streams_the_meminfo_keys_the_charts_need():
    snapshots = accumulated_snapshots(replay(REAL_LOG))
    memory = snapshots[0]["memory"]
    assert memory["MemTotal"] == 843132
    assert memory["MemAvailable"] == 475472
    assert memory["SUnreclaim"] == 158908
    # Only the charted keys are retained, not all of /proc/meminfo.
    assert set(memory) <= SysMonParser.MEM_KEYS


def test_real_capture_yields_dut_identity():
    events = replay(REAL_LOG)
    identities = [event["identity"] for event in events if event["type"] == "dut_identity"]
    assert identities, "no dut_identity event parsed from a real capture"
    identity = identities[0]
    assert identity["model"] == "AP6 840E"
    assert identity["firmware"] == "1.10.336"
    assert identity["uptime"] == "6 days 18:10:52"
    assert identity["uptime_s"] == 6 * 86400 + 18 * 3600 + 10 * 60 + 52
    assert identity["cpu_load"] == 4
    assert identity["mem_load"] == 53
    assert identity["connected_clients"] == 0


def test_identity_is_emitted_once_while_unchanged():
    """The DUT republishes the blob every cycle; only changes go on the wire."""
    events = replay(REAL_LOG)
    identity_events = [event for event in events if event["type"] == "dut_identity"]
    # Three identical blobs in the capture -> a single event (uptime differs per
    # blob in the raw log only if the DUT ran longer; here they repeat).
    assert len(identity_events) <= 3
    assert len({str(event["identity"]) for event in identity_events}) == len(identity_events)


def test_shell_noise_is_ignored():
    """The capture is mostly `ls` output and prompts; none of it may become a
    snapshot, a client update or an identity."""
    events = replay(REAL_LOG)
    assert {event["type"] for event in events} <= {
        "snapshot_update",
        "snapshot_delta",
        "wifi_clients_update",
        "dut_identity",
    }


def test_parser_emits_no_console_events():
    """P0 separation: the console stream does not run through the parser, so the
    parser must never emit console_line_batch (M1's ConsoleBatcher owns it)."""
    events = replay(REAL_LOG)
    assert not [event for event in events if "console" in event["type"]]


# ------------------------------------------------------------------ clients


def test_clients_capture_yields_per_radio_updates():
    events = replay(CLIENTS_LOG)
    updates = [event for event in events if event["type"] == "wifi_clients_update"]
    assert [event["radio"] for event in updates] == ["5G", "2G"]
    assert updates[0]["total_size"] == 2
    assert len(updates[0]["clients"]) == 2
    assert updates[0]["clients"][0]["rssi"] == -42
    assert updates[1]["total_size"] == 1


def test_clients_are_folded_into_the_open_snapshot():
    snapshots = accumulated_snapshots(replay(CLIENTS_LOG))
    assert snapshots[0]["wifi_clients"]["5G"]["total_size"] == 2


# ------------------------------------------------- delta / emission contract


def test_first_snapshot_is_full_then_updates_are_deltas():
    events: list[dict] = []
    parser = SysMonParser(on_event=events.append)
    for line in [
        "= Test Time: 1, 2026-02-26 09:46:01 =",
        "CPU0: 1.0% usr 2.0% sys 0.0% nic 90.0% idle 0.0% io 1.0% irq 6.0% sirq",
        "CPU1: 1.0% usr 2.0% sys 0.0% nic 90.0% idle 0.0% io 1.0% irq 6.0% sirq",
    ]:
        parser.feed(line)

    assert events[0]["type"] == "snapshot_update"
    assert [event["type"] for event in events[1:]] == ["snapshot_delta"]
    assert events[1]["delta"]["cpu"] == {
        "1": {"usr": 1.0, "sys": 2.0, "nic": 0.0, "idle": 90.0, "io": 0.0, "irq": 1.0, "sirq": 6.0}
    }


def test_a_new_test_time_forces_a_full_snapshot():
    events: list[dict] = []
    parser = SysMonParser(on_event=events.append)
    parser.feed("= Test Time: 1, 2026-02-26 09:46:01 =")
    parser.feed("CPU0: 1.0% usr 2.0% sys 0.0% nic 90.0% idle 0.0% io 1.0% irq 6.0% sirq")
    parser.feed("= Test Time: 2, 2026-02-26 09:46:11 =")
    parser.feed("CPU0: 9.0% usr 2.0% sys 0.0% nic 80.0% idle 0.0% io 1.0% irq 8.0% sirq")

    kinds = [event["type"] for event in events]
    assert kinds.count("snapshot_update") == 2, kinds


def test_reset_clears_snapshot_and_identity_state():
    events: list[dict] = []
    parser = SysMonParser(on_event=events.append)
    parser.feed("= Test Time: 1, 2026-02-26 09:46:01 =")
    parser.feed("CPU0: 1.0% usr 2.0% sys 0.0% nic 90.0% idle 0.0% io 1.0% irq 6.0% sirq")
    parser.reset()
    assert parser.identity is None
    events.clear()
    parser.feed("CPU0: 5.0% usr 2.0% sys 0.0% nic 90.0% idle 0.0% io 1.0% irq 6.0% sirq")
    assert events == []  # no open snapshot after reset


# --------------------------------------------------------------- robustness


@pytest.mark.parametrize(
    "line",
    [
        '{"data":{"model_name":"X"',  # truncated JSON
        '{"data":"model_name not an object"}',
        "not json at all but mentions model_name",
        '{"data":{"other":1}}',
    ],
)
def test_malformed_identity_lines_are_ignored(line):
    events: list[dict] = []
    parser = SysMonParser(on_event=events.append)
    parser.feed(line)
    assert events == []
    assert parser.identity is None


def test_malformed_clients_json_is_ignored():
    events: list[dict] = []
    parser = SysMonParser(on_event=events.append)
    parser.feed("--- CLIENTS Radio=5G ---")
    parser.feed('{"data": {"client_list": ')
    assert events == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("6 days 18:10:52 ", 6 * 86400 + 18 * 3600 + 10 * 60 + 52),
        ("1 day 00:00:01", 86401),
        ("18:10:52", 65452),
        ("", None),
        ("not an uptime", None),
    ],
)
def test_uptime_parsing(text, expected):
    assert parse_uptime_seconds(text) == expected
