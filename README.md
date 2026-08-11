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

Planning stage. Implementation is done by two LLM coding agents
(Claude Opus 5 and GPT-5.6) under a cross-review workflow — see
[docs/SPEC.md](docs/SPEC.md) and
[docs/REVIEW_WORKFLOW.md](docs/REVIEW_WORKFLOW.md).

## Layout (planned)

```
backend/    FastAPI app, serial worker, parsers, fleet agent
frontend/   React/Vite source (built on a dev machine, dist/ shipped to the Pi)
deploy/     systemd unit, install.sh, config examples
docs/       SPEC.md, REVIEW_WORKFLOW.md
```

## License

Apache-2.0 — same as DUT_browser, so code can move freely between the two
repositories.
