# pi4_AP

Lightweight DUT monitoring node for Raspberry Pi 4 — a stripped-down field
deployment of [DUT_browser](https://github.com/reviealnow/DUT_browser),
designed to sit next to a mesh node or a DUT that needs close watching.

- **Serial Console first.** The node's primary job is capturing the DUT's
  serial log reliably (always-on raw logging), while still allowing an
  engineer to hand the port over to an external terminal (minicom / TeraTerm)
  for maximum read speed.
- **Five UI pages only:** Overview · CPU / Memory · Wi-Fi clients (detail) ·
  SSID Capability · Site Survey — plus the Serial Console itself.
- **Fleet-ready.** Each pi4_AP node registers and heartbeats to a mother
  server (DUT_browser with the upcoming Fleet feature) so one desktop can
  supervise many Pi nodes.
- **Engineer profile by default.** No auth wall on the LAN; boots headless
  under systemd on Raspberry Pi OS Lite.

## Status

**M2 (console handoff) implemented** — the node captures a DUT's serial output
to rotating raw logs, supports Release/Reacquire for an external terminal, and
offers an opt-in in-process TCP bridge while continuing to log. Milestones
M3–M5 (parsers, monitoring pages, fleet agent and deployment) are still ahead; see
[docs/SPEC.md §5](docs/SPEC.md).

Implementation is done by two LLM coding agents (Claude Opus 5 and GPT-5.6)
under a cross-review workflow — see [docs/SPEC.md](docs/SPEC.md) and
[docs/REVIEW_WORKFLOW.md](docs/REVIEW_WORKFLOW.md).

## Layout

```
backend/    FastAPI app, serial worker  (parsers + fleet agent: M3/M5)
frontend/   React/Vite source; the built dist/ is committed and shipped to the Pi
scripts/    soak_test.sh — the M1 acceptance soak
deploy/     systemd unit, install.sh, config examples  (M5)
docs/       SPEC.md, REVIEW_WORKFLOW.md
```

## Running the node

The Pi needs Python only — `frontend/dist/` is committed, so Node is never
installed on the device (SPEC §2, decision D3).

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cd backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Then open `http://<pi-ip>:8080/`, pick the DUT's port and baud, and click
Connect. Raw capture starts immediately and is written to
`backend/logs/dut-YYYYmmdd-HHMMSS.log` before anything else touches the bytes —
no parser, WebSocket or UI failure can interrupt it.

## Development

```bash
# backend: tests + lint
backend/.venv/bin/pip install -r backend/requirements-dev.txt
cd backend && .venv/bin/python -m pytest && .venv/bin/python -m ruff check .

# frontend: dev server on :5173, proxying /api and /ws to the backend on :8080
cd frontend && npm install && npm run dev

# rebuild the committed bundle after any frontend change
cd frontend && npm run build   # writes frontend/dist/ — commit it
```

Acceptance soak (SPEC §5 — 30 minutes at 115200, zero lost lines):

```bash
./scripts/soak_test.sh                 # the real thing
./scripts/soak_test.sh --duration 60   # 1-minute smoke run of the harness
```

M2 acceptance checks (PTY-backed; no DUT hardware required):

```bash
./scripts/handoff_test.sh
./scripts/rotation_test.sh
./scripts/bridge_test.sh
```

Raw logs rotate at 50 MiB per file with a 200 MiB directory cap by default.
Override with `PI4AP_LOG_SEGMENT_BYTES` and `PI4AP_LOG_TOTAL_BYTES`. Decision
D4 uses the in-process bridge; it is off by default and can be enabled with
`PI4AP_BRIDGE_ENABLED=1` (bind defaults to `0.0.0.0:3333`).

## License

Apache-2.0 — same as DUT_browser, so code can move freely between the two
repositories.
