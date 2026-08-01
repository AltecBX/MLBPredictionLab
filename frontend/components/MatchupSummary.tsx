import { InfoIcon, Tooltip } from "./Tooltip";
import type { MatchupSummaryRow } from "@/lib/types";

/**
 * The five-second read: nine fixed rows in a fixed order, so two games can be
 * compared by glancing at the same lines in each.
 *
 * Four states, and the difference between the last two is the point:
 *   a team abbreviation — that side holds a measurable edge
 *   "level"            — measured, and the sides are even. That is a finding.
 *   "unavailable"      — not measured. Never shown as level, and the row names
 *                        the provider that would populate it.
 */

function toneFor(advantage: string): string | undefined {
  if (advantage === "HOME") return "var(--home)";
  if (advantage === "AWAY") return "var(--away)";
  return undefined;
}

function Verdict({ row }: { row: MatchupSummaryRow }) {
  if (!row.available) {
    return (
      <span className="text-[0.7rem] italic subtle">unavailable</span>
    );
  }
  if (row.advantage === "EVEN") {
    return <span className="text-xs subtle">level</span>;
  }
  return (
    <span className="text-sm font-semibold" style={{ color: toneFor(row.advantage) }}>
      {row.team}
    </span>
  );
}

export function MatchupSummary({ rows }: { rows: MatchupSummaryRow[] }) {
  if (!rows.length) {
    return <p className="text-sm muted">No summary is available for this game.</p>;
  }

  return (
    <ol className="flex min-w-0 flex-col divide-y" style={{ borderColor: "var(--border)" }}>
      {rows.map((row) => (
        <li
          key={row.key}
          className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 py-2 first:pt-0"
        >
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-sm">
              <span className={row.is_context ? "muted" : "font-medium"}>{row.label}</span>
              {row.is_context ? (
                <Tooltip
                  label={
                    row.detail ??
                    "Descriptive context. This row carries no probability weight."
                  }
                >
                  <InfoIcon />
                </Tooltip>
              ) : null}
            </p>
            {row.value ? (
              <p className="tnum mt-0.5 truncate text-xs subtle">{row.value}</p>
            ) : null}
            {!row.available && row.required_source ? (
              <p className="mt-0.5 text-[0.7rem] subtle">
                Needs{" "}
                <code className="font-mono">{row.required_source}</code>
              </p>
            ) : null}
          </div>

          <div className="flex shrink-0 flex-col items-end">
            <Verdict row={row} />
            {row.available && row.magnitude_pp !== null && !row.is_context ? (
              <span className="tnum text-[0.7rem] subtle">
                {row.magnitude_pp.toFixed(1)} pp
              </span>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
