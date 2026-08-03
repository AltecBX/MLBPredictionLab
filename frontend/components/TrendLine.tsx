import { useId } from "react";

import { pct } from "@/lib/format";

/**
 * How the home win probability moved across the predictions issued for one
 * game. Every point is a real issued prediction — the same immutable rows the
 * table beneath this lists — so the line is a record, not an illustration.
 *
 * Two scale decisions carry the honesty:
 *   - x is *time*, not index. Predictions cluster near first pitch as lineups
 *     firm up, and even spacing would hide that rhythm.
 *   - y zooms to the data but always includes the 50% line, so a probability
 *     drifting from 58% to 62% reads as the modest move it is, never as a
 *     plunge or a surge.
 */
export function TrendLine({
  points,
  ariaLabel,
}: {
  points: { t: string; v: number }[];
  ariaLabel: string;
}) {
  const gradientId = useId();
  const ordered = [...points].sort((a, b) => a.t.localeCompare(b.t));
  if (ordered.length < 2) return null;

  const W = 300;
  const H = 46;
  const PX = 6;
  const PY = 7;

  const times = ordered.map((point) => Date.parse(point.t));
  const t0 = Math.min(...times);
  const span = Math.max(...times) - t0;
  const x = (i: number) =>
    PX +
    (span > 0 ? (times[i] - t0) / span : i / (ordered.length - 1)) * (W - 2 * PX);

  const values = ordered.map((point) => point.v);
  const lo = Math.max(0, Math.min(...values, 0.5) - 0.05);
  const hi = Math.min(1, Math.max(...values, 0.5) + 0.05);
  const y = (v: number) => PY + (1 - (v - lo) / (hi - lo)) * (H - 2 * PY);

  const line = ordered
    .map((point, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(point.v).toFixed(1)}`)
    .join(" ");
  const area = `${line} L ${x(ordered.length - 1).toFixed(1)},${H - PY} L ${x(0).toFixed(1)},${H - PY} Z`;

  const last = ordered[ordered.length - 1];
  const lastX = x(ordered.length - 1);
  const lastY = y(last.v);
  // The current value labels its own dot — anchored away from the edge so the
  // number never clips off the right of the chart.
  const labelAnchor = lastX > W - 34 ? "end" : "middle";
  const labelX = labelAnchor === "end" ? lastX - 7 : lastX;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={ariaLabel}>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--accent)" stopOpacity="0.16" />
          <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <line
        x1={PX}
        x2={W - PX}
        y1={y(0.5)}
        y2={y(0.5)}
        stroke="var(--border-strong)"
        strokeWidth="1"
        strokeDasharray="3 4"
      />
      <path d={area} fill={`url(#${gradientId})`} />
      <path
        d={line}
        stroke="var(--accent)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      {ordered.slice(0, -1).map((point, i) => (
        <circle
          key={point.t}
          cx={x(i).toFixed(1)}
          cy={y(point.v).toFixed(1)}
          r="2"
          fill="var(--accent)"
          fillOpacity="0.65"
        />
      ))}
      <circle cx={lastX} cy={lastY} r="5.5" fill="var(--accent)" fillOpacity="0.18" />
      <circle cx={lastX} cy={lastY} r="2.6" fill="var(--accent)" />
      <text
        x={labelX}
        y={lastY - 7 < 9 ? lastY + 13 : lastY - 7}
        textAnchor={labelAnchor}
        fontSize="9.5"
        fontWeight="650"
        fill="var(--accent)"
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {pct(last.v)}
      </text>
    </svg>
  );
}
