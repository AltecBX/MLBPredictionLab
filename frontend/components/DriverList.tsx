import { Badge } from "./Badge";
import { InfoIcon, Tooltip } from "./Tooltip";
import type { DriverSummary } from "@/lib/types";

/**
 * The five factors that most increased a side's probability, in probability
 * points, translated into baseball language.
 */
export function DriverList({
  drivers,
  tone,
  emptyMessage,
}: {
  drivers: DriverSummary[];
  tone: "home" | "away";
  emptyMessage: string;
}) {
  if (!drivers.length) {
    return <p className="text-sm muted">{emptyMessage}</p>;
  }
  const color = tone === "home" ? "var(--home)" : "var(--away)";

  return (
    <ol
      className="flex min-w-0 flex-col divide-y"
      style={{ borderColor: "var(--border)" }}
    >
      {drivers.map((driver) => (
        <li
          key={driver.feature_key}
          className="flex min-w-0 items-start gap-3 py-2.5 first:pt-0"
        >
          <span
            className="tnum w-12 shrink-0 text-right text-sm font-semibold sm:w-14"
            style={{ color }}
          >
            +{driver.contribution_pp.toFixed(1)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{driver.display_name}</p>
            {driver.narrative ? (
              <p className="mt-0.5 text-xs leading-relaxed muted">{driver.narrative}</p>
            ) : null}
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <Badge tone="muted">{driver.category_label}</Badge>
              {driver.sample_size ? (
                <span className="text-[0.7rem] subtle">n={driver.sample_size}</span>
              ) : null}
              {driver.is_estimated ? (
                <Tooltip label="This input was shrunk toward a league baseline because its sample is still small. The sample size behind it is shown next to it.">
                  <span className="inline-flex items-center gap-1 text-[0.7rem] subtle">
                    estimated <InfoIcon />
                  </span>
                </Tooltip>
              ) : null}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}
