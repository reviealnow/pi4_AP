import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getDut, getSerialStatus, getSnapshots, SerialStatus } from "../api/rest";
import { connectNodeWebSocket, DutIdentity, SnapshotPayload } from "../api/websocket";

/**
 * Single source of live truth for the node UI.
 *
 * Ported from DUT_browser's `src/monitoring/useDutMonitor.ts`. Kept: the socket
 * lifecycle, the activity-window status derivation, the line cap, and the
 * CPU/memory history upserts. Cut: the crash-pattern scan (no crash feed in
 * pi4_AP) and the console REST backfill (the ring replays over /ws instead).
 *
 * M3 restores the snapshot state DUT_browser keeps, plus the DUT identity, and
 * backfills both over REST on every (re)connect so charts and Overview populate
 * immediately rather than waiting ~70 s for the DUT's next Test Time.
 */

/** Matches the backend ring buffer (SPEC §3.1), so a replay is never truncated. */
const MAX_LINES = 5000;
const STREAM_ACTIVE_WINDOW_MS = 10_000;
const STATUS_TICK_MS = 2_000;
/** Matches the backend SnapshotStore ring; covers well beyond the 2 h window. */
const MAX_HISTORY = 1000;

/** Connection-status pill states (SPEC §3.2). */
export type NodeStatus = "streaming" | "no-dut" | "offline";

export type ConsoleLine = {
  text: string;
  timestamp: number;
};

export type DutMonitorState = {
  /** Streaming / No DUT / Offline. */
  status: NodeStatus;
  /** Raw console stream, capped at the ring-buffer size. */
  lines: ConsoleLine[];
  /** Latest serial-port status from the backend; null until the first poll. */
  serial: SerialStatus | null;
  /** Whole seconds since the last console event; null before any event. */
  lastEventAgeSec: number | null;
  /** Force an immediate status re-read (after connect/disconnect). */
  refreshSerial: () => void;
  /** Drop the on-screen backlog without touching the raw log. */
  clearLines: () => void;

  // ---- M3 monitoring state (all of it parser-derived; nothing invented) ----
  /** Latest DUT identity; null until the DUT publishes its status blob. */
  identity: DutIdentity | null;
  /** Latest accumulated snapshot; null until the first Test Time. */
  snapshot: SnapshotPayload | null;
  /** Per-Test-Time history, oldest first, for the charts. */
  history: SnapshotPayload[];
  /** 100 - mean(idle) from the latest snapshot; null until one arrives. */
  cpuBusyPct: number | null;
  /** Used% from the latest snapshot's meminfo; null when meminfo is absent. */
  memUsedPct: number | null;
  coreCount: number;
  /** Associated clients summed across radios; null until a Wi-Fi update. */
  clientTotal: number | null;
};

/** 100 - mean(idle) across cores. Null when the snapshot has no CPU table. */
export function cpuBusyPct(snapshot: SnapshotPayload | null): number | null {
  const cores = Object.values(snapshot?.cpu ?? {});
  if (cores.length === 0) {
    return null;
  }
  const meanIdle = cores.reduce((sum, core) => sum + (core.idle ?? 0), 0) / cores.length;
  return round1(Math.max(0, 100 - meanIdle));
}

/** Used% from the streamed /proc/meminfo. Null unless both keys are present. */
export function memUsedPct(snapshot: SnapshotPayload | null): number | null {
  const memory = snapshot?.memory;
  const total = memory?.MemTotal;
  const available = memory?.MemAvailable;
  if (typeof total !== "number" || typeof available !== "number" || total <= 0) {
    return null;
  }
  return round1(((total - available) / total) * 100);
}

/** Per-core busy% for the CPU chart's series. */
export function perCoreBusy(snapshot: SnapshotPayload | null): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [coreId, core] of Object.entries(snapshot?.cpu ?? {})) {
    out[coreId] = round1(Math.max(0, 100 - (core.idle ?? 0)));
  }
  return out;
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/**
 * One entry per Test Time: replace the last entry while cores and meminfo are
 * still streaming in for the same snapshot, otherwise append. Mirrors
 * DUT_browser's `upsertCpuPoint` and the backend SnapshotStore.
 */
function upsertSnapshot(history: SnapshotPayload[], snapshot: SnapshotPayload): SnapshotPayload[] {
  const last = history[history.length - 1];
  if (last && last.test_count === snapshot.test_count) {
    return [...history.slice(0, -1), snapshot];
  }
  return [...history, snapshot].slice(-MAX_HISTORY);
}

/**
 * Per-radio client counts carried by backfilled snapshots.
 *
 * The parser folds each `--- CLIENTS Radio= ---` block into the snapshot it
 * belongs to, so REST history already holds the counts. Without reading them
 * back, a reloaded page shows no client report even though the node parsed one
 * — DUT_browser seeds wifi state from its backfill for exactly this reason and
 * the M1 cut dropped it. Later radios win, so the newest snapshot decides.
 */
