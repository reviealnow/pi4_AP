#!/usr/bin/env bash
#
# M3 acceptance test (SPEC §5): live KPIs and charts against a real DUT log replay.
#
#   ./scripts/monitoring_test.sh
#
# Replays backend/tests/fixtures/dut-sysmon-real.log — an actual AP6 840E
# capture with device identifiers scrubbed — through a pseudo-terminal into the
# running node, then asserts that the parser produced usable monitoring state:
# snapshot history for chart backfill, DUT identity for Overview, correct KPI
# arithmetic, live WebSocket events, and an unchanged byte-exact raw log.
#
# The last check is the important one for P0: adding a parser to the pipeline
# must not cost the capture a single byte.
#
# On a real Pi with a real DUT, the same assertions hold against live output —
# open the port from the UI and watch Overview populate instead of replaying.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"

if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  PYTHON="$BACKEND_DIR/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

if ! "$PYTHON" -c "import fastapi, uvicorn, serial" >/dev/null 2>&1; then
  echo "error: runtime deps missing. Create the venv first:" >&2
  echo "  python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt" >&2
  exit 2
fi

exec "$PYTHON" "$REPO_ROOT/scripts/m3_acceptance.py" "$@"
