import { humanizeKey, num } from "@/lib/format";
import type { FeatureCell } from "@/lib/types";

/**
 * Side-by-side feature comparison with the sample size behind every value and
 * an explicit marker when a value was shrunk toward a baseline.
 */
export function FeatureTable({
  home,
  away,
  homeLabel,
  awayLabel,
  labels,
  emptyMessage,
}: {
  home: Record<string, FeatureCell>;
  away: Record<string, FeatureCell>;
  homeLabel: string;
  awayLabel: string;
  labels?: Record<string, string>;
  emptyMessage: string;
}) {
  const keys = Array.from(new Set([...Object.keys(home), ...Object.keys(away)])).sort();
  const usable = keys.filter(
    (k) => home[k]?.value !== null || away[k]?.value !== null,
  );
  if (!usable.length) {
    return <p className="text-sm muted">{emptyMessage}</p>;
  }

  return (
    <div className="scroll-x edge-cue">
      <table className="data sticky-label min-w-[340px]">
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col" className="num">
              {awayLabel}
            </th>
            <th scope="col" className="num">
              {homeLabel}
            </th>
            <th scope="col" className="num">
              Sample
            </th>
          </tr>
        </thead>
        <tbody>
          {usable.map((key) => {
            const h = home[key];
            const a = away[key];
            const sample = Math.min(h?.sample_size ?? 0, a?.sample_size ?? 0);
            const estimated = (h?.is_estimated ?? false) || (a?.is_estimated ?? false);
            return (
              <tr key={key}>
                <th scope="row" className="font-normal">
                  {labels?.[key] ?? humanizeKey(key)}
                  {estimated ? (
                    <span
                      className="ml-1.5 text-[0.65rem] subtle"
                      title="Shrunk toward a league baseline because the sample is still small"
                    >
                      est
                    </span>
                  ) : null}
                </th>
                <td className="num tnum">{num(a?.value ?? null, 3)}</td>
                <td className="num tnum">{num(h?.value ?? null, 3)}</td>
                <td className="num tnum subtle">{sample || "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
