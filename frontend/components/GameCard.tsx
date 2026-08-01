import Link from "next/link";

import { Badge } from "./Badge";
import { ProbabilityBar } from "./ProbabilityBar";
import {
  CONFIDENCE_LABEL,
  RECOMMENDATION_LABEL,
  gameTime,
  num,
  pct,
  record,
  timestamp,
} from "@/lib/format";
import type { GameCard as GameCardType } from "@/lib/types";

const RECOMMENDATION_TONE: Record<string, "home" | "accent" | "neutral" | "warn"> = {
  STRONG_LEAN: "home",
  MODERATE_LEAN: "accent",
  SMALL_LEAN: "neutral",
  NO_MEANINGFUL_ADVANTAGE: "muted" as never,
  INSUFFICIENT_DATA: "warn",
};

function TeamRow({
  name,
  abbreviation,
  wins,
  losses,
  pitcher,
  favored,
  score,
  showScore,
}: {
  name: string;
  abbreviation: string;
  wins: number | null;
  losses: number | null;
  pitcher: { full_name: string | null; pitch_hand: string | null; status: string };
  favored: boolean;
  score: number | null;
  showScore: boolean;
}) {
  const rec = record(wins, losses);
  return (
    <div className="flex items-baseline justify-between gap-3">
      <div className="min-w-0">
        <p className={`truncate text-sm ${favored ? "font-semibold" : "font-medium"}`}>
          <span className="mr-1.5 font-mono text-[0.7rem] subtle">{abbreviation}</span>
          {name}
          {rec ? <span className="ml-1.5 text-xs subtle tnum">{rec}</span> : null}
        </p>
        <p className="mt-0.5 truncate text-xs muted">
          {pitcher.full_name ? (
            <>
              {pitcher.full_name}
              {pitcher.pitch_hand ? (
                <span className="subtle"> ({pitcher.pitch_hand}HP)</span>
              ) : null}
            </>
          ) : (
            <span className="italic" style={{ color: "var(--color-warn-500)" }}>
              Starter not announced
            </span>
          )}
        </p>
      </div>
      {showScore ? (
        <span className="tnum text-lg font-semibold">{score ?? "—"}</span>
      ) : null}
    </div>
  );
}

export function GameCardView({ game }: { game: GameCardType }) {
  const prediction = game.prediction;
  const homeFavored = prediction ? prediction.predicted_winner === "HOME" : false;

  return (
    <article className="surface rise flex flex-col gap-3 p-4">
      <header className="flex items-start justify-between gap-2">
        {/* The time never wraps; a long ballpark name truncates instead. */}
        <div className="flex min-w-0 items-baseline gap-1.5 text-xs muted">
          <span className="tnum whitespace-nowrap">{gameTime(game.first_pitch_utc)}</span>
          {game.ballpark.name ? (
            <>
              <span className="subtle">·</span>
              <span className="truncate" title={game.ballpark.name}>
                {game.ballpark.name}
              </span>
            </>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {game.is_final ? <Badge tone="muted">Final</Badge> : null}
          {game.doubleheader && game.doubleheader !== "N" ? (
            <Badge tone="muted">DH</Badge>
          ) : null}
          {prediction ? (
            <Badge tone={RECOMMENDATION_TONE[prediction.recommendation] ?? "neutral"}>
              {RECOMMENDATION_LABEL[prediction.recommendation] ?? prediction.recommendation}
            </Badge>
          ) : null}
        </div>
      </header>

      <div className="flex flex-col gap-2">
        <TeamRow
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
          <ProbabilityBar
            homeProb={prediction.home_win_prob}
            homeLabel={game.home.abbreviation}
            awayLabel={game.away.abbreviation}
          />

          <dl className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <dt className="subtle">Projected</dt>
              <dd className="tnum mt-0.5 font-medium">
                {prediction.projected_score.away_runs !== null
                  ? `${num(prediction.projected_score.away_runs, 1)}–${num(
                      prediction.projected_score.home_runs,
                      1,
                    )}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="subtle">Confidence</dt>
              <dd className="mt-0.5 font-medium">
                {CONFIDENCE_LABEL[prediction.confidence_label]?.replace(
                  " confidence",
                  "",
                ) ?? prediction.confidence_label}
                <span className="ml-1 tnum subtle">
                  {pct(prediction.confidence_score, 0)}
                </span>
              </dd>
            </div>
            <div>
              <dt className="subtle">Data</dt>
              <dd className="tnum mt-0.5 font-medium">
                {pct(prediction.data_completeness, 0)}
              </dd>
            </div>
          </dl>

          {prediction.top_drivers.length ? (
            <ul className="flex flex-col gap-1 text-xs">
              {prediction.top_drivers.map((driver) => (
                <li key={driver.feature_key} className="flex items-baseline gap-2">
                  <span
                    className="tnum w-12 shrink-0 text-right font-medium"
                    style={{ color: homeFavored ? "var(--home)" : "var(--away)" }}
                  >
                    +{driver.contribution_pp.toFixed(1)}
                  </span>
                  <span className="truncate muted">{driver.display_name}</span>
                </li>
              ))}
            </ul>
          ) : null}

          {(game.lineup_status === "UNAVAILABLE" ||
            game.bullpen_warning ||
            prediction.warnings.some((w) => w.severity === "high")) && (
            <ul className="flex flex-col gap-1 text-[0.7rem]">
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
        <p className="rounded border border-dashed p-3 text-xs muted">
          {game.prediction_unavailable?.reason ??
            "No prediction has been generated for this game."}
        </p>
      )}

      <footer className="hairline flex items-center justify-between pt-2.5 text-[0.7rem] subtle">
        <span>
          {prediction ? `Updated ${timestamp(prediction.created_at)}` : "Not predicted"}
        </span>
        <Link
          href={`/game/${game.game_id}`}
          className="font-medium hover:underline"
          style={{ color: "var(--accent)" }}
        >
          Full breakdown →
        </Link>
      </footer>
    </article>
  );
}
