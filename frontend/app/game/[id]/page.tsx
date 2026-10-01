import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/Badge";
import { LiveGameBadge } from "@/components/LiveGameBadge";
import { DriverList } from "@/components/DriverList";
import { FreshnessStrip } from "@/components/FreshnessStrip";
import { GameTabPanels } from "@/components/GameTabPanels";
import { MatchupBars } from "@/components/MatchupBars";
import { MatchupSummary } from "@/components/MatchupSummary";
import { TeamStandingBlock } from "@/components/TeamStandingBlock";
import { ProbabilityBar } from "@/components/ProbabilityBar";
import { Section, StatBlock } from "@/components/StatBlock";
import type { TabDef } from "@/components/Tabs";
import { UnavailableNotice } from "@/components/UnavailableNotice";
import { api } from "@/lib/api";
import { asset } from "@/lib/asset";
import {
  CONFIDENCE_LABEL,
  RECOMMENDATION_LABEL,
  gameTime,
  humanizeKey,
  longDate,
  num,
  pct,
  record,
  signedPp,
  timestamp,
} from "@/lib/format";
import { builtGameIds } from "@/lib/games";
import type { ChangeAttribution, GameDetail } from "@/lib/types";

/**
 * A page per game in the published window.
 *
 * These ids come from the same slates the date pages are built from, so a card
 * on a built date always has a detail page behind it. `dynamicParams = false`
 * makes that a build error rather than a runtime 404 if the two ever disagree.
 * The game's detail file (`detail.json/route.ts` beside this) is built for the
 * same ids, from the same helper.
 *
 * The page pre-renders the header and the Prediction tab. The other nine tabs
 * are rendered in the browser from the detail file when opened
 * (`components/GameTabs.tsx`, `components/GameTabPanels.tsx`): they used to
 * be the larger part of three hundred kilobytes a game, written into the HTML
 * and again into the hydration payload, and most readers never open them.
 */
export function generateStaticParams() {
  return builtGameIds();
}

/**
 * Every game page is built ahead of time, and an id with no page is a 404.
 *
 * Next requires a literal here — an expression is rejected as an invalid
 * segment config — so this cannot vary by build mode, and `false` is what the
 * static export needs.
 *
 * The consequence worth stating: if the API cannot be reached while the site is
 * being built, `generateStaticParams` yields nothing and NO game pages are
 * produced, leaving every "Full breakdown" link on an otherwise healthy-looking
 * site pointing at a 404. The publish workflow counts the game pages and fails
 * rather than deploying that.
 */
export const dynamicParams = false;


const TABS: TabDef[] = [
  { key: "prediction", label: "Prediction", shortLabel: "Prediction" },
  { key: "pitchers", label: "Starting pitchers", shortLabel: "Pitchers" },
  { key: "lineups", label: "Lineups & batting", shortLabel: "Lineups" },
  { key: "bullpens", label: "Bullpens", shortLabel: "Bullpens" },
  { key: "history", label: "Matchup history", shortLabel: "History" },
  { key: "environment", label: "Weather & ballpark", shortLabel: "Ballpark" },
  { key: "explanation", label: "Model explanation", shortLabel: "Explain" },
  { key: "simulation", label: "Simulation", shortLabel: "Sim" },
  { key: "market", label: "Market comparison", shortLabel: "Market" },
  { key: "backtest", label: "Backtest evidence", shortLabel: "Backtest" },
];

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const result = await api.game(id);
  if (!result.ok) return { title: "Game" };
  const { away, home } = result.data.card;
  return { title: `${away.abbreviation} @ ${home.abbreviation}` };
}

