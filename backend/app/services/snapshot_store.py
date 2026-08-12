"""In-memory history of accumulated DUT snapshots, for chart backfill.

Mirrors the ``observe(event)`` / ``recent(limit)`` shape of DUT_browser's
``app/services/snapshot_store.py``, but keeps history in memory only. SPEC §4
puts a database out of scope and says state is "in-memory + raw logs on disk",
and unlike the mother server this node has no offline-analysis feature that
needs snapshots to outlive the process.

The store is what makes the parser's delta contract usable by a page that loads
late: the parser emits one full ``snapshot_update`` per Test Time followed by
``snapshot_delta``s, and this class folds them back into whole snapshots so
``GET /api/snapshots`` can hand a fresh client a ready-made chart series.
"""

from __future__ import annotations

import threading
from collections import deque
from copy import deepcopy

# One snapshot per DUT "Test Time" — roughly every 70 s on the reference
# hardware, so this covers well beyond the 2 h window SPEC §3.2 asks for.
SNAPSHOT_HISTORY_MAX = 1000

_SECTIONS = (
    ("cpu", "cpu_removed"),
    ("memory", None),
    ("wifi_clients", "wifi_clients_removed"),
)


def apply_snapshot_delta(base: dict, delta: dict) -> dict:
    """Fold a ``snapshot_delta`` onto a baseline snapshot.

    Kept identical in behaviour to the frontend's ``applySnapshotDelta`` and to
    the reducer asserted in ``tests/test_sysmon_parser.py`` — three
    implementations of one contract, so a drift shows up as a test failure.
    """
    merged = deepcopy(base)
    for key in ("test_count", "device_ts"):
        if key in delta:
            merged[key] = delta[key]
    for section, removed_key in _SECTIONS:
        target = merged.setdefault(section, {})
        if removed_key:
            for gone in delta.get(removed_key, []):
                target.pop(gone, None)
        incoming = delta.get(section)
        if isinstance(incoming, dict):
            target.update(incoming)
    return merged


class SnapshotStore:
    """Thread-safe ring of accumulated snapshots, newest last."""

    def __init__(self, maxlen: int = SNAPSHOT_HISTORY_MAX) -> None:
        self._lock = threading.Lock()
        self._snapshots: deque[dict] = deque(maxlen=maxlen)
        self._current: dict | None = None

    def observe(self, event: dict) -> None:
        """Fold one parser event into history. Never raises into the reader."""
        try:
            event_type = event.get("type")
            if event_type == "snapshot_update":
                snapshot = event.get("snapshot")
                if not isinstance(snapshot, dict):
                    return
                current = deepcopy(snapshot)
            elif event_type == "snapshot_delta":
                delta = event.get("delta")
                if not isinstance(delta, dict) or self._current is None:
                    return
                current = apply_snapshot_delta(self._current, delta)
            else:
                return

            with self._lock:
                self._current = current
                # One entry per Test Time: replace while the same snapshot is
                # still filling in, append when a new Test Time starts.
                if self._snapshots and self._snapshots[-1].get("test_count") == current.get("test_count"):
                    self._snapshots[-1] = deepcopy(current)
                else:
                    self._snapshots.append(deepcopy(current))
        except Exception:
            # Downstream of the P0 raw log: never propagate.
            return

    def recent(self, limit: int = 0) -> list[dict]:
        with self._lock:
            snapshots = list(self._snapshots)
        if limit > 0:
            snapshots = snapshots[-limit:]
        return snapshots

    def latest(self) -> dict | None:
        with self._lock:
            return deepcopy(self._current) if self._current is not None else None

    def clear(self) -> None:
        with self._lock:
            self._snapshots.clear()
            self._current = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._snapshots)
