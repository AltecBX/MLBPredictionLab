import type { CalibrationBin } from "@/lib/types";

const W = 460;
const H = 320;
const PAD = { top: 12, right: 12, bottom: 40, left: 46 };

/**
 * Predicted probability against observed win frequency, with per-bin counts and
 * Wilson intervals so a reader can see which deviations are meaningful
 * (BACKTEST_PLAN.md §5). Hand-rolled SVG — no chart dependency.
 */
export function CalibrationChart({
  bins,
  ece,
  mce,
}: {
  bins: CalibrationBin[];
  ece: number | null;
  mce: number | null;
}) {
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const populated = bins.filter((b) => b.n > 0 && b.mean_predicted !== null);
  const maxN = Math.max(...bins.map((b) => b.n), 1);

  const x = (v: number) => PAD.left + v * plotW;
  const y = (v: number) => PAD.top + (1 - v) * plotH;

  if (!populated.length) {
    return (
      <p className="text-sm muted">
        No calibration bins are populated yet. Run a backtest to produce them.
      </p>
    );
  }

  const path = populated
    .map(
      (b, i) =>
        `${i === 0 ? "M" : "L"}${x(b.mean_predicted!).toFixed(1)},${y(
          b.observed_frequency!,
        ).toFixed(1)}`,
    )
    .join(" ");

  return (
    <figure className="m-0">
      {/* Capped so the SVG never scales its 10px labels up to headline size. */}
      <div className="scroll-x max-w-[560px]">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full min-w-[380px]"
          role="img"
          aria-label="Calibration chart: predicted probability versus observed win frequency"
        >
          {/* count underlay */}
          {bins.map((b) =>
            b.n > 0 ? (
              <rect
                key={`n-${b.lower}`}
                x={x(b.lower) + 1}
                y={PAD.top + plotH - (b.n / maxN) * plotH * 0.28}
                width={Math.max(plotW / bins.length - 2, 1)}
                height={(b.n / maxN) * plotH * 0.28}
                fill="var(--track)"
              />
            ) : null,
          )}

          {/* axes */}
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line
                x1={PAD.left}
                x2={PAD.left + plotW}
                y1={y(t)}
                y2={y(t)}
                stroke="var(--border)"
                strokeWidth="1"
              />
              <text
                x={PAD.left - 8}
                y={y(t) + 3.5}
                textAnchor="end"
                fontSize="10"
                fill="var(--text-subtle)"
              >
                {(t * 100).toFixed(0)}%
              </text>
              <text
                x={x(t)}
                y={PAD.top + plotH + 16}
                textAnchor="middle"
                fontSize="10"
                fill="var(--text-subtle)"
              >
                {(t * 100).toFixed(0)}%
              </text>
            </g>
          ))}

          {/* perfect-calibration reference */}
          <line
            x1={x(0)}
            y1={y(0)}
            x2={x(1)}
            y2={y(1)}
            stroke="var(--border-strong)"
            strokeWidth="1"
            strokeDasharray="4 4"
          />

          {/* Wilson intervals */}
          {populated.map((b) =>
            b.wilson_low !== null && b.wilson_high !== null ? (
              <line
                key={`ci-${b.lower}`}
                x1={x(b.mean_predicted!)}
                x2={x(b.mean_predicted!)}
                y1={y(b.wilson_low)}
                y2={y(b.wilson_high)}
                stroke="var(--accent)"
                strokeWidth="1.5"
                strokeOpacity="0.35"
                strokeLinecap="round"
              />
            ) : null,
          )}

          <path d={path} fill="none" stroke="var(--accent)" strokeWidth="2" />

          {populated.map((b) => (
            <circle
              key={`p-${b.lower}`}
              cx={x(b.mean_predicted!)}
              cy={y(b.observed_frequency!)}
              r="3.5"
              fill="var(--accent)"
            >
              <title>
                {`Predicted ${(b.mean_predicted! * 100).toFixed(1)}% · observed ${(
                  b.observed_frequency! * 100
                ).toFixed(1)}% · n=${b.n}`}
              </title>
            </circle>
          ))}

          <text
            x={PAD.left + plotW / 2}
            y={H - 6}
            textAnchor="middle"
            fontSize="10.5"
            fill="var(--text-muted)"
          >
            Predicted home win probability
          </text>
          <text
            x={-(PAD.top + plotH / 2)}
            y={13}
            transform="rotate(-90)"
            textAnchor="middle"
            fontSize="10.5"
            fill="var(--text-muted)"
          >
            Observed win frequency
          </text>
        </svg>
      </div>
      <figcaption className="mt-2 text-xs muted">
        Dashed line is perfect calibration. Vertical bars are 95% Wilson intervals; a
        point off the diagonal with a wide interval is small-sample noise, not a
        calibration failure. Grey underlay shows how many games fall in each bin.
        {ece !== null ? (
          <>
            {" "}
            Expected calibration error <strong className="tnum">{(ece * 100).toFixed(2)}%</strong>
            {mce !== null ? (
              <>
                , maximum <strong className="tnum">{(mce * 100).toFixed(2)}%</strong>
              </>
            ) : null}
            .
          </>
        ) : null}
      </figcaption>
    </figure>
  );
}
