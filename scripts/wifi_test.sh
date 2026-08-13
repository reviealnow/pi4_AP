#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/backend/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="${PYTHON:-python3}"
exec "$PYTHON" "$ROOT/scripts/m4_acceptance.py" "$@"