export function clientTotalsFromSnapshots(snapshots: SnapshotPayload[]): {
  totals: Record<string, number>;
  seen: boolean;
} {
  const totals: Record<string, number> = {};
  let seen = false;
  for (const snapshot of snapshots) {
    for (const [radio, payload] of Object.entries(snapshot.wifi_clients ?? {})) {
      if (payload && typeof payload.total_size === "number") {
        totals[radio] = payload.total_size;
        seen = true;
      }
    }
  }
  return { totals, seen };
}

export function appendConsoleLines(
  previous: ConsoleLine[],
  incoming: string[],
  receivedAt: number,
): ConsoleLine[] {
  return [...previous, ...incoming.map((text) => ({ text, timestamp: receivedAt }))].slice(-MAX_LINES);
}

export function useDutMonitor(): DutMonitorState {
  const [lines, setLines] = useState<ConsoleLine[]>([]);
  const [serial, setSerial] = useState<SerialStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [nowTick, setNowTick] = useState(() => Date.now());
  const [identity, setIdentity] = useState<DutIdentity | null>(null);
  const [snapshot, setSnapshot] = useState<SnapshotPayload | null>(null);
  const [history, setHistory] = useState<SnapshotPayload[]>([]);
  const [clientsByRadio, setClientsByRadio] = useState<Record<string, number>>({});
  const [clientsSeen, setClientsSeen] = useState(false);

  const lastActivityRef = useRef(0);

  const refreshSerial = useCallback(() => {
    getSerialStatus()
      .then(setSerial)
      .catch(() => setSerial(null));
  }, []);

  const clearLines = useCallback(() => setLines([]), []);

  // Seed charts and Overview from server-side history so they populate on load
  // instead of waiting for the DUT's next Test Time (~70 s on real hardware).
  const runBackfill = useCallback(async () => {
    try {
      const snapshots = await getSnapshots(MAX_HISTORY);
      if (snapshots.length > 0) {
        setHistory(snapshots.slice(-MAX_HISTORY));
        setSnapshot((prev) => prev ?? snapshots[snapshots.length - 1]);
        // Restore the Wi-Fi client counts the snapshots already carry, so a
        // reloaded page reports what the node parsed instead of falling back to
        // the identity blob's figure with a "no client report yet" caption.
        const { totals, seen } = clientTotalsFromSnapshots(snapshots);
        if (seen) {
          setClientsByRadio((prev) => ({ ...totals, ...prev }));
          setClientsSeen(true);
        }
      }
    } catch {
      // Offline or endpoint unavailable: charts stay empty until live events.
    }
    try {
      const dut = await getDut();
      if (dut.identity) {
        setIdentity(dut.identity);
      }
    } catch {
      // Overview shows placeholders until the DUT publishes its status blob.
    }
  }, []);

  useEffect(() => {
    const socket = connectNodeWebSocket({
      onEvent: (event) => {
        if (event.type === "console_line_batch" && Array.isArray(event.lines)) {
          if (event.lines.length === 0) {
            return;
          }
          const receivedAt = Date.now();
          setLines((prev) => appendConsoleLines(prev, event.lines, receivedAt));
          lastActivityRef.current = receivedAt;
          return;
        }
        // snapshot_delta never reaches here: the socket layer folds deltas onto
        // its baseline and re-emits a whole snapshot_update.
        if (event.type === "snapshot_update") {
          setSnapshot(event.snapshot);
          setHistory((prev) => upsertSnapshot(prev, event.snapshot));
          lastActivityRef.current = Date.now();
          return;
        }
        if (event.type === "dut_identity") {
          setIdentity(event.identity);
          lastActivityRef.current = Date.now();
          return;
        }
        if (event.type === "wifi_clients_update") {
          setClientsByRadio((prev) => ({ ...prev, [event.radio]: event.total_size }));
          setClientsSeen(true);
          lastActivityRef.current = Date.now();
        }
      },
      // The backend replays the ring buffer on every (re)connect, so drop the
      // local backlog first: the replay reseeds it and nothing is duplicated.
      onOpen: () => {
        setLines([]);
        setConnected(true);
        refreshSerial();
        void runBackfill();
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
  }, [refreshSerial, runBackfill]);

  // A new serial session resets the node's parser and snapshot store, so drop
  // the previous DUT's charts and identity instead of letting them linger.
  const sessionKey = serial?.opened_at ?? null;
  useEffect(() => {
    setSnapshot(null);
    setHistory([]);
    setIdentity(null);
    setClientsByRadio({});
    setClientsSeen(false);
    void runBackfill();
  }, [sessionKey, runBackfill]);

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

  const clientTotal = useMemo(() => {
    if (!clientsSeen) {
      return null;
    }
    return Object.values(clientsByRadio).reduce((sum, value) => sum + value, 0);
  }, [clientsByRadio, clientsSeen]);

  return {
    status,
    lines,
    serial,
    lastEventAgeSec,
    refreshSerial,
    clearLines,
    identity,
    snapshot,
    history,
    cpuBusyPct: cpuBusyPct(snapshot),
    memUsedPct: memUsedPct(snapshot),
    coreCount: Object.keys(snapshot?.cpu ?? {}).length,
    clientTotal,
  };
}
