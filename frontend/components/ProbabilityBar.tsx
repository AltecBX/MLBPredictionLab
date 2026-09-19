import { pct } from "@/lib/format";

/**
 * The single most important element on a card: which team is favored and by how
 * much, readable in under five seconds. It is set as the card's hero — the
 * leading percentage is the largest thing on the card by design, because it is
 * the product's entire answer.
 *
 * Three deliberate choices.
 *
 * **The favoured side is stated, not inferred.** The larger number is set at a
 * much bigger optical size in its own colour; the other recedes. A reader
 * scanning fifteen cards should never have to compare two equal-looking
 * numbers.
 *
 * **The midpoint is marked.** A 52/48 game and an 80/20 game look alike at a
 * glance without a reference line, and the difference between them is the
 * entire product. The notch sits at exactly 50% and is the only thing on the
 * bar that does not move.
 *
 * **Both halves grow from the outside in.** Each fills from its own edge, so
 * the seam lands where the probability is rather than sweeping past it — and
 * each side's fill brightens toward the seam, so the meeting point reads as
 * two forces pressing rather than two blocks abutting. That gradient encodes
 * direction; it is data, not decoration.
 */
export function ProbabilityBar({
  homeProb,
  homeLabel,
  awayLabel,
  compact = false,
  animate = true,
}: {
  homeProb: number;
  homeLabel: string;
  awayLabel: string;
  compact?: boolean;
  /** Off inside a list that already staggers, so the two do not compete. */
  animate?: boolean;
}) {
  const home = Math.min(Math.max(homeProb, 0), 1);
  const away = 1 - home;
  const homeFavored = home >= 0.5;

  const leader = homeFavored
    ? { pct: home, label: homeLabel, tone: "home" as const }
    : { pct: away, label: awayLabel, tone: "away" as const };
  const trailer = homeFavored
    ? { pct: away, label: awayLabel }
    : { pct: home, label: homeLabel };

  return (
    <div>
      <div className="flex items-end justify-between gap-3">
        {/* Away always sits left and home right, matching the bar beneath and
            the row order on the card. Emphasis, not position, carries which
            side is favoured. */}
        <Side
          label={awayLabel}
          value={away}
          leading={!homeFavored}
          tone="away"
          compact={compact}
          align="left"
        />
        <Side
          label={homeLabel}
          value={home}
          leading={homeFavored}
          tone="home"
          compact={compact}
          align="right"
        />
      </div>

      <div
        className={`meter-track relative mt-2 flex overflow-hidden rounded-full ${
          compact ? "h-2" : "h-3"
        }`}
        role="img"
        aria-label={`${awayLabel} ${pct(away)}, ${homeLabel} ${pct(home)}. ${
          leader.label
        } favoured by ${Math.round(Math.abs(home - away) * 100)} points.`}
      >
        <div
          className={`meter-away ${animate ? "meter-fill" : ""} h-full`}
          style={{ width: `${away * 100}%` }}
        />
        <div
          className={`meter-home ${animate ? "meter-fill-right" : ""} h-full`}
          style={{ width: `${home * 100}%` }}
        />
        {/* The even mark. A notch through the full height, drawn over both
            halves so it reads at any split. */}
        <span
          aria-hidden
          className="pb-notch pointer-events-none absolute inset-y-0 left-1/2 w-[2px] -translate-x-1/2 rounded-full"
        />
      </div>

      {!compact ? (
        <p className="t-micro mt-1.5 subtle">
          {leader.pct - trailer.pct < 0.02 ? (
            "Effectively even"
          ) : (
            <>
              <span className={`ink font-semibold tone-${leader.tone}`}>{leader.label}</span>{" "}
              by {Math.round((leader.pct - trailer.pct) * 100)} points
            </>
          )}
        </p>
      ) : null}
    </div>
  );
}

/**
 * One side's readout. The sizes and colours are `.pb-label` / `.pb-value` in
 * the stylesheet, with `.lead` on the favoured side; `tone-*` on the wrapper
 * supplies the colour they read.
 */
function Side({
  label,
  value,
  leading,
  tone,
  compact,
  align,
}: {
  label: string;
  value: number;
  leading: boolean;
  tone: "home" | "away";
  compact: boolean;
  align: "left" | "right";
}) {
  const lead = leading ? " lead" : "";
  return (
    <span
      className={`flex min-w-0 flex-col tone-${tone} ${
        align === "right" ? "items-end text-right" : "items-start text-left"
      }${compact ? " pb-compact" : ""}`}
    >
      <span className={`pb-label t-micro font-mono uppercase${lead}`}>{label}</span>
      <span className={`pb-value numeral-lg leading-none${lead}`}>{pct(value)}</span>
    </span>
  );
}
