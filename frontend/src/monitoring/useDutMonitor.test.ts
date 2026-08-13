import { describe, expect, it } from "vitest";

import { SnapshotPayload } from "../api/websocket";
import {
  appendConsoleLines,
  clientTotalsFromSnapshots,
  cpuBusyPct,
  memUsedPct,
  perCoreBusy,
} from "./useDutMonitor";

function snapshot(overrides: Partial<SnapshotPayload> = {}): SnapshotPayload {
  return {
    test_count: 1,
    device_ts: "2026-06-09 03:45:34",
    cpu: {},
    memory: {},
    wifi_clients: {},
    ...overrides,
  };
}

const core = (idle: number) => ({ usr: 0, sys: 0, nic: 0, idle, io: 0, irq: 0, sirq: 0 });

describe("cpuBusyPct", () => {
  it("is 100 - mean(idle) across cores", () => {
    // The real capture's first snapshot: 86.4 / 98.0 / 95.0 / 100.0 idle.
    const busy = cpuBusyPct(snapshot({ cpu: { "0": core(86.4), "1": core(98), "2": core(95), "3": core(100) } }));
    expect(busy).toBe(5.2);
  });

  it("is null before any CPU table has streamed", () => {
    expect(cpuBusyPct(snapshot())).toBeNull();
    expect(cpuBusyPct(null)).toBeNull();
  });

  it("never reports a negative busy percentage", () => {
    expect(cpuBusyPct(snapshot({ cpu: { "0": core(150) } }))).toBe(0);
  });
});

describe("memUsedPct", () => {
  it("is (total - available) / total, matching the real capture", () => {
    expect(memUsedPct(snapshot({ memory: { MemTotal: 843132, MemAvailable: 475472 } }))).toBe(43.6);
  });

  it("is null unless both keys have streamed", () => {
    expect(memUsedPct(snapshot({ memory: { MemTotal: 843132 } }))).toBeNull();
    expect(memUsedPct(snapshot({ memory: {} }))).toBeNull();
    expect(memUsedPct(null)).toBeNull();
  });

  it("is null rather than Infinity when the DUT reports a zero total", () => {
    expect(memUsedPct(snapshot({ memory: { MemTotal: 0, MemAvailable: 0 } }))).toBeNull();
  });
});

describe("perCoreBusy", () => {
  it("returns one busy value per reported core", () => {
    expect(perCoreBusy(snapshot({ cpu: { "0": core(86.4), "1": core(98) } }))).toEqual({
      "0": 13.6,
      "1": 2,
    });
  });
});

describe("clientTotalsFromSnapshots", () => {
  it("reports nothing when no snapshot carried a client block", () => {
    expect(clientTotalsFromSnapshots([snapshot()])).toEqual({ totals: {}, seen: false });
  });

  it("seeds per-radio counts a reloaded page would otherwise lose", () => {
    const { totals, seen } = clientTotalsFromSnapshots([
      snapshot({ wifi_clients: { "5G": { total_size: 2, clients: [] } } }),
    ]);
    expect(seen).toBe(true);
    expect(totals).toEqual({ "5G": 2 });
  });

  it("lets the newest snapshot win per radio", () => {
    const { totals } = clientTotalsFromSnapshots([
      snapshot({ test_count: 1, wifi_clients: { "5G": { total_size: 2, clients: [] } } }),
      snapshot({ test_count: 2, wifi_clients: { "5G": { total_size: 5, clients: [] } } }),
    ]);
    expect(totals).toEqual({ "5G": 5 });
  });

  it("keeps radios that only appeared in earlier snapshots", () => {
    const { totals } = clientTotalsFromSnapshots([
      snapshot({ test_count: 1, wifi_clients: { "2G": { total_size: 1, clients: [] } } }),
      snapshot({ test_count: 2, wifi_clients: { "5G": { total_size: 3, clients: [] } } }),
    ]);
    expect(totals).toEqual({ "2G": 1, "5G": 3 });
  });

  it("counts zero associated clients as a real report, not as absent", () => {
    const { totals, seen } = clientTotalsFromSnapshots([
      snapshot({ wifi_clients: { "5G": { total_size: 0, clients: [] } } }),
    ]);
    expect(seen).toBe(true);
    expect(totals).toEqual({ "5G": 0 });
  });
});

describe("appendConsoleLines", () => {
  it("pairs every line with the moment it arrived", () => {
    const lines = appendConsoleLines([], ["a", "b"], 1000);
    expect(lines).toEqual([
      { text: "a", timestamp: 1000 },
      { text: "b", timestamp: 1000 },
    ]);
  });

  it("keeps text and timestamp together once the buffer rotates", () => {
    // The M2 review finding: a parallel timestamp array desynchronised here.
    let lines = appendConsoleLines([], Array.from({ length: 5000 }, (_, i) => `old-${i}`), 1000);
    lines = appendConsoleLines(lines, ["fresh"], 2000);

    expect(lines).toHaveLength(5000);
    const newest = lines[lines.length - 1];
    expect(newest).toEqual({ text: "fresh", timestamp: 2000 });
    // The oldest line was evicted with its own timestamp, not left behind.
    expect(lines[0]).toEqual({ text: "old-1", timestamp: 1000 });
  });
});
