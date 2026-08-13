import { useEffect, useMemo, useState } from "react";
import { Card, EmptyState } from "../components/shell/Card";
import type { WifiClient } from "../api/websocket";
import type { DutMonitorState } from "../monitoring/useDutMonitor";
import { getWifiClients, humanizeApiError, refreshWifiClients } from "../api/rest";
import { filterAndSortClients, rssiHistoryByMac } from "../monitoring/wifiView";

type Props = { monitor: DutMonitorState };
type SortKey = "mac" | "hostname" | "band" | "rssi";

export default function WifiClientsPage({ monitor }: Props) {
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<SortKey>("rssi");
  const [scanClients, setScanClients] = useState<WifiClient[] | null>(null);
  const [scannedAt, setScannedAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    getWifiClients().then((scan) => {
      setScanClients(scan.timestamp ? scan.clients : null);
      setScannedAt(scan.timestamp);
    }).catch(() => undefined);
  }, [monitor.serial?.opened_at]);
  const snapshotClients = useMemo(() => {
    const rows: WifiClient[] = [];
    for (const [band, payload] of Object.entries(monitor.snapshot?.wifi_clients ?? {})) {
      for (const raw of payload.clients) rows.push({ ...raw, band: raw.band ?? band });
    }
    return rows;
  }, [monitor.snapshot]);
  const clients = useMemo(() => {
    return filterAndSortClients(scanClients ?? snapshotClients, filter, sort);
  }, [scanClients, snapshotClients, filter, sort]);
  const rssiHistory = useMemo(() => rssiHistoryByMac(monitor.history), [monitor.history]);

  const refresh = async () => {
    setBusy(true); setError("");
    try {
      const scan = await refreshWifiClients();
      setScanClients(scan.clients); setScannedAt(scan.timestamp);
    } catch (caught) { setError(humanizeApiError(caught)); }
    finally { setBusy(false); }
  };

  return <Card title="Associated Wi-Fi clients" subtitle={scannedAt ? `Serial detail scanned ${new Date(scannedAt).toLocaleString()}` : "Parser-derived summary; scan explicitly for PHY detail"}
    actions={<><button className="btn" disabled={busy} onClick={refresh}>{busy ? "Scanning…" : "Scan client details"}</button><input aria-label="Filter clients" placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <select aria-label="Sort clients" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}><option value="rssi">RSSI</option><option value="mac">MAC</option><option value="hostname">Hostname</option><option value="band">Band</option></select></>}>
    {error ? <p className="error-text">{error}</p> : null}
    {clients.length === 0 ? <EmptyState message="No associated clients reported" hint="Waiting for a replayed or live CLIENTS block." /> :
      <div className="table-scroll"><table className="wifitable"><thead><tr><th>MAC</th><th>Hostname</th><th>Band / BSS</th><th>RSSI</th><th>RSSI history</th><th>PHY TX / RX</th><th>Airtime</th></tr></thead><tbody>
      {clients.map((c, i) => <tr key={`${c.mac}-${i}`}><td className="mono">{c.mac ?? "—"}</td><td>{c.hostname ?? c.host_name ?? "—"}</td><td>{c.band ?? "—"} / {c.bss ?? c.iface ?? "—"}</td><td>{c.rssi ?? "—"} dBm</td><td><RssiSpark values={rssiHistory[c.mac ?? ""] ?? []} /></td><td>{c.tx_rate ?? c.txrate ?? "—"} / {c.rx_rate ?? c.rxrate ?? "—"}</td><td>{c.airtime ?? "—"}</td></tr>)}</tbody></table></div>}
  </Card>;
}

function RssiSpark({ values }: { values: number[] }) {
  if (!values.length) return <span>—</span>;
  const points = values.map((v, i) => `${(i / Math.max(1, values.length - 1)) * 90 + 5},${5 + ((Math.max(-100, Math.min(-20, v)) + 20) / -80) * 25}`).join(" ");
  return <svg className="client-spark" viewBox="0 0 100 35" aria-label="RSSI sparkline"><polyline points={points} fill="none" className="spark-line" /></svg>;
}
