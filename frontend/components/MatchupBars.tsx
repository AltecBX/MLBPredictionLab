import { signedPp } from "@/lib/format";
import type { MatchupBar } from "@/lib/types";

/**
 * Which side holds the advantage in each phase of the game, measured in the
 * same probability points the model actually assigned.
 */
export function MatchupBars({
  bars,
  homeLabel,
  awayLabel,
}: {
  bars: MatchupBar[];
  homeLabel: string;
  awayLabel: string;
}) {
  if (!bars.length) {
    return <p className="text-sm muted">No contribution breakdown available.</p>;
  }
  const max = Math.max(...bars.map((b) => Math.abs(b.net_pp)), 1);

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between text-[0.7rem] subtle">
        <span>← {awayLabel}</span>
        <span>{homeLabel} →</span>
      </div>
      {bars.map((bar) => {
        const width = (Math.abs(bar.net_pp) / max) * 50;
        const homeSide = bar.net_pp > 0;
        return (
          <div
            key={bar.category}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3"
          >
            <div>
              <div className="mb-1 flex items-baseline justify-between text-xs">
                <span className="font-medium">{bar.label}</span>
                <span
                  className="tnum text-[0.7rem]"
                  style={{
                    color:
                      bar.advantage === "EVEN"
                        ? "var(--text-subtle)"
                        : homeSide
                          ? "var(--home)"
                          : "var(--away)",
                  }}
                >
                  {bar.advantage === "EVEN" ? "even" : signedPp(Math.abs(bar.net_pp))}
                </span>
              </div>
              <div
                className="relative h-2 rounded-full"
                style={{ background: "var(--track)" }}
                role="img"
                aria-label={`${bar.label}: ${
                  bar.advantage === "EVEN"
                    ? "even"
                    : `${homeSide ? homeLabel : awayLabel} by ${Math.abs(bar.net_pp).toFixed(1)} points`
                }`}
              >
                <span
                  aria-hidden
                  className="absolute inset-y-0 left-1/2 w-px"
                  style={{ background: "var(--border-strong)" }}
                />
                <span
                  className="absolute inset-y-0 rounded-full transition-[width] duration-300"
                  style={{
                    width: `${width}%`,
                    left: homeSide ? "50%" : undefined,
                    right: homeSide ? undefined : "50%",
                    // Brightening toward the tip — the bar's leading edge is
                    // where the eye reads its length, same as the slate meters.
                    background: homeSide
                      ? "linear-gradient(to right, var(--home), var(--home-hi))"
                      : "linear-gradient(to left, var(--away), var(--away-hi))",
                  }}
                />
              </div>
            </div>
            <span
              className="w-14 shrink-0 text-right text-[0.7rem] font-medium"
              style={{
                color:
                  bar.advantage === "EVEN"
                    ? "var(--text-subtle)"
                    : homeSide
                      ? "var(--home)"
                      : "var(--away)",
              }}
            >
              {bar.advantage === "EVEN"
                ? "—"
                : homeSide
                  ? homeLabel
                  : awayLabel}
            </span>
          </div>
        );
      })}
    </div>
  );
}
