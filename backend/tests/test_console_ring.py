"""Ring buffer behaviour (SPEC §3.1: last 5000 lines, replayed to new clients)."""

from __future__ import annotations

from app.config import CONSOLE_RING_MAX
from app.services.console_ring import ConsoleRing


def batch(*lines: str) -> dict:
    return {"type": "console_line_batch", "lines": list(lines)}


def test_records_batches_in_order():
    ring = ConsoleRing()
    ring.observe(batch("one", "two"))
    ring.observe(batch("three"))
    assert ring.recent() == ["one", "two", "three"]


def test_evicts_oldest_beyond_capacity():
    ring = ConsoleRing(maxlen=5)
    ring.observe(batch(*[f"line-{i}" for i in range(12)]))
    assert len(ring) == 5
    assert ring.recent() == ["line-7", "line-8", "line-9", "line-10", "line-11"]


def test_default_capacity_is_the_spec_value():
    ring = ConsoleRing()
    ring.observe(batch(*[f"line-{i}" for i in range(CONSOLE_RING_MAX + 250)]))
    assert len(ring) == CONSOLE_RING_MAX
    assert ring.recent()[0] == "line-250"
    assert ring.recent()[-1] == f"line-{CONSOLE_RING_MAX + 249}"


def test_recent_limit_trims_to_newest():
    ring = ConsoleRing()
    ring.observe(batch("a", "b", "c", "d"))
    assert ring.recent(2) == ["c", "d"]
    assert ring.recent(0) == ["a", "b", "c", "d"]


def test_ignores_other_event_types_and_malformed_payloads():
    ring = ConsoleRing()
    ring.observe({"type": "snapshot_update", "snapshot": {}})
    ring.observe({"type": "console_line_batch", "lines": "not-a-list"})
    ring.observe({"type": "console_line_batch", "lines": ["ok", 42, None]})
    ring.observe({})
    assert ring.recent() == ["ok"]


def test_clear_empties_the_ring():
    ring = ConsoleRing()
    ring.observe(batch("a", "b"))
    ring.clear()
    assert ring.recent() == []
    assert len(ring) == 0


def test_observe_never_raises():
    """P0: the ring sits downstream of the raw log and must never throw back."""
    ring = ConsoleRing()

    class Hostile:
        def get(self, *_args, **_kwargs):
            raise ValueError("boom")

    ring.observe(Hostile())  # type: ignore[arg-type]
    assert ring.recent() == []
