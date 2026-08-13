import { useEffect, useMemo, useState } from "react";
import { getSurvey, humanizeApiError, runSurvey, SurveyState } from "../api/rest";
import { Card, EmptyState } from "../components/shell/Card";

export default function SiteSurveyPage() {
  const [state, setState] = useState<SurveyState>({ timestamp: null, results: [] }); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useEffect(() => { getSurvey().then(setState).catch(() => undefined); }, []);
  const scan = async () => { setBusy(true); setError(""); try { setState(await runSurvey()); } catch (e) { setError(humanizeApiError(e)); } finally { setBusy(false); } };
  const occupancy = useMemo(() => Object.entries(state.results.reduce<Record<string, number>>((a, r) => { if (r.channel != null) a[r.channel] = (a[r.channel] ?? 0) + 1; return a; }, {})), [state]);
  return <><Card title="Site survey" subtitle={state.timestamp ? `Last scan ${new Date(state.timestamp).toLocaleString()}` : "No cached scan"} actions={<button className="btn primary" disabled={busy} onClick={scan}>{busy ? "Scanning…" : "Scan from DUT"}</button>}>
    {error ? <p className="error-text">{error}</p> : null}{state.results.length === 0 ? <EmptyState message="No survey results" hint="The scan is sent over the DUT serial connection." /> : <div className="table-scroll"><table className="wifitable"><thead><tr><th>SSID</th><th>BSSID</th><th>Channel</th><th>RSSI</th><th>Security</th></tr></thead><tbody>{state.results.map((r, i) => <tr key={`${r.bssid}-${i}`}><td>{r.ssid || "Hidden"}</td><td className="mono">{r.bssid}</td><td>{r.channel ?? "—"}</td><td>{r.rssi ?? "—"} dBm</td><td>{r.security}</td></tr>)}</tbody></table></div>}
  </Card><Card title="Channel occupancy" subtitle="Networks observed per channel"><svg className="occupancy" viewBox="0 0 600 120" aria-label="Channel occupancy bars">{occupancy.map(([ch, count], i) => <g key={ch} transform={`translate(${i * 45 + 15},0)`}><rect x="0" y={100-count*18} width="28" height={count*18} /><text x="14" y="116" textAnchor="middle">{ch}</text></g>)}</svg></Card></>;
}
