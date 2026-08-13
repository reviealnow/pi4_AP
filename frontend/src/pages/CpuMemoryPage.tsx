import { useMemo, useState } from "react";

import LineChart, { Series } from "../components/charts/LineChart";
import { Card, EmptyState } from "../components/shell/Card";
import { cpuBusyPct, DutMonitorState, memUsedPct, perCoreBusy } from "../monitoring/useDutMonitor";
import { SnapshotPayload } from "../api/websocket";

/**
 * CPU / Memory (SPEC §3.2): live line charts as inline SVG, a window selector,
 * and per-core series when the DUT reports more than one core.
 *
 * Windows are measured on the DUT's own clock (`device_ts`), not the browser's,
 * so a replayed capture windows the same way a live DUT does.
 */

const WINDOWS = [
  { label: "5 min", minutes: 5 },
  { label: "30 min", minutes: 30 },
  { label: "2 h", minutes: 120 },
] as const;

/** Per-core palette; cores beyond it wrap. Accent first so core 0 matches the UI. */
const CORE_COLORS = ["#1565c0", "#0c7a43", "#9a6a00", "#b3261e", "#6b54c6", "#137a4d"];

type Props = {
  monitor: DutMonitorState;
};

export default function CpuMemoryPage({ monitor }: Props) {
  const [minutes, setMinutes] = useState<number>(30);
  const windowed = useMemo(() => windowSnapshots(monitor.history, minutes), [monitor.history, minutes]);

  const cpuSeries = useMemo<Series[]>(() => {
    if (windowed.length === 0) {
      return [];
    }
    const coreIds = Array.from(
      new Set(windowed.flatMap((snapshot) => Object.keys(snapshot.cpu ?? {}))),
    ).sort((a, b) => Number(a) - Number(b));

    // Per-core lines sit behind the bold mean so a single hot core is visible
    // without drowning out the aggregate.
    const perCore: Series[] = coreIds.map((coreId, index) => ({
      label: `Core ${coreId}`,
      color: CORE_COLORS[index % CORE_COLORS.length],
      faint: true,
      values: windowed.map((snapshot) => perCoreBusy(snapshot)[coreId] ?? 0),
    }));

    const mean: Series = {
      label: "Mean busy",
      color: "#1565c0",
      values: windowed.map((snapshot) => cpuBusyPct(snapshot) ?? 0),
    };

    return coreIds.length > 1 ? [...perCore, mean] : [mean];
  }, [windowed]);

  const memorySeries = useMemo<Series[]>(() => {
    const values = windowed
      .map((snapshot) => memUsedPct(snapshot))
      .filter((value): value is number => value !== null);
    return values.length > 0 ? [{ label: "Memory used", color: "#0c7a43", values }] : [];
  }, [windowed]);

  const selector = (
    <div className="window-select" role="group" aria-label="Chart window">
      {WINDOWS.map((option) => (
        <button
          key={option.minutes}
          type="button"
          className={`btn${minutes === option.minutes ? " primary" : ""}`}
          onClick={() => setMinutes(option.minutes)}
          aria-pressed={minutes === option.minutes}
        >
          {option.label}
        </button>
      ))}
    </div>
  );

  if (monitor.history.length === 0) {
    return (
      <Card title="CPU / Memory" subtitle="Live charts from the DUT's sysmon output" actions={selector}>
        <EmptyState
          message="No snapshots yet"
          hint="Charts fill in once the DUT prints its first '= Test Time:' block."
        />
      </Card>
    );
  }

  const startLabel = windowed[0]?.device_ts ?? "";
  const endLabel = windowed[windowed.length - 1]?.device_ts ?? "";
  const subtitle = `${windowed.length} of ${monitor.history.length} snapshots in the last ${labelFor(minutes)}`;

  return (
    <>
      <Card title="CPU utilisation" subtitle={subtitle} actions={selector}>
        <LineChart
          series={cpuSeries}
          startLabel={startLabel}
          endLabel={endLabel}
          ariaLabel="CPU busy percent over time"
        />
      </Card>

      <Card title="Memory used" subtitle="MemTotal minus MemAvailable, from the streamed /proc/meminfo">
        {memorySeries.length > 0 ? (
          <LineChart
            series={memorySeries}
            startLabel={startLabel}
            endLabel={endLabel}
            ariaLabel="Memory used percent over time"
          />
        ) : (
          <EmptyState
            message="No memory samples in this window"
            hint="This DUT may not dump /proc/meminfo inside its snapshot block."
          />
        )}
      </Card>
    </>
  );
}

function labelFor(minutes: number): string {
  return WINDOWS.find((option) => option.minutes === minutes)?.label ?? `${minutes} min`;
}

/**
 * Keep the snapshots whose device_ts falls within `minutes` of the newest one.
 *
 * Anchoring on the newest sample rather than "now" means a replayed capture
 * (whose DUT clock is historical) windows exactly like a live DUT. Snapshots
 * with an unparseable timestamp are kept rather than silently dropped.
 */
export function windowSnapshots(history: SnapshotPayload[], minutes: number): SnapshotPayload[] {
  if (history.length === 0) {
    return [];
  }
  const times = history.map((snapshot) => parseDeviceTs(snapshot.device_ts));
  const newest = times.reduce<number | null>((max, time) => (time !== null && (max === null || time > max) ? time : max), null);
  if (newest === null) {
    return history;
  }
  const cutoff = newest - minutes * 60_000;
  return history.filter((snapshot, index) => {
    const time = times[index];
    return time === null || time >= cutoff;
  });
}

function parseDeviceTs(value: string): number | null {
  // "2026-06-09 03:45:34" -> local time. The T separator keeps Safari happy.
  const parsed = Date.parse(value.replace(" ", "T"));
  return Number.isNaN(parsed) ? null : parsed;
}
