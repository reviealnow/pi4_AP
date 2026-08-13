import { useEffect, useState } from "react";
import type { SsidCapability } from "../api/websocket";
import { getCapabilities, humanizeApiError, refreshCapabilities } from "../api/rest";
import { Card, EmptyState } from "../components/shell/Card";

export default function SsidCapabilityPage() {
  const [rows, setRows] = useState<SsidCapability[]>([]); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { getCapabilities().then(setRows).catch(() => undefined); }, []);
  const refresh = async () => { setBusy(true); setError(""); try { setRows(await refreshCapabilities()); } catch (e) { setError(humanizeApiError(e)); } finally { setBusy(false); } };
  return <Card title="SSID capability" subtitle="Read-only capability data reported by the DUT" actions={<button className="btn" disabled={busy} onClick={refresh}>{busy ? "Reading…" : "Refresh"}</button>}>
    {error ? <p className="error-text">{error}</p> : null}{rows.length === 0 ? <EmptyState message="No SSID capabilities reported" /> : <div className="table-scroll"><table className="wifitable"><thead><tr><th>SSID / BSSID</th><th>Band</th><th>Channel / width</th><th>Security</th><th>PHY mode</th><th>MLO</th></tr></thead><tbody>{rows.map((r, i) => <tr key={`${r.iface}-${i}`}><td>{r.ssid ?? "—"}<small>{r.bssid ? ` · ${r.bssid}` : ""}</small></td><td>{r.band ?? "—"}</td><td>{r.channel ?? "—"} / {r.channel_width ?? "—"}</td><td>{r.security ?? "—"}</td><td>{r.phy_mode ?? r.generation ?? "—"}</td><td>{r.mlo === true ? "Enabled" : r.mlo === false ? "Disabled" : r.mlo ?? "Not reported"}</td></tr>)}</tbody></table></div>}
  </Card>;
}
