import { NodeStatus } from "../monitoring/useDutMonitor";

/**
 * Connection-status pill (SPEC §3.2): Streaming / No DUT / Offline.
 *
 * Uses DUT_browser's `.pill` markup and `ok` / `warn` / `danger` tones, so the
 * component reads the same as the pill on the mother server's dashboard.
 */

const TONE: Record<NodeStatus, { className: string; label: string }> = {
  streaming: { className: "ok", label: "Streaming" },
  "no-dut": { className: "warn", label: "No DUT" },
  offline: { className: "danger", label: "Offline" },
};

type Props = {
  status: NodeStatus;
  title?: string;
};

export default function StatusPill({ status, title }: Props) {
  const tone = TONE[status];
  return (
    <span className={`pill ${tone.className}`} title={title} role="status">
      <span className="dot" aria-hidden />
      {tone.label}
    </span>
  );
}
