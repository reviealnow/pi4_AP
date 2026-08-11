# pi4_AP — Specification v0.1 (planning)

Target: a **lightweight DUT monitoring node** running on Raspberry Pi 4
(Raspberry Pi OS Lite 64-bit), derived from
[DUT_browser](https://github.com/reviealnow/DUT_browser) but cut down for
field deployment beside a mesh node or a DUT under close observation.

The mother server (a desktop running DUT_browser with its Fleet feature)
supervises many pi4_AP nodes. This repo only implements the **node**.

---

## 1. Product priorities (ordered)

1. **Serial Console + raw log capture** — never lose DUT log lines. This is
   the reason the node exists.
2. **External-terminal handoff** — an engineer must be able to open the same
   port in minicom / TeraTerm (higher read throughput, familiar tooling)
   without fighting the service for the device.
3. **The five monitoring pages** — Overview, CPU / Memory, Wi-Fi clients
   (detail), SSID Capability, Site Survey.
4. **Fleet node agent** — register + heartbeat to the mother server.
5. Everything else in DUT_browser is **out of scope** (no analyzer plots UI,
   no file share, no desktop packaging, no multi-DUT tabs).

## 2. Hard constraints (lightweight)

| Constraint | Target |
|---|---|
| Idle CPU (Pi 4, streaming at 115200) | < 5 % of one core |
| RSS of backend process | < 150 MB |
| Serial rates | 115200 default; sustain up to 921600 without drops |
| Runtime deps (Python) | `fastapi`, `uvicorn[standard]`, `pyserial` only |
| Frontend on the Pi | prebuilt static `dist/` served by FastAPI — **no Node.js on the Pi** |
| Charts | inline SVG, hand-rendered (same rule as DUT_browser) — no chart libraries, no CDN |
| Boot | single systemd service, headless, survives power loss |
| Disk | raw logs rotated (size-based, default 200 MB total cap) |

Single process, single port: **`:8080`** (decision D1 resolved 2026-08-11 —
DUT_browser mother server owns `:8000`, DAVE owns `:8765`; `:8080` clashes
with neither).
LAN-only, engineer profile, no login (same posture as DUT_browser).

## 3. Architecture

```
DUT ── USB/UART ──> SerialWorker (thread)
                      ├── raw log writer  logs/dut-YYYYmmdd-HHMMSS.log  (always on)
                      ├── ring buffer (last N lines, N=5000)
                      └── SysMonParser ──> state store (snapshot / cpu / wifi / survey)
                                             │
        FastAPI :PORT ── REST /api/* ────────┤   (controls, log download, fleet)
                     └── WS /ws ─────────────┘   (console_line_batch, snapshot_delta,
                                                  wifi_clients_update, survey_update)
        FleetAgent (async task) ──> POST heartbeat to mother server
        static dist/ served at /
```

Port from DUT_browser wholesale where possible: `SerialWorker`,
`SysMonParser`, `WebSocketManager`, the `useDutMonitor` hook and the console
UI. Cutting code, not rewriting, is the default.

### 3.1 Serial console (P0)

- Always-on **raw log**: every byte read is appended to the current session
  log before any parsing. Parser crashes must not stop logging.
- WebSocket streams `console_line_batch` (batched ≥ 50 ms windows) to the UI;
  ring buffer replays the last N lines to a newly connected client.
- **Port handoff — two mechanisms:**
  - **(MUST) Release / Reacquire.** UI button releases the tty (worker stops,
    fd closed) so a local/SSH minicom session can own it; Reacquire resumes
    logging. State clearly shown in the UI ("Port released to external
    terminal — logging paused").
  - **(SHOULD) TCP raw bridge** (`ser2net`-style, default off, config-gated):
    expose the port on TCP `:3333` so TeraTerm/PuTTY on the engineer's PC
    connects over LAN **while the node keeps logging** (tee). If enabled,
    bridge writes and local writes are serialized through the worker.
- Console UI: pause/follow, search, timestamp toggle, download current log,
  list/download rotated logs, baud/port picker, send-line input.

### 3.2 Pages (P1)

All data comes from `SysMonParser` output over the single WS (same event
contract as DUT_browser; extend, don't fork, the event names).

- **Overview** — connection pill (Streaming / No DUT / Offline), DUT identity
  (model/FW from parser), uptime, KPI row (CPU %, mem %, client count, log
  size), last-10 console lines teaser.
- **CPU / Memory** — live line charts (inline SVG), window selector
  (5 min / 30 min / 2 h), per-core if the DUT reports it.
- **Wi-Fi clients (detail)** — table: MAC, hostname (if reported), band/BSS,
  RSSI, PHY rate TX/RX, airtime if available; per-client RSSI sparkline;
  sort + filter.
- **SSID Capability** — per-BSS/SSID: band, channel/bandwidth, security,
  PHY mode (11ax/be), MLO status if reported. Rendered from parser events;
  read-only.
- **Site Survey** — triggered scan (button → REST → serial command to DUT),
  results table (SSID, BSSID, channel, RSSI, security), channel-occupancy
  bar (inline SVG). Cache last result with timestamp.

If the DUT command set for SSID Capability / Site Survey differs per firmware,
the serial command strings live in one config file (`config/dut_commands.yaml`
— **decision D2**), not in code.

### 3.3 Fleet node agent (P2)

Config file `deploy/node.yaml`:

```yaml
node_id: pi4-lab-07          # unique, human-assigned
fleet_server: http://192.168.1.10:8000   # mother server (DUT_browser)
heartbeat_interval_s: 30
labels: {site: "meshroom-A", dut: "AX3000-node2"}
```

- `POST {fleet_server}/api/fleet/heartbeat` every interval:
  `{node_id, labels, url: "http://<node-ip>:<port>", serial: {connected,
  port, baud, released}, dut: {model, fw, uptime_s}, kpis: {cpu, mem,
  clients}, log: {bytes, files}}`.
- Fire-and-forget with backoff; the node is fully functional with no fleet
  server configured. The mother-server side of the contract is **out of
  scope here** but this payload is the proposed contract — keep it stable.

### 3.4 Deployment

- `deploy/install.sh`: idempotent; installs Python deps into a venv, copies
  `dist/`, installs `pi4ap.service` (systemd), enables on boot.
- Log rotation implemented in-process (size-based), not logrotate, to keep
  install simple.
- Frontend is built on a dev machine (`npm run build`) and the `dist/` output
  is committed to the repo (**decision D3**) so the Pi never needs Node.

## 4. Non-goals

Auth/roles, HTTPS, desktop packaging (Tauri/Electron), offline analyzer UI,
file sharing, multi-DUT per node (one node = one serial port = one DUT),
database (state is in-memory + raw logs on disk).

## 5. Milestones

| # | Deliverable | Acceptance |
|---|---|---|
| M1 | Scaffold + SerialWorker + raw logging + WS + minimal console page | 30-min soak at 115200 with zero lost lines vs `cat` reference capture |
| M2 | Console UX complete + Release/Reacquire + log rotation/download; TCP bridge if D-decision says yes | minicom handoff round-trip works; rotation caps disk |
| M3 | Parser port + Overview + CPU/Memory pages | live KPIs and charts against a real DUT log replay |
| M4 | Wi-Fi clients detail + SSID Capability + Site Survey | pages populate from replayed + live DUT output |
| M5 | Fleet agent + systemd + install.sh + perf validation | fresh Pi OS Lite → running node in ≤ 10 min; perf table in §2 met |

Each milestone is one PR authored by one model and reviewed by the other —
see `REVIEW_WORKFLOW.md`.

## 6. Testing

- Unit: parser fixtures from real DUT logs (`tests/fixtures/*.log` — reuse
  DUT_browser's replay logs).
- Soak: scripted replay at high baud comparing captured log to source.
- Perf: `scripts/perfcheck.sh` samples CPU/RSS during a replay soak and
  fails if §2 targets are exceeded (run on the Pi, required before M5 close).

## 7. Repo conventions

- Python 3.11, `ruff` + `pytest`; TypeScript React 18 + Vite, `eslint`.
- Conventional Commits; feature branches; PRs into `main`; humans merge.
- Code and comments in English.

## 8. Open decisions (answer before M1)

| ID | Question | Default if unanswered |
|---|---|---|
| D1 | ~~Node port number~~ **Resolved: `:8080`** (mother server DUT_browser uses `:8000`, DAVE uses `:8765`) | — |
| D2 | DUT command strings per firmware — YAML config or Python table? | YAML |
| D3 | Commit `dist/` vs GitHub Actions release artifact | commit `dist/` (simplest for lab pull) |
| D4 | TCP serial bridge in-process vs documenting `ser2net` alongside | in-process (tee keeps logging) |
| D5 | Wi-Fi client data source: DUT serial output only, or also local `iw` scans from the Pi's own radio for Site Survey cross-check? | serial only for M4; Pi-radio survey is a later enhancement |
