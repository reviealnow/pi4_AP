#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_ROOT/backend/.venv/bin/python}"
exec "$PYTHON" "$REPO_ROOT/scripts/m2_acceptance.py" bridge
