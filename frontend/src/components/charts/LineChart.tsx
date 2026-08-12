/**
 * Zero-dependency inline-SVG line chart (SPEC §2: no chart libraries, no CDN).
 *
 * Grown from DUT_browser's `src/components/charts/Sparkline.tsx` — same
 * viewBox/preserveAspectRatio approach, same `spark-*` class hooks and
 * `vectorEffect="non-scaling-stroke"` so strokes stay 1px under the non-uniform
 * scale. Extended for M3: multiple series (per-core CPU), a y-axis label rail,
 * and an x-axis showing the window span.
 */

const VIEW_W = 100;

export type Series = {
  label: string;
  values: number[];
  /** Any CSS colour; series beyond the palette repeat it. */
  color: string;
  /** Thin lines are per-core detail behind the bold aggregate. */
  faint?: boolean;
};

type Props = {
  series: Series[];
  /** Axis labels for the first and last sample (e.g. device timestamps). */
  startLabel?: string;
  endLabel?: string;
  /** Y-axis max; values are clamped to [0, max]. */
  max?: number;
  unit?: string;
  height?: number;
  ariaLabel: string;
};

export default function LineChart({
  series,
  startLabel,
  endLabel,
  max = 100,
  unit = "%",
  height = 180,
  ariaLabel,
}: Props) {
  const safeMax = max > 0 ? max : 1;
  const longest = series.reduce((count, item) => Math.max(count, item.values.length), 0);

  if (longest === 0) {
    return (
      <div className="chart-empty">
        No samples yet — charts fill in as the DUT reports each Test Time.
      </div>
    );
  }

  const toX = (index: number, length: number) =>
    length <= 1 ? VIEW_W / 2 : (index / (length - 1)) * VIEW_W;
  const toY = (value: number) => {
    const clamped = Math.min(safeMax, Math.max(0, value));
    return height - (clamped / safeMax) * height;
  };

  const gridFractions = [0.25, 0.5, 0.75, 1];

  return (
    <div className="chart">
      <div className="chart-rail" aria-hidden>
        {[...gridFractions].reverse().map((fraction) => (
          <span key={fraction}>{Math.round(safeMax * fraction)}</span>
        ))}
        <span>0</span>
      </div>
      <div className="chart-figure">
        <svg
          className="spark"
          viewBox={`0 0 ${VIEW_W} ${height}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={ariaLabel}
          style={{ height }}
        >
          {gridFractions.map((fraction) => (
            <line
              key={fraction}
              x1={0}
              x2={VIEW_W}
              y1={height - fraction * height}
              y2={height - fraction * height}
              className="spark-grid"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {series.map((item) => {
            if (item.values.length === 0) {
              return null;
            }
            const points = item.values
              .map((value, index) => `${toX(index, item.values.length).toFixed(2)},${toY(value).toFixed(2)}`)
              .join(" ");
            const last = item.values[item.values.length - 1];
            return (
              <g key={item.label}>
                <polyline
                  points={points}
                  className={`spark-line${item.faint ? " faint" : ""}`}
                  style={{ stroke: item.color }}
                  vectorEffect="non-scaling-stroke"
                  fill="none"
                />
                {!item.faint ? (
                  <circle
                    cx={toX(item.values.length - 1, item.values.length)}
                    cy={toY(last)}
                    r={2.5}
                    className="spark-dot"
                    style={{ fill: item.color }}
                    vectorEffect="non-scaling-stroke"
                  />
                ) : null}
              </g>
            );
          })}
        </svg>
        <div className="chart-axis">
          <span>{startLabel ?? ""}</span>
          <span>{endLabel ?? ""}</span>
        </div>
      </div>
      {/* Every series is listed, faint ones included: four anonymous per-core
          lines are worse than a slightly longer legend. */}
      <div className="chart-legend">
        {series.map((item) => {
          const last = item.values[item.values.length - 1];
          return (
            <span key={item.label} className={`legend-item${item.faint ? " faint" : ""}`}>
              <span className="swatch" style={{ background: item.color }} aria-hidden />
              {item.label}
              {typeof last === "number" ? (
                <strong>
                  {last}
                  {unit}
                </strong>
              ) : null}
            </span>
          );
        })}
      </div>
    </div>
  );
}
