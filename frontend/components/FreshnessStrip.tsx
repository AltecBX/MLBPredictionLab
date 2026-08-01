import { Dot } from "./Badge";
import { Tooltip, InfoIcon } from "./Tooltip";
import { FRESHNESS_LABEL, relativeAge } from "@/lib/format";
import type { FreshnessEntry } from "@/lib/types";

const TONE = {
  FRESH: "ok",
  AGING: "warn",
  STALE: "bad",
  UNAVAILABLE: "off",
} as const;

/** Freshness is per-category, never global (ARCHITECTURE.md §7). */
export function FreshnessStrip({ entries }: { entries: FreshnessEntry[] }) {
  if (!entries.length) return null;
  return (
    <div className="scroll-x -mx-1 px-1">
      <ul className="flex min-w-max items-center gap-x-4 gap-y-1.5 text-[0.7rem]">
        {entries.map((entry) => (
          <li key={entry.category} className="flex items-center gap-1.5 whitespace-nowrap">
            <Dot tone={TONE[entry.freshness] ?? "off"} />
            <span className="muted">{entry.label}</span>
            <span className="subtle">
              {entry.freshness === "UNAVAILABLE"
                ? "unavailable"
                : relativeAge(entry.age_seconds)}
            </span>
            {entry.detail ? (
              <Tooltip
                label={
                  <>
                    <strong>{entry.label}</strong> — {FRESHNESS_LABEL[entry.freshness]}.
                    <br />
                    {entry.detail}
                  </>
                }
              >
                <InfoIcon />
              </Tooltip>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
