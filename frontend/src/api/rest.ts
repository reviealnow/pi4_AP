/**
 * REST client for the node's control surface.
 *
 * Ported from DUT_browser's `src/api/rest.ts`: the `get`/`post` helpers and
 * `humanizeApiError`. Cut: console tail (the ring replays over /ws instead),
 * analyzer, files, bulletin and the DUT-registry calls. M3 restores the
 * snapshot backfill call, since charts must populate on load rather than wait
 * ~70 s for the DUT's next Test Time.
 */

import type { DutIdentity, SnapshotPayload } from "./websocket";
import type { SsidCapability, WifiClient } from "./websocket";

export type SurveyResult = { ssid: string | null; bssid: string; channel: number | null; rssi: number | null; security: string };
export type SurveyState = { timestamp: string | null; results: SurveyResult[] };
export type WifiClientScan = { timestamp: string | null; clients: WifiClient[] };

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
  released: boolean;
  log_segment_bytes: number;
  log_total_bytes: number;
  bridge: {
    enabled: boolean;
    host: string;
    port: number;
    clients: number;
    last_error: string | null;
    dropped_output_chunks: number;
  };
};

export type LogInfo = { name: string; size: number; mtime_ns: number };

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

export async function releaseSerial(): Promise<SerialStatus> {
  return post<SerialStatus>("/api/serial/release", {});
}

export async function reacquireSerial(): Promise<SerialStatus> {
  return post<SerialStatus>("/api/serial/reacquire", {});
}

export async function sendSerial(text: string): Promise<void> {
  await post<{ ok: boolean }>("/api/serial/send", { text });
}

export async function listLogs(): Promise<LogInfo[]> {
  const payload = await request<{ logs: LogInfo[] }>("/api/serial/logs");
  return payload.logs;
}

export function logDownloadUrl(): string {
  return "/api/serial/log";
}

export function namedLogDownloadUrl(name: string): string {
  return `/api/serial/logs/${encodeURIComponent(name)}`;
}

/** Accumulated snapshot history for chart backfill (SPEC §3.2). */
export async function getSnapshots(limit = 240): Promise<SnapshotPayload[]> {
  const payload = await request<{ snapshots: SnapshotPayload[] }>(`/api/snapshots?limit=${limit}`);
  return payload.snapshots ?? [];
}

/** Latest parsed DUT identity + snapshot, for an Overview that loads late. */
export async function getDut(): Promise<{
  identity: DutIdentity | null;
  snapshot: SnapshotPayload | null;
}> {
  return request<{ identity: DutIdentity | null; snapshot: SnapshotPayload | null }>("/api/dut");
}

export async function getWifiClients(): Promise<WifiClientScan> {
  return request<WifiClientScan>("/api/wifi/clients");
}
export async function refreshWifiClients(): Promise<WifiClientScan> {
  return post<WifiClientScan>("/api/wifi/clients/refresh", {});
}
export async function getCapabilities(): Promise<SsidCapability[]> {
  return (await request<{ capabilities: SsidCapability[] }>("/api/wifi/capabilities")).capabilities;
}
export async function refreshCapabilities(): Promise<SsidCapability[]> {
  return (await post<{ capabilities: SsidCapability[] }>("/api/wifi/capabilities/refresh", {})).capabilities;
}
export async function getSurvey(): Promise<SurveyState> { return request<SurveyState>("/api/wifi/survey"); }
export async function runSurvey(): Promise<SurveyState> { return post<SurveyState>("/api/wifi/survey", {}); }
