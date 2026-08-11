import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getSerialStatus, SerialStatus } from "../api/rest";
import { connectNodeWebSocket } from "../api/websocket";

/**
 * Single source of live truth for the node UI.
 *
 * Ported from DUT_browser's `src/monitoring/useDutMonitor.ts`. Kept: the socket
 * lifecycle, the activity-window status derivation and the line cap. Cut: CPU /
 * memory / Wi-Fi snapshot state, the crash-pattern scan and the REST backfill —
 * M1 has no parser, and the console backlog now arrives as the ring-buffer
 * replay the backend sends on connect, so there is nothing to backfill over
 * REST.
 */

/** Matches the backend ring buffer (SPEC §3.1), so a replay is never truncated. */
const MAX_LINES = 5000;
const STREAM_ACTIVE_WINDOW_MS = 10_000;
const STATUS_TICK_MS = 2_000;

/** Connection-status pill states (SPEC §3.2). */
export type NodeStatus = "streaming" | "no-dut" | "offline";

export type DutMonitorState = {
  /** Streaming / No DUT / Offline. */
  status: NodeStatus;
  /** Raw console stream, capped at the ring-buffer size. */
  lines: string[];
  /** Latest serial-port status from the backend; null until the first poll. */
  serial: SerialStatus | null;
  /** Whole seconds since the last console event; null before any event. */
  lastEventAgeSec: number | null;
  /** Force an immediate status re-read (after connect/disconnect). */
  refreshSerial: () => void;
  /** Drop the on-screen backlog without touching the raw log. */
  clearLines: () => void;
};

export function useDutMonitor(): DutMonitorState {
  const [lines, setLines] = useState<string[]>([]);
  const [serial, setSerial] = useState<SerialStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [nowTick, setNowTick] = useState(() => Date.now());

  const lastActivityRef = useRef(0);

  const refreshSerial = useCallback(() => {
    getSerialStatus()
      .then(setSerial)
      .catch(() => setSerial(null));
  }, []);

  const clearLines = useCallback(() => setLines([]), []);

  useEffect(() => {
    const socket = connectNodeWebSocket({
      onEvent: (event) => {
        if (event.type === "console_line_batch" && Array.isArray(event.lines)) {
          if (event.lines.length === 0) {
            return;
          }
          setLines((prev) => [...prev, ...event.lines].slice(-MAX_LINES));
          lastActivityRef.current = Date.now();
        }
      },
      // The backend replays the ring buffer on every (re)connect, so drop the
      // local backlog first: the replay reseeds it and nothing is duplicated.
      onOpen: () => {
        setLines([]);
        setConnected(true);
        refreshSerial();
      },
      onClose: () => setConnected(false),
    });

    const interval = window.setInterval(() => {
      setNowTick(Date.now());
      refreshSerial();
    }, STATUS_TICK_MS);

    return () => {
      window.clearInterval(interval);
      socket.close();
    };
  }, [refreshSerial]);

  const status: NodeStatus = useMemo(() => {
    if (!connected) {
      return "offline";
    }
    if (!serial?.connected) {
      return "no-dut";
    }
    return nowTick - lastActivityRef.current < STREAM_ACTIVE_WINDOW_MS ? "streaming" : "no-dut";
  }, [connected, serial, nowTick]);

  const lastEventAgeSec = useMemo(() => {
    if (lastActivityRef.current === 0) {
      return null;
    }
    return Math.max(0, Math.floor((nowTick - lastActivityRef.current) / 1000));
  }, [nowTick]);

  return { status, lines, serial, lastEventAgeSec, refreshSerial, clearLines };
}
