"""Batching: >=50 ms windows, and never one WebSocket send per line (SPEC §3.1)."""

from __future__ import annotations

import time

from app.services.console_batcher import ConsoleBatcher


def collector():
    events: list[dict] = []
    return events, events.append


def test_size_threshold_emits_one_batch():
    events, on_event = collector()
    batcher = ConsoleBatcher(on_event=on_event)

    for i in range(ConsoleBatcher.BATCH_SIZE):
        batcher.feed(f"line-{i}")

    assert len(events) == 1
    assert events[0]["type"] == "console_line_batch"
    assert len(events[0]["lines"]) == ConsoleBatcher.BATCH_SIZE


def test_nothing_is_emitted_before_the_window_closes():
    events, on_event = collector()
    batcher = ConsoleBatcher(on_event=on_event)

    batcher.feed("first")
    batcher.feed("second")
    assert events == []  # still inside the 50 ms window

    time.sleep(ConsoleBatcher.BATCH_MAX_LATENCY_SEC * 4)
    assert len(events) == 1
    assert events[0]["lines"] == ["first", "second"]


def test_many_lines_never_produce_one_send_per_line():
    events, on_event = collector()
    batcher = ConsoleBatcher(on_event=on_event)

    total = 500
    for i in range(total):
        batcher.feed(f"line-{i}")
    batcher.flush()

    emitted = [line for event in events for line in event["lines"]]
    assert emitted == [f"line-{i}" for i in range(total)]
    assert len(events) <= total / ConsoleBatcher.BATCH_SIZE + 1

    report = batcher.efficiency_report()
    assert report["console_line_count"] == total
    assert report["average_batch_size"] > 1.0


def test_flush_is_idempotent_and_drains_the_tail():
    events, on_event = collector()
    batcher = ConsoleBatcher(on_event=on_event)

    batcher.feed("tail")
    batcher.flush()
    batcher.flush()

    assert len(events) == 1
    assert events[0]["lines"] == ["tail"]


def test_reset_drops_pending_and_stats():
    events, on_event = collector()
    batcher = ConsoleBatcher(on_event=on_event)

    batcher.feed("dropped")
    batcher.reset()
    time.sleep(ConsoleBatcher.BATCH_MAX_LATENCY_SEC * 4)

    assert events == []
    assert batcher.efficiency_report()["console_line_count"] == 0
