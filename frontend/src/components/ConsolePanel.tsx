import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  humanizeApiError,
  listLogs,
  LogInfo,
  logDownloadUrl,
  namedLogDownloadUrl,
  sendSerial,
  SerialStatus,
} from "../api/rest";
import { formatBytes } from "../format";
import { ConsoleLine } from "../monitoring/useDutMonitor";
import { Card } from "./shell/Card";

/**
 * Live console tail with follow/pause and a raw-log download.
 *
 * Ported from DUT_browser's `src/components/ConsolePanel.tsx`: the scroll
 * container, stick-to-bottom logic, download and send-line form. M2 adds the
 * lightweight search/timestamp/log-list controls without component libraries.
 */

type Props = {
  lines: ConsoleLine[];
  serial: SerialStatus | null;
  onClear: () => void;
};

export default function ConsolePanel({ lines, serial, onClear }: Props) {
  const consoleRef = useRef<HTMLPreElement | null>(null);
  const [follow, setFollow] = useState(true);
  const [search, setSearch] = useState("");
  const [timestamps, setTimestamps] = useState(false);
  const [command, setCommand] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogInfo[]>([]);
  const visibleLines = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return lines
      .map(({ text: line, timestamp }) => ({ line, time: new Date(timestamp).toLocaleTimeString() }))
      .filter(({ line }) => !needle || line.toLowerCase().includes(needle));
  }, [lines, search]);
  const text = useMemo(
    () => visibleLines.map(({ line, time }) => (timestamps ? `[${time}] ${line}` : line)).join("\n"),
    [visibleLines, timestamps],
  );
  const logName = serial?.log_name ?? null;

  useEffect(() => {
    if (!follow || !consoleRef.current) {
      return;
    }
    consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
  }, [text, follow]);

  async function refreshLogs() {
    try {
      setLogs(await listLogs());
    } catch {
      setLogs([]);
    }
  }

  useEffect(() => {
    void refreshLogs();
  }, [logName]);

  async function handleSend(event: FormEvent) {
    event.preventDefault();
    if (!command.trim()) return;
    setSendError(null);
    try {
      await sendSerial(command);
      setCommand("");
    } catch (cause) {
      setSendError(humanizeApiError(cause));
    }
  }

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
      subtitle={
        serial?.released
          ? "Port released to external terminal — logging paused"
          : logName
            ? `Streaming into ${logName}`
            : "No session log yet — connect a port to start capturing."
      }
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
            Download current log
          </a>
          <button type="button" className="btn" onClick={() => setTimestamps((value) => !value)}>
            {timestamps ? "Hide timestamps" : "Show timestamps"}
          </button>
        </>
      }
    >
      <pre className="console" ref={consoleRef} onScroll={handleScroll} tabIndex={0}>
        {visibleLines.length > 0 ? (
          text
        ) : (
          <span className="console-empty">
            Waiting for DUT output. Everything received is written to the raw log first, whether or not this
            view is open.
          </span>
        )}
      </pre>
      <div className="console-tools">
        <input
          className="console-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search console"
          aria-label="Search console"
        />
        <span>{visibleLines.length} matching</span>
      </div>
      <form className="console-send" onSubmit={handleSend}>
        <input
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          placeholder="Send command to DUT"
          aria-label="Command"
          disabled={!serial?.connected}
        />
        <button className="btn primary" type="submit" disabled={!serial?.connected || !command.trim()}>
          Send
        </button>
      </form>
      {sendError ? <div className="conn-error">{sendError}</div> : null}
      <div className="console-foot">
        <span>
          {lines.length} line{lines.length === 1 ? "" : "s"} buffered
        </span>
        {follow ? <span>Following</span> : <span className="console-paused">Paused — scroll to bottom to resume</span>}
        {serial?.connected ? <span>{formatBytes(serial.bytes_written)} in this session</span> : null}
      </div>
      <div className="log-list-head">
        <strong>Raw logs</strong>
        <button type="button" className="btn" onClick={() => void refreshLogs()}>
          Refresh logs
        </button>
      </div>
      <div className="log-list">
        {logs.length ? (
          logs.map((log) => (
            <a key={log.name} href={namedLogDownloadUrl(log.name)} download={log.name}>
              <span>{log.name}</span>
              <span>{formatBytes(log.size)}</span>
            </a>
          ))
        ) : (
          <span className="console-empty">No raw logs yet.</span>
        )}
      </div>
    </Card>
  );
}
