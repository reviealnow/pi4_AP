import { describe, expect, it } from "vitest";
import type { SnapshotPayload } from "../api/websocket";
import { filterAndSortClients, occupancyBars, rssiHistoryByMac } from "./wifiView";

describe("rssiHistoryByMac", () => {
  it("indexes each MAC once and caps its sparkline at 20 samples", () => {
    const history = Array.from({ length: 25 }, (_, test_count) => ({
      test_count, device_ts: String(test_count), cpu: {},
      wifi_clients: { "5G": { total_size: 1, clients: [{ mac: "aa", rssi: -70 + test_count }] } },
    })) satisfies SnapshotPayload[];
    expect(rssiHistoryByMac(history).aa).toHaveLength(20);
    expect(rssiHistoryByMac(history).aa[19]).toBe(-46);
  });
});

describe("filterAndSortClients", () => {
  it("filters all fields and sorts numeric RSSI values", () => {
    const rows = [{ mac: "bb", band: "5G", rssi: -40 }, { mac: "aa", band: "5G", rssi: -60 }];
    expect(filterAndSortClients(rows, "5g", "rssi").map((row) => row.mac)).toEqual(["aa", "bb"]);
  });
});

describe("occupancyBars", () => {
  it("keeps many channels inside the viewBox and normalises tall counts", () => {
    const results = Array.from({ length: 30 }, (_, channel) => ({
      ssid: "x", bssid: String(channel), channel, rssi: -50, security: "Open",
    }));
    results.push(...Array.from({ length: 12 }, (_, i) => ({ ...results[1], bssid: `extra-${i}` })));
    const bars = occupancyBars(results);
    expect(Math.max(...bars.map((bar) => bar.x + bar.width))).toBeLessThanOrEqual(600);
    expect(Math.min(...bars.map((bar) => bar.y))).toBeGreaterThanOrEqual(18);
  });
});
