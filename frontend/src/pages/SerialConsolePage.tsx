import ConnectionCard from "../components/ConnectionCard";
import ConsolePanel from "../components/ConsolePanel";
import StatusPill from "../components/StatusPill";
import { useDutMonitor } from "../monitoring/useDutMonitor";

/**
 * The only page in M1. DUT_browser's `AppShell` sidebar/nav is intentionally
 * not ported: with one page there is nothing to navigate between, and the nav
 * comes back with the monitoring pages in M3/M4.
 */
export default function SerialConsolePage() {
  const { status, lines, serial, lastEventAgeSec, refreshSerial, clearLines } = useDutMonitor();

  return (
    <div className="app">
      <header className="toolbar">
        <div className="brand-mark" aria-hidden>
          AP
        </div>
        <div className="toolbar-titles">
          <div className="toolbar-title">Serial console</div>
          <div className="toolbar-sub">pi4_AP node — raw DUT capture</div>
        </div>
        <div className="toolbar-spacer" />
        <StatusPill status={status} title={statusHint(status, lastEventAgeSec)} />
      </header>

      <main className="content">
        <ConnectionCard serial={serial} onChanged={refreshSerial} />
        <ConsolePanel lines={lines} serial={serial} onClear={clearLines} />
      </main>
    </div>
  );
}

function statusHint(status: string, lastEventAgeSec: number | null): string {
  if (status === "offline") {
    return "No connection to the node — the backend is unreachable.";
  }
  if (status === "no-dut") {
    return lastEventAgeSec === null
      ? "Connected to the node, but no DUT output has arrived yet."
      : `Connected to the node; last DUT output ${lastEventAgeSec}s ago.`;
  }
  return "DUT output is arriving and being written to the raw log.";
}
