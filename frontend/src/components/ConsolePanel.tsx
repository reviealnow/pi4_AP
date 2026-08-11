import { useEffect, useMemo, useRef, useState } from "react";

import { logDownloadUrl, SerialStatus } from "../api/rest";
import { formatBytes } from "../format";
import { Card } from "./shell/Card";

/**
 * Live console tail with follow/pause and a raw-log download.
 *
 * Ported from DUT_browser's `src/components/ConsolePanel.tsx`: the scroll
 * container, the stick-to-bottom logic and the download action. Cut: the
 * send-line form and the CodeMirror/Vim popup editor — those are console-UX
 * (M2) and would each drag in a dependency SPEC §2 does not allow.
 */

type Props = {
  lines: string[];
  serial: SerialStatus | null;
  onClear: () => void;
};

export default function ConsolePanel({ lines, serial, onClear }: Props) {
  const consoleRef = useRef<HTMLPreElement | null>(null);
  const [follow, setFollow] = useState(true);

  const text = useMemo(() => lines.join("\n"), [lines]);
  const logName = serial?.log_name ?? null;

  useEffect(() => {
    if (!follow || !consoleRef.current) {
      return;
    }
    consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
  }, [text, follow]);

  // Scrolling away from the bottom pauses follow; scrolling back resumes it —
  // same affordance as DUT_browser's console.
  function handleScroll() {
    const element = consoleRef.current;
    if (!element) {
      return;
    }
    const atBottom = element.scrollHeight - (element.scrollTop + element.clientHeight) < 20;
    setFollow(atBottom);
  }

  return (
    <Card
      title="Serial console"
      subtitle={logName ? `Streaming into ${logName}` : "No session log yet — connect a port to start capturing."}
      actions={
        <>
          <button
            type="button"
            className={`btn ${follow ? "" : "primary"}`}
            onClick={() => setFollow((prev) => !prev)}
            aria-pressed={follow}
          >
            {follow ? "Pause" : "Follow"}
          </button>
          <button type="button" className="btn" onClick={onClear} disabled={lines.length === 0}>
            Clear view
          </button>
          <a
            className="btn"
            href={logName ? logDownloadUrl() : undefined}
            download={logName ?? undefined}
            aria-disabled={logName ? undefined : true}
            onClick={(event) => {
              if (!logName) {
                event.preventDefault();
              }
            }}
          >
            Download raw log
          </a>
        </>
      }
    >
      <pre className="console" ref={consoleRef} onScroll={handleScroll} tabIndex={0}>
        {lines.length > 0 ? (
          text
        ) : (
          <span className="console-empty">
            Waiting for DUT output. Everything received is written to the raw log first, whether or not this
            view is open.
          </span>
        )}
      </pre>
      <div className="console-foot">
        <span>
          {lines.length} line{lines.length === 1 ? "" : "s"} buffered
        </span>
        {follow ? <span>Following</span> : <span className="console-paused">Paused — scroll to bottom to resume</span>}
        {serial?.connected ? <span>{formatBytes(serial.bytes_written)} in this session</span> : null}
      </div>
    </Card>
  );
}
