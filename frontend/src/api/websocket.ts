/**
 * Self-reconnecting console WebSocket.
 *
 * Ported from DUT_browser's `src/api/websocket.ts`. Kept: the exponential
 * backoff reconnect (1s -> cap 10s + jitter) and the onOpen/onClose hooks. Cut:
 * the snapshot/delta and wifi event members and `applySnapshotDelta` — M1 has
 * no parser, so `console_line_batch` is the only event on the wire.
 *
 * The event names and shapes are unchanged from DUT_browser, so the two repos
 * stay contract-compatible as the later milestones add events back.
 */

export type ConsoleLineBatch = { type: "console_line_batch"; lines: string[] };

export type NodeEvent = ConsoleLineBatch;
// Note: like DUT_browser's, this is a closed discriminated union. Unknown
// runtime event types are parsed as NodeEvent and fall through the type checks
// (ignored). A permissive `{ type: string }` member is intentionally omitted
// because it poisons discriminant narrowing.

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
    const socket = new WebSocket(url);
    ws = socket;

    socket.onopen = () => {
      attempt = 0;
      onOpen?.();
    };

    socket.onmessage = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as NodeEvent;
        if (event && typeof event === "object" && "type" in event) {
          onEvent(event);
        }
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
