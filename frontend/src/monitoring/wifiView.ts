import type { SurveyResult } from "../api/rest";
import type { SnapshotPayload, WifiClient } from "../api/websocket";

export type OccupancyBar = { channel: string; count: number; x: number; y: number; width: number; height: number };

export function rssiHistoryByMac(history: SnapshotPayload[]): Record<string, number[]> {
  const index: Record<string, number[]> = {};
  for (const snapshot of history) {
    for (const payload of Object.values(snapshot.wifi_clients ?? {})) {
      for (const client of payload.clients) {
        if (!client.mac || typeof client.rssi !== "number") continue;
        index[client.mac] = [...(index[client.mac] ?? []), client.rssi].slice(-20);
      }
    }
  }
  return index;
}

export function filterAndSortClients(clients: WifiClient[], filter: string, sort: string): WifiClient[] {
  const needle = filter.toLowerCase();
  return clients.filter((row) => JSON.stringify(row).toLowerCase().includes(needle)).sort((a, b) => {
    const left = a[sort]; const right = b[sort];
    if (typeof left === "number" && typeof right === "number") return left - right;
    return String(left ?? "").localeCompare(String(right ?? ""), undefined, { numeric: true });
  });
}

export function occupancyBars(results: SurveyResult[], width = 600, baseline = 100): OccupancyBar[] {
  const counts = results.reduce<Record<string, number>>((out, result) => {
    if (result.channel !== null) out[result.channel] = (out[result.channel] ?? 0) + 1;
    return out;
  }, {});
  const entries = Object.entries(counts).sort(([a], [b]) => Number(a) - Number(b));
  const maxCount = Math.max(1, ...entries.map(([, count]) => count));
  const slot = width / Math.max(1, entries.length);
  const barWidth = Math.min(28, slot * 0.65);
  return entries.map(([channel, count], index) => {
    const height = (count / maxCount) * 82;
    return { channel, count, x: index * slot + (slot - barWidth) / 2, y: baseline - height, width: barWidth, height };
  });
}
