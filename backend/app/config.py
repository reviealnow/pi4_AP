"""Static configuration for the pi4_AP node.

Ported from DUT_browser's ``app/config.py``, cut to what M1 needs (raw session
logs + the built frontend). Everything else in that file — analyzer outputs,
workspace DB, uploads — belongs to features pi4_AP does not have.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BASE_DIR / "logs"

# Production build of the frontend (npm run build, committed per SPEC D3).
# Served by the backend at "/" only when it exists; in dev it may be absent and
# Vite serves the UI instead. The Pi never needs Node.
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

# Single process, single port (SPEC §2, decision D1 resolved: :8080).
HOST = "0.0.0.0"
PORT = 8080

DEFAULT_BAUDRATE = 115200

# Ring buffer of recent console lines replayed to a newly connected client
# (SPEC §3.1: "ring buffer replays the last N lines", N = 5000).
CONSOLE_RING_MAX = 5000
