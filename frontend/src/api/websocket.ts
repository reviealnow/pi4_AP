/**
 * Self-reconnecting node WebSocket.
 *
 * Ported from DUT_browser's `src/api/websocket.ts`. M1 cut this down to
 * `console_line_batch` because there was no parser; M3 restores the snapshot
 * members and `applySnapshotDelta` verbatim from the original, so the two repos
 * still speak one event contract.
 *
 * Extended (not in DUT_browser): `dut_identity`. See the M3 PR — the DUT emits a
 * JSON status blob that DUT_browser ignores, and SPEC §3.2 needs it for
 * Overview.
 */

export type CpuCore = {
  usr: number;
  sys: number;
  nic: number;
  idle: number;
  io: number;
  irq: number;
  sirq: number;
};

export type WifiClient = {
  mac?: string;
  ip?: string;
  rssi?: number;
  snr?: number;
  [key: string]: unknown;
};

/** Selected /proc/meminfo keys (kB), streamed live inside each snapshot. */
export type MemoryInfo = Record<string, number>;

export type SnapshotPayload = {
  test_count: number;
  device_ts: string;
  cpu: Record<string, CpuCore>;
  memory?: MemoryInfo;
  wifi_clients?: Record<string, { total_size: number; clients: WifiClient[] }>;
};

export type SnapshotDelta = {
  test_count?: number;
  device_ts?: string;
  cpu?: Record<string, CpuCore>;
  cpu_removed?: string[];
  memory?: MemoryInfo;
  wifi_clients?: Record<string, { total_size: number; clients: WifiClient[] }>;
  wifi_clients_removed?: string[];
};

export type DutIdentity = {
  model: string | null;
  product: string | null;
  firmware: string | null;
  mac: string | null;
  serial: string | null;
  ip: string | null;
  uptime: string | null;
  uptime_s: number | null;
  device_ts: string | null;
  cpu_load: number | null;
  mem_load: number | null;
  connected_clients: number | null;
};

export type NodeEvent =
  | { type: "console_line_batch"; lines: string[] }
  | { type: "snapshot_update"; snapshot: SnapshotPayload }
  | { type: "snapshot_delta"; delta: SnapshotDelta }
  | { type: "dut_identity"; identity: DutIdentity }
  | {
      type: "wifi_clients_update";
      radio: "2G" | "5G" | "6G";
      total_size: number;
      clients: WifiClient[];
    };
// Note: like DUT_browser's, this is a closed discriminated union. Unknown
// runtime event types are parsed as NodeEvent and fall through the type checks
// (ignored). A permissive `{ type: string }` member is intentionally omitted
// because it poisons discriminant narrowing on the members above.

export type NodeSocket = { close: () => void };

export type NodeSocketHandlers = {
  onEvent: (event: NodeEvent) => void;
  /** Fired on every (re)connect once the socket is open. */
  onOpen?: () => void;
  /** Fired on every drop/close. */
  onClose?: () => void;
};

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 10_000;

export function connectNodeWebSocket(handlers: NodeSocketHandlers): NodeSocket {
  const { onEvent, onOpen, onClose } = handlers;
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${protocol}://${window.location.host}/ws`;

  let ws: WebSocket | null = null;
  let closedByCaller = false;
  let reconnectTimer: number | null = null;
  let attempt = 0;

  const scheduleReconnect = () => {
    if (closedByCaller) {
      return;
    }
    const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt);
    const jitter = Math.random() * 0.3 * delay;
    attempt += 1;
    reconnectTimer = window.setTimeout(open, delay + jitter);
  };

  function open() {
    // The delta base is per-connection: the backend replays a fresh
    // snapshot_update after every (re)connect, so a stale base is never reused.
    let latestSnapshot: SnapshotPayload | null = null;
    const socket = new WebSocket(url);
    ws = socket;

    socket.onopen = () => {
      attempt = 0;
      onOpen?.();
    };

    socket.onmessage = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as NodeEvent;
        if (!event || typeof event !== "object" || !("type" in event)) {
          return;
        }
        if (event.type === "snapshot_update") {
          latestSnapshot = event.snapshot;
          onEvent(event);
          return;
        }
        if (event.type === "snapshot_delta") {
          // A delta before any baseline cannot be applied; drop it rather than
          // invent a snapshot. The next Test Time emits a full update.
          if (!latestSnapshot) {
            return;
          }
          latestSnapshot = applySnapshotDelta(latestSnapshot, event.delta);
          onEvent({ type: "snapshot_update", snapshot: latestSnapshot });
          return;
        }
        onEvent(event);
      } catch {
        // Ignore malformed messages.
      }
    };

    socket.onclose = () => {
      onClose?.();
      scheduleReconnect();
    };

    socket.onerror = () => {
      socket.close(); // triggers onclose -> reconnect
    };
  }

  open();

  return {
    close: () => {
      closedByCaller = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      ws?.close();
    },
  };
}

/**
 * Fold a `snapshot_delta` onto a baseline snapshot.
 *
 * Ported verbatim from DUT_browser. Behaviourally identical to the backend's
 * `apply_snapshot_delta` and to the reducer asserted in
 * `tests/test_sysmon_parser.py` — three implementations of one contract, so a
 * drift shows up as a backend test failure.
 */
export function applySnapshotDelta(base: SnapshotPayload, delta: SnapshotDelta): SnapshotPayload {
  const nextCpu = { ...base.cpu };
  if (delta.cpu_removed) {
    for (const coreId of delta.cpu_removed) {
      delete nextCpu[coreId];
    }
  }
  if (delta.cpu) {
    Object.assign(nextCpu, delta.cpu);
  }

  const nextMemory = { ...(base.memory ?? {}) };
  if (delta.memory) {
    Object.assign(nextMemory, delta.memory);
  }

  const nextWifi = { ...(base.wifi_clients ?? {}) };
  if (delta.wifi_clients_removed) {
    for (const radio of delta.wifi_clients_removed) {
      delete nextWifi[radio];
    }
  }
  if (delta.wifi_clients) {
    Object.assign(nextWifi, delta.wifi_clients);
  }

  return {
    test_count: delta.test_count ?? base.test_count,
    device_ts: delta.device_ts ?? base.device_ts,
    cpu: nextCpu,
    memory: nextMemory,
    wifi_clients: nextWifi,
  };
}
