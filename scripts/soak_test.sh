#!/usr/bin/env bash
#
# M1 acceptance test (SPEC §5): 30-minute soak at 115200 with zero lost lines.
#
#   ./scripts/soak_test.sh                 # the real thing: 30 min at 115200
#   ./scripts/soak_test.sh --duration 60   # 1-minute smoke run of the harness
#   ./scripts/soak_test.sh --baud 921600   # SPEC §2 upper bound
#
# With no DUT attached, a pseudo-terminal pair stands in for the wire. If socat
# is installed the pair comes from
#
#     socat -d -d pty,raw,echo=0 pty,raw,echo=0
#
# exactly as the SPEC describes; otherwise the harness falls back to the
# equivalent stdlib `pty.openpty()` so this runs on a bare Raspberry Pi OS Lite
# image with nothing extra installed. Pass --no-socat to force the fallback.
#
# A replay script writes a known fixture into one end at a sustained
# 115200-equivalent rate (11520 B/s) while the real node — uvicorn, REST, the
# console batcher and a live /ws subscriber — reads the other end. Afterwards
# the raw log is downloaded back over REST and compared to the source fixture.
# Exit status 0 means byte-identical.
#
# On a real Pi with a real DUT, run the same comparison against the DUT's own
# output instead: `cat /dev/ttyUSB0 > reference.log` on a second port, or diff
# against the DUT-side transcript.

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

exec "$PYTHON" "$REPO_ROOT/scripts/soak_replay.py" "$@"
