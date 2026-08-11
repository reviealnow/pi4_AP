"""SerialWorker driven by a fake byte stream — no hardware required.

The P0 contract under test (SPEC §1, §3.1): *every byte read is appended to the
raw log before any other processing, and no downstream failure may interrupt
raw logging.*
"""

from __future__ import annotations

import time

from app.serial.serial_worker import SerialWorker


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def run_worker(fake_serial, log_dir, chunks, on_line=None) -> tuple[SerialWorker, bytes]:
    """Open a worker over a scripted stream, let it drain, close it, return the log."""
    fake = fake_serial(chunks)
    worker = SerialWorker(on_line=on_line)
    worker.open(port="/dev/fake", baudrate=115200)
    try:
        assert wait_for(fake.drained.is_set), "worker never consumed the scripted stream"
        expected = sum(len(chunk) for chunk in chunks)
        assert wait_for(lambda: worker.status()["bytes_written"] == expected), "bytes not all logged"
    finally:
        worker.close()
    assert log_dir.is_dir()
    log_files = list(log_dir.glob("dut-*.log"))
    assert len(log_files) == 1
    return worker, log_files[0].read_bytes()


# --------------------------------------------------------------- raw logging


def test_raw_log_is_byte_exact(fake_serial, log_dir):
    """The log holds the DUT bytes and nothing else — no header, no re-wrapping.

    This is the property the M1 soak acceptance test leans on: source fixture
    and captured log must diff clean.
    """
    payload = b"boot line 1\nboot line 2\r\nbinary \x00\xff bytes\nno trailing newline"
    chunks = [payload[i : i + 7] for i in range(0, len(payload), 7)]

    _worker, written = run_worker(fake_serial, log_dir, chunks)

    assert written == payload


def test_log_file_is_named_per_spec(fake_serial, log_dir):
    fake_serial([b"x\n"])
    worker = SerialWorker()
    worker.open(port="/dev/fake", baudrate=115200)
    try:
        name = worker.status()["log_name"]
    finally:
        worker.close()
    # logs/dut-YYYYmmdd-HHMMSS.log
    assert name is not None
    stem = name.removeprefix("dut-").removesuffix(".log")
    date_part, _, time_part = stem.partition("-")
    assert len(date_part) == 8 and date_part.isdigit()
    assert len(time_part) == 6 and time_part.isdigit()


def test_partial_lines_are_logged_without_waiting_for_a_newline(fake_serial, log_dir):
    """A chunk with no terminator must still hit the log immediately — a DUT
    sitting at a prompt would otherwise leave bytes unrecorded indefinitely."""
    fake = fake_serial([b"root@AP:/# "])
    worker = SerialWorker()
    worker.open(port="/dev/fake", baudrate=115200)
    try:
        assert wait_for(fake.drained.is_set)
        assert wait_for(lambda: worker.status()["bytes_written"] == 11)
        log_path = log_dir / worker.status()["log_name"]
        assert wait_for(lambda: log_path.read_bytes() == b"root@AP:/# ")
    finally:
        worker.close()


# ------------------------------------------------------- ordering guarantee


def test_raw_log_is_written_before_the_line_is_dispatched(fake_serial, log_dir):
    """Ordering proof: when a console line reaches the consumer, its bytes are
    already on disk. Read straight from the file — not from an in-process
    counter — so this checks the durable record, not bookkeeping."""
    observed: list[tuple[str, bytes]] = []
    log_holder: dict[str, object] = {}

    def on_line(line: str) -> None:
        log_path = log_holder["path"]
        observed.append((line, log_path.read_bytes()))  # type: ignore[union-attr]

    fake = fake_serial([b"alpha\n", b"bravo\n", b"charlie\n"])
    worker = SerialWorker(on_line=on_line)
    worker.open(port="/dev/fake", baudrate=115200)
    log_holder["path"] = log_dir / worker.status()["log_name"]
    try:
        assert wait_for(fake.drained.is_set)
        assert wait_for(lambda: len(observed) == 3)
    finally:
        worker.close()

    assert [line for line, _ in observed] == ["alpha", "bravo", "charlie"]
    # At each dispatch the line's own bytes were already durable on disk.
    assert observed[0][1] == b"alpha\n"
    assert observed[1][1] == b"alpha\nbravo\n"
    assert observed[2][1] == b"alpha\nbravo\ncharlie\n"


