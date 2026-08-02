import Link from "next/link";

import { Badge, TeamTag } from "./Badge";
import { ProbabilityBar } from "./ProbabilityBar";
import { TeamContextLine } from "./TeamContextLine";
import {
  CONFIDENCE_LABEL,
  RECOMMENDATION_LABEL,
  gameTime,
  num,
  pct,
  record,
  timestamp,
} from "@/lib/format";
import type { GameCard as GameCardType, TeamRef as TeamRefType } from "@/lib/types";

const RECOMMENDATION_TONE: Record<string, "home" | "accent" | "neutral" | "warn"> = {
  STRONG_LEAN: "home",
  MODERATE_LEAN: "accent",
  SMALL_LEAN: "neutral",
  NO_MEANINGFUL_ADVANTAGE: "muted" as never,
  INSUFFICIENT_DATA: "warn",
};

/**
 * One row of the matchup.
 *
 * The abbreviation is a fixed-width chip, so two rows align on a hard vertical
 * edge and the club name always starts at the same x. Without that the rows read
 * as ragged text; with it they read as a table, which is what a matchup is.
 */
function TeamRow({
  team,
  name,
  abbreviation,
  wins,
  losses,
  pitcher,
  favored,
  score,
  showScore,
  isHome,
}: {
  team: TeamRefType;
  name: string;
  abbreviation: string;
  wins: number | null;
  losses: number | null;
  pitcher: { full_name: string | null; pitch_hand: string | null; status: string };
  favored: boolean;
  score: number | null;
  showScore: boolean;
  isHome: boolean;
}) {
  const rec = record(wins, losses);
  const tone = isHome ? "var(--home)" : "var(--away)";

  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <TeamTag abbreviation={abbreviation} emphasis={favored} tone={tone} />

      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <span
            className="t-body min-w-0 truncate"
            style={{ fontWeight: favored ? 640 : 520 }}
          >
            {name}
          </span>
          {rec ? <span className="t-micro tnum shrink-0 subtle">{rec}</span> : null}
          {/* Role-specific record and streak: an away team's road record is what
              bears on an away game, and it is often nothing like the overall
              one. Context only — never a model input. */}
          <TeamContextLine team={team} isHome={isHome} className="ml-auto shrink-0" />
        </div>
        <p className="t-micro mt-0.5 truncate muted">
          {pitcher.full_name ? (
            <>
              {pitcher.full_name}
              {pitcher.pitch_hand ? (
                <span className="subtle"> · {pitcher.pitch_hand}HP</span>
              ) : null}
            </>
          ) : (
            <span style={{ color: "var(--color-warn-500)" }}>Starter not announced</span>
          )}
        </p>
      </div>

      {showScore ? (
        <span
          className="numeral-lg shrink-0 text-[1.375rem] leading-none"
          style={{ color: favored ? tone : "var(--text)" }}
        >
          {score ?? "—"}
        </span>
      ) : null}
    </div>
  );
}

/** A labelled figure in the card's stat strip. */
function Stat({
  label,
  value,
  suffix,
}: {
  label: string;
  value: string;
  suffix?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="eyebrow">{label}</dt>
      <dd className="numeral mt-1 truncate text-[0.9375rem] leading-none">
        {value}
        {suffix ? <span className="t-micro ml-1 font-normal subtle">{suffix}</span> : null}
      </dd>
    </div>
  );
}

