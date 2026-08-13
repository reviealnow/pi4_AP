import { useEffect, useMemo, useState } from "react";
import { getSurvey, humanizeApiError, runSurvey, SurveyState } from "../api/rest";
import { Card, EmptyState } from "../components/shell/Card";
import { occupancyBars } from "../monitoring/wifiView";

export default function SiteSurveyPage() {
  const [state, setState] = useState<SurveyState>({ timestamp: null, results: [] }); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useEffect(() => { getSurvey().then(setState).catch(() => undefined); }, []);
  const scan = async () => { setBusy(true); setError(""); try { setState(await runSurvey()); } catch (e) { setError(humanizeApiError(e)); } finally { setBusy(false); } };
  const occupancy = useMemo(() => occupancyBars(state.results), [state.results]);
  return <><Card title="Site survey" subtitle={state.timestamp ? `Last scan ${new Date(state.timestamp).toLocaleString()}` : "No cached scan"} actions={<button className="btn primary" disabled={busy} onClick={scan}>{busy ? "Scanning…" : "Scan from DUT"}</button>}>
    {error ? <p className="error-text">{error}</p> : null}{state.results.length === 0 ? <EmptyState message="No survey results" hint="The scan is sent over the DUT serial connection." /> : <div className="table-scroll"><table className="wifitable"><thead><tr><th>SSID</th><th>BSSID</th><th>Channel</th><th>RSSI</th><th>Security</th></tr></thead><tbody>{state.results.map((r, i) => <tr key={`${r.bssid}-${i}`}><td>{r.ssid || "Hidden"}</td><td className="mono">{r.bssid}</td><td>{r.channel ?? "—"}</td><td>{r.rssi ?? "—"} dBm</td><td>{r.security}</td></tr>)}</tbody></table></div>}
  </Card><Card title="Channel occupancy" subtitle="Networks observed per channel"><svg className="occupancy" viewBox="0 0 600 120" aria-label="Channel occupancy bars">{occupancy.map((bar) => <g key={bar.channel}><rect x={bar.x} y={bar.y} width={bar.width} height={bar.height} /><text x={bar.x + bar.width / 2} y="116" textAnchor="middle">{bar.channel}</text></g>)}</svg></Card></>;
}
