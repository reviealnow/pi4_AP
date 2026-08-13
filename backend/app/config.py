"""Static configuration for the pi4_AP node.

Ported from DUT_browser's ``app/config.py``, cut to raw logs, the built frontend
and M2's rotation/bridge settings. Analyzer outputs, workspace DB and uploads
belong to features pi4_AP does not have.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = Path(os.getenv("PI4AP_LOG_DIR", str(BASE_DIR / "logs")))
COMMANDS_PATH = Path(os.getenv("PI4AP_COMMANDS_PATH", str(BASE_DIR / "config" / "dut_commands.yaml")))

# Production build of the frontend (npm run build, committed per SPEC D3).
# Served by the backend at "/" only when it exists; in dev it may be absent and
# Vite serves the UI instead. The Pi never needs Node.
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

# Single process, single port (SPEC §2, decision D1 resolved: :8080).
HOST = "0.0.0.0"
PORT = 8080

DEFAULT_BAUDRATE = 115200

# M2 in-process log rotation. A session may span multiple files; the oldest
# closed files are removed until the directory is back under the total cap.
LOG_SEGMENT_BYTES = int(os.getenv("PI4AP_LOG_SEGMENT_BYTES", str(50 * 1024 * 1024)))
LOG_TOTAL_BYTES = int(os.getenv("PI4AP_LOG_TOTAL_BYTES", str(200 * 1024 * 1024)))

# Decision D4: the bridge is implemented in-process but remains opt-in.
TCP_BRIDGE_ENABLED = os.getenv("PI4AP_BRIDGE_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
TCP_BRIDGE_HOST = os.getenv("PI4AP_BRIDGE_HOST", "0.0.0.0")
TCP_BRIDGE_PORT = int(os.getenv("PI4AP_BRIDGE_PORT", "3333"))

# Ring buffer of recent console lines replayed to a newly connected client
# (SPEC §3.1: "ring buffer replays the last N lines", N = 5000).
CONSOLE_RING_MAX = 5000
