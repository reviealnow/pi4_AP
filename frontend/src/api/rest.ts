/**
 * REST client for the node's control surface.
 *
 * Ported from DUT_browser's `src/api/rest.ts`: the `get`/`post` helpers and
 * `humanizeApiError`. Cut: snapshots, console tail (M1 replays the ring over
 * /ws instead), analyzer, files, bulletin and the DUT-registry calls.
 */

export type SerialPortInfo = {
  device: string;
  description: string;
  hwid: string;
};

export type SerialStatus = {
  connected: boolean;
  port: string | null;
  baudrate: number;
  opened_at: string | null;
  log_path: string | null;
  log_name: string | null;
  bytes_written: number;
  last_rx_age_s: number | null;
  last_error: string | null;
};

/**
 * Turn a backend error (thrown by `post`/`get` as `new Error(response.text())`,
 * whose message is usually a JSON body like `{"detail":"..."}`) into friendly,
 * user-facing copy. Never surfaces raw JSON.
 */
export function humanizeApiError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error ?? "");
  let detail = raw;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.detail === "string") {
      detail = parsed.detail;
    }
  } catch {
    // Not JSON — keep the raw message as the fallback detail.
  }
  if (detail.includes("Permission denied")) {
    return "Permission denied on that port. Add the service user to the `dialout` group and retry.";
  }
  if (detail.includes("could not open port") || detail.includes("No such file")) {
    return "That port could not be opened. Re-scan and check the DUT cable is attached.";
  }
  if (!detail.trim()) {
    return "Something went wrong. Please try again.";
  }
  return detail;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return (await response.json()) as T;
}

async function post<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function listSerialPorts(): Promise<SerialPortInfo[]> {
  const data = await request<{ ports: SerialPortInfo[] }>("/api/serial/ports");
  return data.ports ?? [];
}

export async function getSerialStatus(): Promise<SerialStatus> {
  return request<SerialStatus>("/api/serial/status");
}

export async function openSerial(port: string, baudrate: number): Promise<SerialStatus> {
  return post<SerialStatus>("/api/serial/open", { port, baudrate });
}

export async function closeSerial(): Promise<SerialStatus> {
  return post<SerialStatus>("/api/serial/close", {});
}

export function logDownloadUrl(): string {
  return "/api/serial/log";
}
