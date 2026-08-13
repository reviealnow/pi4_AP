import { describe, expect, it } from "vitest";

import { applySnapshotDelta, SnapshotPayload } from "./websocket";

/**
 * The delta contract has three implementations: this one, the backend's
 * `apply_snapshot_delta`, and the reducer asserted in
 * `backend/tests/test_sysmon_parser.py`. These cases mirror the backend's so a
 * drift between them shows up on one side or the other.
 */

const base: SnapshotPayload = {
  test_count: 1,
  device_ts: "2026-06-09 03:45:34",
  cpu: {
    "0": { usr: 0, sys: 4.9, nic: 0, idle: 86.4, io: 0, irq: 1, sirq: 7.8 },
  },
  memory: { MemTotal: 843132, MemAvailable: 475472 },
  wifi_clients: { "5G": { total_size: 2, clients: [] } },
};

describe("applySnapshotDelta", () => {
  it("adds a newly reported core without disturbing the existing one", () => {
    const next = applySnapshotDelta(base, {
      cpu: { "1": { usr: 1, sys: 0, nic: 0, idle: 98, io: 0, irq: 0, sirq: 1 } },
    });
    expect(Object.keys(next.cpu).sort()).toEqual(["0", "1"]);
    expect(next.cpu["0"].idle).toBe(86.4);
    expect(next.cpu["1"].idle).toBe(98);
  });

  it("overwrites a core that reports again", () => {
    const next = applySnapshotDelta(base, {
      cpu: { "0": { usr: 9, sys: 1, nic: 0, idle: 50, io: 0, irq: 0, sirq: 40 } },
    });
    expect(next.cpu["0"].idle).toBe(50);
  });

  it("drops cores listed in cpu_removed", () => {
    const next = applySnapshotDelta(base, { cpu_removed: ["0"] });
    expect(next.cpu).toEqual({});
  });

  it("merges streamed meminfo keys instead of replacing the map", () => {
    const next = applySnapshotDelta(base, { memory: { SUnreclaim: 158908 } });
    expect(next.memory).toEqual({ MemTotal: 843132, MemAvailable: 475472, SUnreclaim: 158908 });
  });

  it("carries test_count and device_ts forward when the delta omits them", () => {
    const next = applySnapshotDelta(base, { memory: { MemFree: 1 } });
    expect(next.test_count).toBe(1);
    expect(next.device_ts).toBe("2026-06-09 03:45:34");
  });

  it("applies a new Test Time when the delta carries one", () => {
    const next = applySnapshotDelta(base, { test_count: 2, device_ts: "2026-06-09 03:46:48" });
    expect(next.test_count).toBe(2);
    expect(next.device_ts).toBe("2026-06-09 03:46:48");
  });

  it("updates and removes per-radio client entries", () => {
    const added = applySnapshotDelta(base, {
      wifi_clients: { "2G": { total_size: 1, clients: [] } },
    });
    expect(Object.keys(added.wifi_clients ?? {}).sort()).toEqual(["2G", "5G"]);

    const removed = applySnapshotDelta(base, { wifi_clients_removed: ["5G"] });
    expect(removed.wifi_clients).toEqual({});
  });

  it("does not mutate the baseline it was given", () => {
    const before = JSON.stringify(base);
    applySnapshotDelta(base, {
      cpu: { "1": { usr: 1, sys: 0, nic: 0, idle: 98, io: 0, irq: 0, sirq: 1 } },
      memory: { MemFree: 5 },
      wifi_clients_removed: ["5G"],
    });
    expect(JSON.stringify(base)).toBe(before);
  });
});