def test_a_failing_consumer_never_stops_raw_logging(fake_serial, log_dir):
    """P0: a parser/batcher/WebSocket blow-up must not cost a single byte."""
    calls: list[str] = []

    def exploding_on_line(line: str) -> None:
        calls.append(line)
        raise RuntimeError("downstream consumer is on fire")

    payload = b"".join(f"line-{i}\n".encode() for i in range(50))
    chunks = [payload[i : i + 13] for i in range(0, len(payload), 13)]

    _worker, written = run_worker(fake_serial, log_dir, chunks, on_line=exploding_on_line)

    assert written == payload
    # The consumer really was invoked (and really did raise) at least once.
    assert calls


# ------------------------------------------------------------ line assembly


def test_lines_are_reassembled_across_chunk_boundaries(fake_serial, log_dir):
    lines: list[str] = []
    fake = fake_serial([b"hel", b"lo wor", b"ld\r\nsec", b"ond\n"])
    worker = SerialWorker(on_line=lines.append)
    worker.open(port="/dev/fake", baudrate=115200)
    try:
        assert wait_for(fake.drained.is_set)
        assert wait_for(lambda: len(lines) == 2)
    finally:
        worker.close()

    assert lines == ["hello world", "second"]


def test_trailing_unterminated_line_is_emitted_on_close(fake_serial, log_dir):
    lines: list[str] = []
    fake = fake_serial([b"done\nhalf-a-li"])
    worker = SerialWorker(on_line=lines.append)
    worker.open(port="/dev/fake", baudrate=115200)
    assert wait_for(fake.drained.is_set)
    assert wait_for(lambda: lines == ["done"])
    worker.close()

    assert lines == ["done", "half-a-li"]


def test_undecodable_bytes_do_not_break_the_line_stream(fake_serial, log_dir):
    lines: list[str] = []
    fake = fake_serial([b"good\n\xff\xfe bad\ntail\n"])
    worker = SerialWorker(on_line=lines.append)
    worker.open(port="/dev/fake", baudrate=115200)
    try:
        assert wait_for(fake.drained.is_set)
        assert wait_for(lambda: len(lines) == 3)
    finally:
        worker.close()

    assert lines[0] == "good"
    assert lines[2] == "tail"


# ----------------------------------------------------------------- lifecycle


def test_status_reports_the_open_session(fake_serial, log_dir):
    fake_serial([b"hello\n"])
    worker = SerialWorker()
    assert worker.status()["connected"] is False

    worker.open(port="/dev/ttyFAKE0", baudrate=921600)
    try:
        status = worker.status()
        assert status["connected"] is True
        assert status["port"] == "/dev/ttyFAKE0"
        assert status["baudrate"] == 921600
        assert status["log_path"] is not None
        assert status["opened_at"] is not None
    finally:
        worker.close()

    closed = worker.status()
    assert closed["connected"] is False


def test_reopening_within_the_same_second_never_shares_a_log(fake_serial, log_dir):
    """Two sessions must never interleave into one file, even when the reconnect
    lands inside the same timestamp second."""
    fake_serial([b"first session\n"])
    worker = SerialWorker()
    worker.open(port="/dev/fake", baudrate=115200)
    first = worker.status()["log_name"]
    assert wait_for(lambda: worker.status()["bytes_written"] == 14)
    worker.close()

    fake_serial([b"second session\n"])
    worker.open(port="/dev/fake", baudrate=115200)
    second = worker.status()["log_name"]
    assert wait_for(lambda: worker.status()["bytes_written"] == 15)
    worker.close()

    assert first != second
    assert {path.name for path in log_dir.glob("dut-*.log")} == {first, second}
    assert (log_dir / first).read_bytes() == b"first session\n"
    assert (log_dir / second).read_bytes() == b"second session\n"


def test_a_failing_raw_log_write_is_reported_not_swallowed(fake_serial, log_dir, monkeypatch):
    """Disk full is the one failure that legitimately stops capture — but it has
    to surface, not leave the console silently dead."""
    fake = fake_serial([b"before\n", b"after\n"])
    worker = SerialWorker()
    worker.open(port="/dev/fake", baudrate=115200)

    def explode(_data: bytes) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(worker._log_fp, "write", explode)
    try:
        assert wait_for(lambda: worker.status()["last_error"] is not None)
    finally:
        worker.close()

    assert fake is not None
    assert "raw log write failed" in worker.status()["last_error"]
    assert "No space left on device" in worker.status()["last_error"]


def test_intentional_close_does_not_report_an_error(fake_serial, log_dir):
    """Closing the port yanks the fd from under the blocking read. That is the
    normal disconnect path, so it must not leave an error on the status pill."""
    fake = fake_serial([b"streaming\n"])
    worker = SerialWorker()
    worker.open(port="/dev/fake", baudrate=115200)
    assert wait_for(fake.drained.is_set)
    worker.close()

    assert worker.status()["last_error"] is None


def test_close_is_safe_when_never_opened():
    SerialWorker().close()