export default async function GameDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  const result = await api.game(id);
  if (!result.ok) {
    if (result.status === 404) notFound();
    return (
      <UnavailableNotice
        title="Could not load this game"
        reason={result.message}
        requiredSource="backend at API_BASE_URL"
      />
    );
  }

  const detail = result.data;
  const { card } = detail;
  const prediction = card.prediction;
  const homeLabel = card.home.abbreviation;
  const awayLabel = card.away.abbreviation;

  return (
    <div className="flex flex-col gap-4 sm:gap-5">
      <Link
        href={`/d/${card.official_date}/`}
        className="pill tap t-small group -my-1 gap-1.5 self-start px-3"
      >
        <span
          aria-hidden
          className="inline-block transition-transform group-hover:-translate-x-0.5"
        >
          ←
        </span>
        {longDate(card.official_date)}
      </Link>

      <header className="card flex min-w-0 flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="t-title">
              {card.away.name}{" "}
              <span className="subtle" style={{ fontWeight: 400 }}>
                at
              </span>{" "}
              {card.home.name}
            </h1>
            <p className="t-small mt-1.5 muted">
              <span className="tnum">{gameTime(card.first_pitch_utc)}</span>
              {card.ballpark.name ? ` · ${card.ballpark.name}` : ""}
              {card.ballpark.city ? `, ${card.ballpark.city}` : ""}
              {card.day_night ? ` · ${card.day_night} game` : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <LiveGameBadge
              gameId={card.game_id}
              date={card.official_date}
              firstPitch={card.first_pitch_utc}
              isFinal={card.is_final}
            />
            {card.is_final ? (
              <Badge tone="muted">
                Final {card.away_score}–{card.home_score}
              </Badge>
            ) : (
              <Badge tone="muted">{card.status_detail ?? card.status}</Badge>
            )}
            {prediction ? (
              <Badge tone="accent">
                {RECOMMENDATION_LABEL[prediction.recommendation] ??
                  prediction.recommendation}
              </Badge>
            ) : null}
          </div>
        </div>

        {prediction ? (
          <>
            <ProbabilityBar
              homeProb={prediction.home_win_prob}
              homeLabel={`${homeLabel}${record(card.home.wins, card.home.losses) ? ` (${record(card.home.wins, card.home.losses)})` : ""}`}
              awayLabel={`${awayLabel}${record(card.away.wins, card.away.losses) ? ` (${record(card.away.wins, card.away.losses)})` : ""}`}
            />
            <hr className="rule-soft" />
            <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-x-4 gap-y-5 sm:grid-cols-4">
              <StatBlock
                label="Projected score"
                value={
                  prediction.projected_score.away_runs !== null
                    ? `${num(prediction.projected_score.away_runs, 1)} – ${num(prediction.projected_score.home_runs, 1)}`
                    : "—"
                }
                sub={
                  prediction.projected_score.away_low !== null
                    ? `Range ${prediction.projected_score.away_low}–${prediction.projected_score.away_high} vs ${prediction.projected_score.home_low}–${prediction.projected_score.home_high}`
                    : "Requires more scoring history"
                }
              />
              <StatBlock
                label="Confidence"
                value={pct(prediction.confidence_score, 0)}
                sub={
                  CONFIDENCE_LABEL[prediction.confidence_label] ??
                  prediction.confidence_label
                }
              />
              <StatBlock
                label="Data completeness"
                value={pct(prediction.data_completeness, 0)}
                sub={
                  prediction.missing_data.length
                    ? `Not available: ${prediction.missing_data.join(", ")}`
                    : "Every input this model consumes was available"
                }
              />
              <StatBlock
                label="Model agreement"
                value={
                  prediction.model_agreement !== null
                    ? pct(prediction.model_agreement, 0)
                    : "—"
                }
                sub={
                  prediction.model_agreement !== null
                    ? "Calibrated model vs. Elo reference"
                    : "Single model — no agreement signal"
                }
              />
            </dl>
          </>
        ) : (
          <UnavailableNotice
            title="No prediction for this game"
            reason={
              card.prediction_unavailable?.reason ??
              "No prediction has been generated yet."
            }
            requiredSource={card.prediction_unavailable?.required_source}
          />
        )}
      </header>

      {/* The Prediction tab is pre-rendered here; the rest render from the
          detail file when opened. The file's URL carries the base path, which
          only the server knows. */}
      <GameTabPanels
        tabs={TABS}
        basePath={`/game/${card.game_id}`}
        detailUrl={asset(`/game/${card.game_id}/detail.json`)}
      >
        <PredictionTab detail={detail} />
      </GameTabPanels>

      <section className="surface px-4 py-3" aria-label="Data freshness">
        <p className="eyebrow mb-2">Data freshness by source</p>
        <FreshnessStrip entries={detail.freshness} />
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ tabs */

function PredictionTab({ detail }: { detail: GameDetail }) {
  const { card } = detail;
  const prediction = card.prediction;
  if (!prediction) {
    return (
      <UnavailableNotice
        title="No prediction to explain"
        reason="Generate a prediction for this game to populate this tab."
      />
    );
  }
  const favored =
    prediction.predicted_winner === "HOME" ? card.home : card.away;
  const opponent = prediction.predicted_winner === "HOME" ? card.away : card.home;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
      <Section
        title="At a glance"
        description="The same nine rows for every game, so two games can be compared line by line. Rows marked as context describe the matchup but carry no probability weight."
      >
        <MatchupSummary rows={detail.matchup_summary} />
      </Section>

      <Section
        title="Standings, splits and streaks"
        description="Derived from ingested results under the same as-of cut the model uses, so these agree with the prediction beside them. Context only — see Methodology for why streak length is deliberately not a model input."
      >
        <div className="flex flex-col gap-4">
          <TeamStandingBlock team={card.away} isHome={false} />
          <TeamStandingBlock team={card.home} isHome />
        </div>
      </Section>

      <Section
        title={`Why the model favors ${favored.team_name ?? favored.name}`}
        description={`${favored.name} win probability ${pct(
          prediction.predicted_winner === "HOME"
            ? prediction.home_win_prob
            : prediction.away_win_prob,
        )}. Each factor is the exact number of probability points it contributed.`}
      >
        <DriverList
          drivers={detail.drivers_for}
          tone={prediction.predicted_winner === "HOME" ? "home" : "away"}
          emptyMessage="No positive contributions were recorded."
        />
      </Section>

      <Section
        title={`What argues for ${opponent.team_name ?? opponent.name}`}
        description="The strongest counterweights the model measured, plus the risks that make this prediction less certain."
      >
        <DriverList
          drivers={detail.drivers_against}
          tone={prediction.predicted_winner === "HOME" ? "away" : "home"}
          emptyMessage="No counterweights were recorded."
        />
        {prediction.warnings.length ? (
          <>
            <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide subtle">
              Risks and uncertainty
            </h3>
            <ul className="mt-2 flex flex-col gap-1.5 text-xs">
              {prediction.warnings.map((w) => (
                <li key={w.code} className="flex items-start gap-2">
                  <span
                    aria-hidden
                    className="mt-1.5 inline-block size-1.5 shrink-0 rounded-full"
                    style={{
                      background:
                        w.severity === "high"
                          ? "var(--color-danger-500)"
                          : w.severity === "medium"
                            ? "var(--color-warn-500)"
                            : "var(--text-subtle)",
                    }}
                  />
                  <span className="muted">{w.message}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </Section>

      <Section
        title="Where the advantage sits"
        description="Net probability points by phase of the game."
      >
        <MatchupBars
          bars={detail.matchup_bars}
          homeLabel={card.home.abbreviation}
          awayLabel={card.away.abbreviation}
        />
      </Section>

      <Section
        title="What changed since the previous prediction"
        description="Predictions are immutable snapshots. This compares the current one with the one it superseded."
      >
        <ChangeSummary detail={detail} />
      </Section>
    </div>
  );
}

/**
 * Why the number moved, split three ways.
 *
 * The split is exact rather than attributed — the logistic model and the blend
 * are both linear in log-odds, so these three terms *are* the move rather than
 * an estimate of it. The share bar uses absolute magnitudes because two stages
 * can pull in opposite directions and a signed bar would show one of them as
 * negative width.
 */
function ChangeAttributionPanel({
  attribution,
}: {
  attribution: ChangeAttribution | null;
}) {
  if (!attribution || !attribution.has_previous) return null;
  const { stages, drivers } = attribution;
  const parts = [
    { key: "features", label: "Team form", value: stages.features },
    { key: "calibration", label: "Calibration", value: stages.calibration },
    { key: "simulation", label: "Run simulation", value: stages.simulation },
  ];
  const magnitude = parts.reduce((sum, p) => sum + Math.abs(p.value), 0);
  if (magnitude < 1e-9) return null;

  return (
    <div className="flex flex-col gap-3">
      <div>
        <p className="eyebrow mb-2">What moved it</p>
        <div
          className="flex h-2.5 w-full overflow-hidden rounded-full"
          style={{ background: "var(--surface-sunken)" }}
        >
          {parts.map((part, index) => (
            <div
              key={part.key}
              style={{
                width: `${(Math.abs(part.value) / magnitude) * 100}%`,
                background:
                  index === 0
                    ? "var(--accent)"
                    : index === 1
                      ? "var(--border-strong)"
                      : "var(--home)",
              }}
            />
          ))}
        </div>
        <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          {parts.map((part) => (
            <div key={part.key} className="flex items-baseline gap-1.5">
              <dt className="text-xs muted">{part.label}</dt>
              <dd className="numeral text-xs">
                {((Math.abs(part.value) / magnitude) * 100).toFixed(0)}%
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {attribution.simulation_note ? (
        <p className="t-small muted">{attribution.simulation_note}</p>
      ) : null}

      {drivers.length ? (
        <div>
          <p className="eyebrow mb-2">Inputs that moved it most</p>
          <ul className="flex flex-col gap-1.5">
            {drivers.slice(0, 6).map((driver) => (
              <li
                key={driver.feature_key}
                className="flex items-center justify-between gap-3"
              >
                <span className="min-w-0 truncate text-sm">
                  {driver.display_name}
                </span>
                <span
                  className="numeral shrink-0 text-sm"
                  style={{
                    color:
                      driver.favors === "H" ? "var(--home)" : "var(--away)",
                  }}
                >
                  {signedPp(driver.contribution_pp)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function ChangeSummary({ detail }: { detail: GameDetail }) {
  const change = detail.change_since_previous;
  if (!change.has_previous) {
    return (
      <p className="text-sm muted">
        {change.message ?? "This is the first prediction issued for this game."}
      </p>
    );
  }
  const delta = change.home_win_prob_delta_pp ?? 0;
  return (
    <div className="flex flex-col gap-3">
      <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3">
        <StatBlock
          label="Home probability"
          value={signedPp(delta)}
          sub={`${pct(change.home_win_prob_previous)} → ${pct(change.home_win_prob_current)}`}
          tone={delta > 0 ? "home" : delta < 0 ? "away" : undefined}
        />
        <StatBlock
          label="Confidence"
          value={pct(change.confidence_current, 0)}
          sub={`was ${pct(change.confidence_previous, 0)}`}
        />
        <StatBlock
          label="Inputs changed"
          value={change.n_changed_features ?? 0}
          sub={`as of ${timestamp(change.current_as_of)}`}
        />
      </dl>
      <ChangeAttributionPanel attribution={change.attribution ?? null} />
      {change.changed_features.length ? (
        <div className="scroll-x edge-cue">
          <table className="data sticky-label min-w-[320px]">
            <thead>
              <tr>
                <th scope="col">Input</th>
                <th scope="col" className="num">
                  Previous
                </th>
                <th scope="col" className="num">
                  Current
                </th>
                <th scope="col" className="num">
                  Δ
                </th>
              </tr>
            </thead>
            <tbody>
              {change.changed_features.slice(0, 12).map((row) => (
                <tr key={row.feature_key}>
                  <th scope="row" className="font-normal">
                    {humanizeKey(row.feature_key)}
                  </th>
                  <td className="num tnum">{num(row.previous, 3)}</td>
                  <td className="num tnum">{num(row.current, 3)}</td>
                  <td className="num tnum">{num(row.delta, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
