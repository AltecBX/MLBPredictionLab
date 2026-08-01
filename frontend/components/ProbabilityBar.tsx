import { pct } from "@/lib/format";

/**
 * The single most important element on a card: which team is favored and by
 * how much, readable in under five seconds.
 */
export function ProbabilityBar({
  homeProb,
  homeLabel,
  awayLabel,
  compact = false,
}: {
  homeProb: number;
  homeLabel: string;
  awayLabel: string;
  compact?: boolean;
}) {
  const home = Math.min(Math.max(homeProb, 0), 1);
  const away = 1 - home;
  const homeFavored = home >= 0.5;

  return (
    <div>
      <div
        className={`flex items-baseline justify-between ${compact ? "text-xs" : "text-sm"}`}
      >
        <span
          className={`tnum font-semibold ${homeFavored ? "" : "opacity-60"}`}
          style={{ color: homeFavored ? "var(--home)" : undefined }}
        >
          {pct(away)} <span className="font-normal opacity-70">{awayLabel}</span>
        </span>
        <span
          className={`tnum font-semibold ${homeFavored ? "" : "opacity-60"}`}
          style={{ color: homeFavored ? "var(--home)" : undefined }}
        >
          <span className="font-normal opacity-70">{homeLabel}</span> {pct(home)}
        </span>
      </div>
      <div
        className="mt-1.5 flex h-2 overflow-hidden rounded-full"
        style={{ background: "var(--track)" }}
        role="img"
        aria-label={`${awayLabel} ${pct(away)}, ${homeLabel} ${pct(home)}`}
      >
        <div
          className="h-full transition-[width] duration-300"
          style={{ width: `${away * 100}%`, background: "var(--away)" }}
        />
        <div
          className="h-full transition-[width] duration-300"
          style={{ width: `${home * 100}%`, background: "var(--home)" }}
        />
      </div>
    </div>
  );
}
