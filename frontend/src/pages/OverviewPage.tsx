import { DutMonitorState } from "../monitoring/useDutMonitor";
import { formatBytes } from "../format";
import { Card, EmptyState, KpiCard } from "../components/shell/Card";

/**
 * Overview (SPEC §3.2): DUT identity, uptime, a KPI row and a console teaser.
 * The connection pill lives in the toolbar, shared by every page.
 *
 * Every value here is parser- or status-derived. Where the DUT has not reported
 * something yet the card shows an em dash rather than a plausible zero — a
 * fabricated KPI on a monitoring page is worse than a blank one.
 */

const TEASER_LINES = 10;

type Props = {
  monitor: DutMonitorState;
};

export default function OverviewPage({ monitor }: Props) {
  const { identity, serial, cpuBusyPct, memUsedPct, coreCount, clientTotal, lines } = monitor;
  const teaser = lines.slice(-TEASER_LINES);

  return (
    <>
      <div className="kpis">
        <KpiCard
          label="CPU busy"
          value={cpuBusyPct === null ? undefined : `${cpuBusyPct}%`}
          sub={coreCount ? `mean across ${coreCount} cores` : "awaiting first snapshot"}
        />
        <KpiCard
          label="Memory used"
          value={memUsedPct === null ? undefined : `${memUsedPct}%`}
          sub={identity?.mem_load != null ? `DUT reports ${identity.mem_load}%` : "from /proc/meminfo"}
        />
        <KpiCard
          label="Wi-Fi clients"
          value={clientTotal ?? identity?.connected_clients ?? undefined}
          // The caption has to describe the number actually shown. Saying "no
          // client report yet" while rendering the identity blob's count is a
          // contradiction the M3 review caught.
          sub={
            clientTotal !== null
              ? "associated across radios"
              : identity?.connected_clients != null
                ? "reported by the DUT"
                : "no client report yet"
          }
        />
        <KpiCard
          label="Raw log"
          value={serial?.bytes_written ? formatBytes(serial.bytes_written) : undefined}
          sub={serial?.log_name ?? "no session log yet"}
        />
      </div>

      <div className="grid">
        <Card title="DUT identity" subtitle="Parsed from the DUT's own status output">
          {identity ? (
            <dl className="stat-list">
              <Row label="Model" value={identity.model} />
              <Row label="Product" value={identity.product} />
              <Row label="Firmware" value={identity.firmware} />
              <Row label="Uptime" value={formatUptime(identity)} />
              <Row label="MAC" value={identity.mac} mono />
              <Row label="Serial" value={identity.serial} mono />
              <Row label="IP" value={identity.ip} mono />
              <Row label="DUT clock" value={identity.device_ts} mono />
            </dl>
          ) : (
            <EmptyState
              message="No DUT identity yet"
              hint="Connect a port; identity appears once the DUT prints its status block."
            />
          )}
        </Card>

        <Card title="Console" subtitle={`Last ${TEASER_LINES} lines — full stream on the Serial console page`}>
          {teaser.length > 0 ? (
            <pre className="console teaser">{teaser.map((line) => line.text).join("\n")}</pre>
          ) : (
            <EmptyState message="No console output yet" hint="Everything received is written to the raw log first." />
          )}
        </Card>
      </div>
    </>
  );
}

function Row({ label, value, mono }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return (
    <div className="stat-row">
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value || "—"}</dd>
    </div>
  );
}

/** Prefer the DUT's own wording, but show the parsed seconds when we have them. */
function formatUptime(identity: { uptime: string | null; uptime_s: number | null }): string | null {
  if (!identity.uptime) {
    return null;
  }
  if (identity.uptime_s === null) {
    return identity.uptime;
  }
  const days = Math.floor(identity.uptime_s / 86400);
  return days >= 1 ? `${identity.uptime} (${days}d)` : identity.uptime;
}
