import { useState } from "react";

import StatusPill from "../components/StatusPill";
import { useDutMonitor } from "../monitoring/useDutMonitor";
import ConnectionCard from "../components/ConnectionCard";
import ConsolePanel from "../components/ConsolePanel";
import CpuMemoryPage from "./CpuMemoryPage";
import OverviewPage from "./OverviewPage";

/**
 * App shell with the sidebar nav restored.
 *
 * M1 dropped DUT_browser's sidebar because a single page has nothing to
 * navigate between; M3 adds the monitoring pages, so it comes back — same
 * `.sidebar` / `.nav-item` markup as DUT_browser's `AppShell`.
 *
 * One `useDutMonitor` lives here and is passed down, so all three pages share a
 * single WebSocket and one copy of the console backlog. Mounting the hook per
 * page would open a socket per page and re-replay the ring on every nav.
 */

const PAGES = [
  { id: "overview", label: "Overview", icon: "◉" },
  { id: "cpu", label: "CPU / Memory", icon: "◧" },
  { id: "console", label: "Serial console", icon: "▤" },
] as const;

type PageId = (typeof PAGES)[number]["id"];

export default function AppShell() {
  const [page, setPage] = useState<PageId>("overview");
  const monitor = useDutMonitor();

  const active = PAGES.find((item) => item.id === page) ?? PAGES[0];

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden>
            AP
          </div>
          <div>
            <div className="brand-name">pi4_AP</div>
            <div className="brand-sub">DUT monitoring node</div>
          </div>
        </div>
        <nav className="nav" aria-label="Pages">
          {PAGES.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`nav-item${item.id === page ? " active" : ""}`}
              onClick={() => setPage(item.id)}
              aria-current={item.id === page ? "page" : undefined}
            >
              <span className="nav-ico" aria-hidden>
                {item.icon}
              </span>
              {item.label}
            </button>
          ))}
        </nav>
      </aside>

      <div className="main">
        <header className="toolbar">
          <div className="toolbar-titles">
            <div className="toolbar-title">{active.label}</div>
            <div className="toolbar-sub">
              {monitor.identity?.model
                ? `${monitor.identity.model}${monitor.identity.firmware ? ` · ${monitor.identity.firmware}` : ""}`
                : "pi4_AP node — raw DUT capture"}
            </div>
          </div>
          <div className="toolbar-spacer" />
          <StatusPill status={monitor.status} title={statusHint(monitor.status, monitor.lastEventAgeSec)} />
        </header>

        <main className="content">
          {page === "overview" ? <OverviewPage monitor={monitor} /> : null}
          {page === "cpu" ? <CpuMemoryPage monitor={monitor} /> : null}
          {page === "console" ? (
            <>
              <ConnectionCard serial={monitor.serial} onChanged={monitor.refreshSerial} />
              <ConsolePanel lines={monitor.lines} serial={monitor.serial} onClear={monitor.clearLines} />
            </>
          ) : null}
        </main>
      </div>
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