export function GameCardView({ game }: { game: GameCardType }) {
  const prediction = game.prediction;
  const homeFavored = prediction ? prediction.predicted_winner === "HOME" : false;

  return (
    <article className="card card-interactive flex min-w-0 flex-col overflow-hidden">
      {/* An open header — time and venue as a quiet line, status chips right.
          The old full-width tinted band read as web furniture; a native cell
          separates with space and a hairline, not with panels. */}
      <header className="flex items-center justify-between gap-2 px-4 pt-3.5 pb-1">
        {/* The time never wraps; a long ballpark name truncates instead. */}
        <div className="t-micro flex min-w-0 items-center gap-1.5 muted">
          <span className="tnum whitespace-nowrap" style={{ fontWeight: 580 }}>
            {gameTime(game.first_pitch_utc)}
          </span>
          {game.ballpark.name ? (
            <>
              <span aria-hidden className="subtle">
                ·
              </span>
              <span className="truncate" title={game.ballpark.name}>
                {game.ballpark.name}
              </span>
            </>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {game.is_final ? <Badge tone="muted">Final</Badge> : null}
          {game.doubleheader && game.doubleheader !== "N" ? (
            <Badge tone="muted">DH</Badge>
          ) : null}
          {prediction ? (
            <Badge tone={RECOMMENDATION_TONE[prediction.recommendation] ?? "neutral"}>
              {RECOMMENDATION_LABEL[prediction.recommendation] ??
                prediction.recommendation}
            </Badge>
          ) : null}
        </div>
      </header>

      <div className="flex flex-col gap-3.5 px-4 pt-2.5 pb-4">
        <div className="flex flex-col gap-3">
          <TeamRow
            team={game.away}
            isHome={false}
            name={game.away.team_name ?? game.away.name}
            abbreviation={game.away.abbreviation}
            wins={game.away.wins}
            losses={game.away.losses}
            pitcher={game.away_pitcher}
            favored={!!prediction && !homeFavored}
            score={game.away_score}
            showScore={game.is_final}
          />
          <TeamRow
            team={game.home}
            isHome
            name={game.home.team_name ?? game.home.name}
            abbreviation={game.home.abbreviation}
            wins={game.home.wins}
            losses={game.home.losses}
            pitcher={game.home_pitcher}
            favored={!!prediction && homeFavored}
            score={game.home_score}
            showScore={game.is_final}
          />
        </div>

        {prediction ? (
          <>
            <hr className="rule-soft" />

            <ProbabilityBar
              homeProb={prediction.home_win_prob}
              homeLabel={game.home.abbreviation}
              awayLabel={game.away.abbreviation}
              animate={false}
            />

            <dl
              className="grid grid-cols-3 gap-3 rounded-[var(--radius-md)] px-3.5 py-2.5"
              style={{ background: "var(--surface-inset)" }}
            >
              <Stat
                label="Projected"
                value={
                  prediction.projected_score.away_runs !== null
                    ? `${num(prediction.projected_score.away_runs, 1)}–${num(
                        prediction.projected_score.home_runs,
                        1,
                      )}`
                    : "—"
                }
              />
              <Stat
                label="Confidence"
                value={
                  CONFIDENCE_LABEL[prediction.confidence_label]?.replace(
                    " confidence",
                    "",
                  ) ?? prediction.confidence_label
                }
                suffix={pct(prediction.confidence_score, 0)}
              />
              <Stat label="Data" value={pct(prediction.data_completeness, 0)} />
            </dl>

            {prediction.top_drivers.length ? (
              <ul className="flex flex-col gap-1.5">
                {prediction.top_drivers.map((driver) => (
                  <li
                    key={driver.feature_key}
                    className="t-micro flex min-w-0 items-center gap-2.5"
                  >
                    <span
                      className="numeral w-10 shrink-0 text-right"
                      style={{ color: homeFavored ? "var(--home)" : "var(--away)" }}
                    >
                      +{driver.contribution_pp.toFixed(1)}
                    </span>
                    {/* A hairline weight bar: the same number again, as length.
                        Reading a list of magnitudes is faster as shape than as
                        digits, and both are present so neither is a guess. */}
                    <span
                      aria-hidden
                      className="h-1 w-8 shrink-0 overflow-hidden rounded-full"
                      style={{ background: "var(--track)" }}
                    >
                      <span
                        className="block h-full rounded-full"
                        style={{
                          width: `${Math.min(driver.contribution_pp / 8, 1) * 100}%`,
                          background: homeFavored ? "var(--home)" : "var(--away)",
                          opacity: 0.7,
                        }}
                      />
                    </span>
                    <span className="min-w-0 truncate muted">{driver.display_name}</span>
                  </li>
                ))}
              </ul>
            ) : null}

            {(game.lineup_status === "UNAVAILABLE" ||
              game.bullpen_warning ||
              prediction.warnings.some((w) => w.severity === "high")) && (
              <ul className="t-micro flex flex-col gap-1">
                {prediction.warnings
                  .filter((w) => w.severity === "high")
                  .slice(0, 2)
                  .map((w) => (
                    <li key={w.code} style={{ color: "var(--color-warn-500)" }}>
                      {w.message}
                    </li>
                  ))}
                {game.bullpen_warning ? (
                  <li className="muted">{game.bullpen_warning}</li>
                ) : null}
                {game.lineup_status === "UNAVAILABLE" ? (
                  <li className="subtle">Lineups not confirmed</li>
                ) : null}
              </ul>
            )}
          </>
        ) : (
          <p
            className="t-micro rounded-[var(--radius-md)] border border-dashed p-3 muted"
            style={{ borderColor: "var(--border-strong)" }}
          >
            {game.prediction_unavailable?.reason ??
              "No prediction has been generated for this game."}
          </p>
        )}
      </div>

      <footer
        className="t-micro mt-auto flex min-w-0 items-center justify-between gap-2 border-t px-4 py-1.5 subtle"
        style={{
          borderColor: "color-mix(in srgb, var(--border) 72%, transparent)",
        }}
      >
        <span className="min-w-0 truncate">
          {prediction ? `Updated ${timestamp(prediction.created_at)}` : "Not predicted"}
        </span>
        {/* The one action on a card, sized for a thumb rather than a cursor. */}
        <Link
          href={`/game/${game.game_id}`}
          className="tap group shrink-0 gap-1.5 pl-3"
          style={{ color: "var(--accent)", fontWeight: 600 }}
        >
          Full breakdown
          <span
            aria-hidden
            className="inline-flex size-[1.125rem] items-center justify-center rounded-full transition-transform group-hover:translate-x-0.5"
            style={{ background: "var(--accent-soft)", fontSize: "0.6875rem" }}
          >
            →
          </span>
        </Link>
      </footer>
    </article>
  );
}
