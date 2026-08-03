import { Badge } from "@/components/Badge";
import { Section } from "@/components/StatBlock";
import { pct } from "@/lib/format";
import {
  deriveCell,
  parseStreakLabel,
  type CompactCell,
  type NextGameStreak,
} from "@/lib/streaks";

/**
 * Today's games through the streak lens — built at publish time from the same
 * slate the Game Center shows.
 *
 * Each side shows its active streak, what history says about streaks of that
 * exact length for that team, and — deliberately in the same breath — the
 * model's own probability for tonight. The two numbers answer different
 * questions and the card never pretends otherwise: the history is "what
 * happened after runs like this", the model is "what this specific matchup
 * looks like", and when they disagree the model is the one built for the job.
 */
export function NextGameStreakCards({
  games,
  minOccurrences,
  shrinkageK,
}: {
  games: NextGameStreak[];
  minOccurrences: number;
  shrinkageK: number;
}) {
  if (!games.length) {
    return (
      <Section
        title="Next game streak watch"
        description="No games remain on the slate this page was built from. The table refreshes with the next publish."
      >
        <p className="text-sm muted">Check back when today&apos;s slate is up.</p>
      </Section>
    );
  }
  return (
    <Section
      title="Next game streak watch"
      description="Each side's active streak, what history says about streaks of that exact length, and the model's own number for the game — side by side on purpose."
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3.5 sm:grid-cols-2 xl:grid-cols-3">
        {games.map((game) => (
          <article
            key={game.game_id}
            className="surface flex min-w-0 flex-col gap-2.5 rounded-[12px] p-3.5"
          >
            <div className="flex items-center justify-between gap-2">
              <p className="t-heading">
                {game.away.abbreviation}{" "}
                <span className="subtle" style={{ fontWeight: 400 }}>
                  at
                </span>{" "}
                {game.home.abbreviation}
              </p>
              {game.model_home_win_prob !== null ? (
                <span className="t-micro numeral shrink-0 muted">
                  Model: {game.home.abbreviation}{" "}
                  {pct(game.model_home_win_prob)}
                </span>
              ) : (
                <span className="t-micro shrink-0 subtle">No prediction yet</span>
              )}
            </div>
            <SideRow side={game.away} minOccurrences={minOccurrences} shrinkageK={shrinkageK} />
            <SideRow side={game.home} minOccurrences={minOccurrences} shrinkageK={shrinkageK} />
          </article>
        ))}
      </div>
    </Section>
  );
}

function SideRow({
  side,
  minOccurrences,
  shrinkageK,
}: {
  side: NextGameStreak["home"];
  minOccurrences: number;
  shrinkageK: number;
}) {
  const streak = side.current_streak;
  if (!streak) {
    return (
      <div className="flex items-baseline gap-2">
        <span className="w-11 shrink-0 font-medium">{side.abbreviation}</span>
        <span className="t-small subtle">no completed game yet this season</span>
      </div>
    );
  }
  const { sign } = parseStreakLabel(streak);
  const history = side.history as
    | {
        n: number;
        continued: number;
        expected_win_rate: number | null;
        avg_next_run_diff: number | null;
      }
    | undefined;

  const derived = history
    ? deriveCell(
        [
          history.n,
          history.continued,
          history.expected_win_rate,
          history.avg_next_run_diff,
        ] as CompactCell,
        sign as 1 | -1,
        shrinkageK,
        minOccurrences,
      )
    : null;

  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
      <span className="w-11 shrink-0 font-medium">{side.abbreviation}</span>
      <Badge tone={sign > 0 ? "home" : "danger"}>
        <span className="numeral">{streak}</span>
      </Badge>
      {derived ? (
        <span className="t-small min-w-0 muted">
          continues {pct(derived.pContinueShrunk ?? derived.pContinue, 0)} ·
          ends {pct(1 - (derived.pContinueShrunk ?? derived.pContinue), 0)} ·
          adj{" "}
          <span className="numeral">
            {derived.adjustedEffectShrunk === null
              ? "—"
              : `${derived.adjustedEffectShrunk >= 0 ? "+" : "−"}${Math.abs(derived.adjustedEffectShrunk * 100).toFixed(1)}pp`}
          </span>{" "}
          · n={derived.n}
          {derived.insufficient ? (
            <span className="subtle italic"> · insufficient sample</span>
          ) : null}
        </span>
      ) : (
        <span className="t-small subtle">
          streak shorter than 2 — no history to consult
        </span>
      )}
    </div>
  );
}
