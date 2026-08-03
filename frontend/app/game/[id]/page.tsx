import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/Badge";
import { LiveGameBadge } from "@/components/LiveGameBadge";
import { DriverList } from "@/components/DriverList";
import { FeatureTable } from "@/components/FeatureTable";
import { FreshnessStrip } from "@/components/FreshnessStrip";
import { MatchupBars } from "@/components/MatchupBars";
import { MatchupSummary } from "@/components/MatchupSummary";
import { ParkDimensions } from "@/components/ParkDimensions";
import { TeamStandingBlock } from "@/components/TeamStandingBlock";
import { ProbabilityBar } from "@/components/ProbabilityBar";
import { TrendLine } from "@/components/TrendLine";
import { Section, StatBlock } from "@/components/StatBlock";
import { TabPanels } from "@/components/TabPanels";
import type { TabDef } from "@/components/Tabs";
import { InfoIcon, Tooltip } from "@/components/Tooltip";
import { UnavailableNotice } from "@/components/UnavailableNotice";
import { api } from "@/lib/api";
import {
  CONFIDENCE_LABEL,
  RECOMMENDATION_LABEL,
  gameTime,
  humanizeKey,
  longDate,
  moneyline,
  num,
  pct,
  record,
  signedPp,
  timestamp,
} from "@/lib/format";
import type { ChangeAttribution, GameDetail } from "@/lib/types";
import { buildDates } from "@/lib/window";

/**
 * A page per game in the published window.
 *
 * These ids come from the same slates the date pages are built from, so a card
 * on a built date always has a detail page behind it. `dynamicParams = false`
 * makes that a build error rather than a runtime 404 if the two ever disagree.
 *
 * Slates are fetched in parallel: fifteen dates fetched one after another is
 * fifteen round trips to a service that may be waking up, and the build waits
 * for all of them either way.
 */
export async function generateStaticParams() {
  const slates = await Promise.all(buildDates().map((date) => api.games(date)));
  const ids = new Set<number>();
  for (const slate of slates) {
    if (!slate.ok) continue;
    for (const game of slate.data.games) ids.add(game.game_id);
  }
  return [...ids].map((id) => ({ id: String(id) }));
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

      <TabPanels
        tabs={TABS}
        basePath={`/game/${card.game_id}`}
        panels={{
          prediction: <PredictionTab detail={detail} />,
          pitchers: <PitchersTab detail={detail} />,
          lineups: <LineupsTab detail={detail} />,
          bullpens: <BullpensTab detail={detail} />,
          history: <HistoryTab detail={detail} />,
          environment: <EnvironmentTab detail={detail} />,
          explanation: <ExplanationTab detail={detail} />,
          simulation: <SimulationTab detail={detail} />,
          market: <MarketTab detail={detail} />,
          backtest: <BacktestTab detail={detail} />,
        }}
      />

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

/**
 * A starter's identity block: monogram, name, and the two facts that matter
 * before first pitch. The monogram is initials in the side's tone — presence
 * without pretending to have a photo we don't ingest.
 */
function StarterIdentity({
  starter,
  tone,
}: {
  starter: GameDetail["home_detail"]["starter"];
  tone: "home" | "away";
}) {
  const name = starter.full_name ?? "";
  const initials = name
    .split(/\s+/)
    .map((word) => word[0] ?? "")
    .slice(0, 2)
    .join("")
    .toUpperCase();
  const status = starter.status
    ? starter.status.charAt(0) + starter.status.slice(1).toLowerCase()
    : null;
  return (
    <div className="flex min-w-0 items-center gap-3.5">
      <span
        aria-hidden
        className="grid size-12 shrink-0 place-items-center rounded-full text-sm font-bold"
        style={{
          background: `color-mix(in srgb, var(--${tone}) 13%, transparent)`,
          color: `var(--${tone})`,
          boxShadow: `inset 0 0 0 1.5px color-mix(in srgb, var(--${tone}) 30%, transparent)`,
          letterSpacing: "0.02em",
        }}
      >
        {initials}
      </span>
      <div className="min-w-0">
        <p
          className="truncate text-[1.0625rem] font-semibold"
          style={{ letterSpacing: "-0.015em" }}
        >
          {name}
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <Badge tone="muted">
            Throws {starter.pitch_hand ?? "—"}
          </Badge>
          {status ? <Badge tone="muted">{status}</Badge> : null}
        </div>
      </div>
    </div>
  );
}

function PitchersTab({ detail }: { detail: GameDetail }) {
  const { card, home_detail, away_detail } = detail;
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2">
        {[away_detail, home_detail].map((side, index) => (
          <Section
            key={side.team.id}
            title={`${side.team.name} starter`}
            description={index === 0 ? "Away" : "Home"}
          >
            {side.starter.full_name ? (
              <StarterIdentity
                starter={side.starter}
                tone={index === 0 ? "away" : "home"}
              />
            ) : (
              <UnavailableNotice
                compact
                title="Starter not announced"
                reason="The model uses a replacement-level starter prior in its place, and data completeness drops accordingly."
                requiredSource="MLB Stats API probable pitchers"
              />
            )}
          </Section>
        ))}
      </div>
      <Section
        title="Starter comparison"
        description="Every value is computed from the pitcher's own dated game log, strictly before first pitch, and shrunk toward the league rate when the sample is small."
      >
        <FeatureTable
          home={home_detail.starter_stats}
          away={away_detail.starter_stats}
          homeLabel={card.home.abbreviation}
          awayLabel={card.away.abbreviation}
          emptyMessage="No starter metrics are available for this matchup."
        />
      </Section>
    </div>
  );
}

function LineupsTab({ detail }: { detail: GameDetail }) {
  const { card, home_detail, away_detail } = detail;
  const deferred = detail.deferred_features["lineups"] ?? [];
  return (
    <div className="flex flex-col gap-4">
      <UnavailableNotice
        title="Confirmed batting orders are not available"
        reason={
          card.lineup_status_reason ??
          "Pregame lineups require the Phase 2 lineup poller."
        }
        requiredSource="LINEUP_PROVIDER"
        phase={2}
      />
      <Section
        title="Team offense"
        description="Team-level offense computed from real per-game box-score lines. Player-level, plate-appearance-weighted lineup strength requires the lineup feed and Statcast, and is reported as unavailable rather than approximated."
      >
        <FeatureTable
          home={home_detail.offense}
          away={away_detail.offense}
          homeLabel={card.home.abbreviation}
          awayLabel={card.away.abbreviation}
          emptyMessage="No offensive metrics are available."
        />
      </Section>
      {deferred.length ? (
        <Section
          title="Lineup features planned for Phase 2"
          description="These are registered in the feature dictionary and will populate once the lineup feed is enabled."
        >
          <ul className="flex flex-col gap-2 text-xs">
            {deferred.map((f) => (
              <li key={f.key}>
                <span className="font-medium">{f.display_name}</span>
                <span className="muted"> — {f.description}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

function BullpensTab({ detail }: { detail: GameDetail }) {
  const { card, home_detail, away_detail } = detail;
  const deferred = detail.deferred_features["bullpen_availability"] ?? [];
  return (
    <div className="flex flex-col gap-4">
      <Section
        title="Bullpen quality and recent workload"
        description="Usage and fatigue are observable from ingested relief-appearance game logs, so they are real. Per-pitcher availability — closer rested, arm unavailable — is a separate feed and stays unavailable until Phase 2."
      >
        <FeatureTable
          home={home_detail.bullpen}
          away={away_detail.bullpen}
          homeLabel={card.home.abbreviation}
          awayLabel={card.away.abbreviation}
          emptyMessage="No bullpen metrics are available."
        />
      </Section>
      <UnavailableNotice
        title="Per-pitcher availability is not available"
        reason="Closer and setup availability, consecutive-day tracking and handedness availability require a bullpen availability provider."
        requiredSource="BULLPEN_AVAILABILITY_PROVIDER"
        phase={2}
      />
      {deferred.length ? (
        <Section title="Availability features planned for Phase 2">
          <ul className="flex flex-col gap-2 text-xs">
            {deferred.map((f) => (
              <li key={f.key}>
                <span className="font-medium">{f.display_name}</span>
                <span className="muted"> — {f.description}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

function HistoryTab({ detail }: { detail: GameDetail }) {
  const history = detail.matchup_history as {
    available?: boolean;
    reason?: string;
    season_series_shrunk_diff?: number | null;
    sample_size?: number | null;
    note?: string;
    batter_vs_pitcher?: { available: boolean; reason: string };
  };
  return (
    <div className="flex flex-col gap-4">
      <Section
        title="Season series"
        description={history.note}
      >
        {history.available ? (
          <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4">
            <StatBlock
              label="Shrunk series edge"
              value={num(history.season_series_shrunk_diff, 3)}
              sub="Positive favors the home team"
            />
            <StatBlock
              label="Games played"
              value={history.sample_size ?? 0}
              sub="Sample behind the split"
            />
          </dl>
        ) : (
          <p className="text-sm muted">{history.reason}</p>
        )}
      </Section>
      <UnavailableNotice
        title="Batter versus pitcher history is not available"
        reason={
          history.batter_vs_pitcher?.reason ??
          "Batter-versus-pitcher history requires play-by-play ingestion."
        }
        phase={3}
      />
      <Section title="Prediction history for this game">
        {detail.prediction_history.length >= 2 ? (
          <div className="mb-4">
            <TrendLine
              points={detail.prediction_history.map((row) => ({
                t: row.as_of,
                v: row.home_win_prob,
              }))}
              ariaLabel={`${detail.card.home.abbreviation} win probability across ${detail.prediction_history.length} issued predictions`}
            />
            <p className="t-micro mt-1 subtle">
              {detail.card.home.abbreviation} win probability by issue time ·
              dashed line marks 50% · spacing is real elapsed time
            </p>
          </div>
        ) : null}
        {detail.prediction_history.length ? (
          <div className="scroll-x edge-cue">
            <table className="data sticky-label min-w-[360px]">
              <thead>
                <tr>
                  <th scope="col">As of</th>
                  <th scope="col" className="num">
                    Home win prob
                  </th>
                  <th scope="col" className="num">
                    Confidence
                  </th>
                  <th scope="col" className="num">
                    Completeness
                  </th>
                  <th scope="col">Label</th>
                </tr>
              </thead>
              <tbody>
                {detail.prediction_history.map((row) => (
                  <tr key={row.as_of}>
                    <th scope="row" className="font-normal">
                      {timestamp(row.as_of)}
                      {row.is_latest ? (
                        <span className="ml-1.5">
                          <Badge tone="accent">latest</Badge>
                        </span>
                      ) : null}
                    </th>
                    <td className="num tnum">{pct(row.home_win_prob)}</td>
                    <td className="num tnum">{pct(row.confidence_score, 0)}</td>
                    <td className="num tnum">{pct(row.data_completeness, 0)}</td>
                    <td>{RECOMMENDATION_LABEL[row.recommendation] ?? row.recommendation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm muted">No predictions have been issued yet.</p>
        )}
      </Section>
    </div>
  );
}

function EnvironmentTab({ detail }: { detail: GameDetail }) {
  const env = detail.environment as {
    ballpark: Record<string, unknown>;
    is_dome: number | null;
    elevation_km: number | null;
    weather: { status: string; summary: string | null; reason: string | null };
    park_factors: { available: boolean; reason: string };
    umpire: { available: boolean; reason: string };
  };
  const park = detail.card.ballpark;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
      <Section title="Ballpark" description="Physical attributes, which are static and genuinely available.">
        {park.lf_line && park.center && park.rf_line ? (
          /* The wall drawn to scale says more than three numbers in a row —
             the numbers are still on the drawing, at the points they measure. */
          <div className="mb-4 flex justify-center">
            <ParkDimensions lf={park.lf_line} cf={park.center} rf={park.rf_line} />
          </div>
        ) : null}
        <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 sm:grid-cols-3">
          <StatBlock label="Venue" value={park.name ?? "—"} sub={park.city ?? undefined} />
          <StatBlock label="Roof" value={park.roof_type ?? "—"} />
          <StatBlock
            label="Elevation"
            value={park.elevation_ft !== null ? `${park.elevation_ft} ft` : "—"}
          />
          {!(park.lf_line && park.center && park.rf_line) ? (
            <>
              <StatBlock label="LF line" value={park.lf_line ? `${park.lf_line}′` : "—"} />
              <StatBlock label="Center" value={park.center ? `${park.center}′` : "—"} />
              <StatBlock label="RF line" value={park.rf_line ? `${park.rf_line}′` : "—"} />
            </>
          ) : null}
          <StatBlock label="Surface" value={park.turf_type ?? "—"} />
          <StatBlock
            label="Capacity"
            value={park.capacity ? park.capacity.toLocaleString() : "—"}
          />
          <StatBlock label="Day / night" value={detail.card.day_night ?? "—"} />
        </dl>
      </Section>

      <Section title="Weather">
        {env.weather.status === "OBSERVED" && env.weather.summary ? (
          <div>
            <p className="text-sm">{env.weather.summary}</p>
            <p className="mt-1 text-xs subtle">
              Observed conditions recorded by the source for this game. Forecast
              weather features are a Phase 2 addition and are not used by the
              active model.
            </p>
          </div>
        ) : (
          <UnavailableNotice
            compact
            title="Weather is not available"
            reason={env.weather.reason ?? "No weather provider is configured."}
            requiredSource="WEATHER_PROVIDER"
            phase={2}
          />
        )}
      </Section>

      <Section title="Park factors">
        <UnavailableNotice
          compact
          title="Empirical park factors are not available"
          reason={env.park_factors.reason}
          phase={2}
        />
      </Section>

      <Section title="Umpire">
        <UnavailableNotice
          compact
          title="Umpire strike-zone profile is not available"
          reason={env.umpire.reason}
          phase={2}
        />
      </Section>
    </div>
  );
}

function ExplanationTab({ detail }: { detail: GameDetail }) {
  const prediction = detail.card.prediction;
  // Bars are scaled to the largest contribution so relative weight is read
  // straight off the column without comparing printed numbers.
  const maxPp = Math.max(
    ...detail.all_drivers.map((d) => d.contribution_pp),
    0.01,
  );
  return (
    <div className="flex flex-col gap-4">
      <Section
        title="Every measured contribution"
        description="Exact leave-one-out effect of each model input, in probability points. Contributions are additive in log-odds and sum with the intercept to the final probability."
      >
        {detail.all_drivers.length ? (
          <div className="scroll-x edge-cue">
            <table className="data sticky-label min-w-[460px]">
              <thead>
                <tr>
                  <th scope="col">Input</th>
                  <th scope="col">Category</th>
                  <th scope="col">Favors</th>
                  <th scope="col" className="num">
                    Points
                  </th>
                  <th scope="col" className="num">
                    Value
                  </th>
                  <th scope="col" className="num">
                    Sample
                  </th>
                </tr>
              </thead>
              <tbody>
                {detail.all_drivers.map((d) => (
                  <tr key={d.feature_key}>
                    <th scope="row" className="font-normal">
                      {d.display_name}
                      {d.is_estimated ? (
                        <span className="ml-1.5 text-[0.65rem] subtle">est</span>
                      ) : null}
                    </th>
                    <td className="muted">{d.category_label}</td>
                    <td>
                      <Badge tone={d.favors === "H" ? "home" : "away"}>
                        {d.favors === "H"
                          ? detail.card.home.abbreviation
                          : detail.card.away.abbreviation}
                      </Badge>
                    </td>
                    <td className="num tnum">
                      <span className="inline-flex items-center gap-2">
                        <span
                          aria-hidden
                          className="inline-block h-[5px] w-14 overflow-hidden rounded-full"
                          style={{ background: "var(--track)" }}
                        >
                          <span
                            className="block h-full rounded-full"
                            style={{
                              width: `${(d.contribution_pp / maxPp) * 100}%`,
                              background:
                                d.favors === "H"
                                  ? "linear-gradient(to right, var(--home), var(--home-hi))"
                                  : "linear-gradient(to right, var(--away), var(--away-hi))",
                            }}
                          />
                        </span>
                        +{d.contribution_pp.toFixed(2)}
                      </span>
                    </td>
                    <td className="num tnum subtle">{d.feature_display ?? "—"}</td>
                    <td className="num tnum subtle">{d.sample_size ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm muted">No contributions recorded.</p>
        )}
      </Section>

      {prediction ? (
        <Section
          title="Model internals"
          description="Everything needed to reproduce this prediction exactly."
        >
          <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 sm:grid-cols-4">
            <StatBlock
              label="Model"
              value={
                prediction.model_name
                  ? `${prediction.model_name}:${prediction.model_version}`
                  : "—"
              }
            />
            <StatBlock label="As of" value={timestamp(prediction.as_of)} />
            <StatBlock
              label="Uncalibrated"
              value={pct(prediction.home_win_prob_uncalibrated)}
              sub={`calibrated ${pct(prediction.home_win_prob)}`}
            />
            <StatBlock
              label="Components"
              value={Object.keys(prediction.component_probs).length}
              sub={Object.entries(prediction.component_probs)
                .map(([k, v]) => `${k.replace(/_/g, " ")} ${pct(v)}`)
                .join(" · ")}
            />
          </dl>
        </Section>
      ) : null}
    </div>
  );
}

/**
 * One row of the run distribution. The bar is scaled to the *most likely* run
 * total rather than to 100%, because every bar would otherwise be a stub — no
 * single run total carries much more than a fifth of the probability, and a
 * chart where nothing is visible communicates nothing.
 */
function RunBar({
  runs,
  probability,
  peak,
  isTail,
  tone,
}: {
  runs: number;
  probability: number;
  peak: number;
  isTail: boolean;
  tone: "home" | "away";
}) {
  const width = peak > 0 ? Math.max(2, (probability / peak) * 100) : 0;
  // The modal total is the answer a reader takes away; it gets the ink.
  const isPeak = peak > 0 && probability === peak;
  const emphasis = isPeak ? { color: "var(--text)", fontWeight: 650 } : undefined;
  return (
    <div className="flex items-center gap-2">
      <span
        className={`numeral w-7 shrink-0 text-right text-xs${isPeak ? "" : " muted"}`}
        style={emphasis}
      >
        {isTail ? `${runs}+` : runs}
      </span>
      <div
        className="h-2.5 min-w-0 flex-1 overflow-hidden rounded-full"
        style={{ background: "var(--surface-sunken)" }}
      >
        <div
          className="h-full rounded-full"
          style={{
            width: `${width}%`,
            // Brightening toward the leading edge — the same gradient language
            // as the probability meters on the slate cards.
            background:
              tone === "home"
                ? "linear-gradient(to right, var(--home), var(--home-hi))"
                : "linear-gradient(to right, var(--away), var(--away-hi))",
            opacity: isPeak ? 1 : 0.82,
          }}
        />
      </div>
      <span
        className={`numeral w-11 shrink-0 text-right text-xs${isPeak ? "" : " muted"}`}
        style={emphasis}
      >
        {pct(probability, 1)}
      </span>
    </div>
  );
}

function RunDistribution({
  label,
  distribution,
  maxReported,
  tone,
}: {
  label: string;
  distribution: number[];
  maxReported: number | null;
  tone: "home" | "away";
}) {
  if (distribution.length === 0) return null;
  const peak = Math.max(...distribution);
  const tailIndex = maxReported ?? distribution.length - 1;
  return (
    <div className="min-w-0">
      <p className="eyebrow mb-2">{label}</p>
      <div className="flex flex-col gap-1.5">
        {distribution.map((probability, runs) => (
          <RunBar
            key={runs}
            runs={runs}
            probability={probability}
            peak={peak}
            isTail={runs >= tailIndex}
            tone={tone}
          />
        ))}
      </div>
    </div>
  );
}

function SimulationTab({ detail }: { detail: GameDetail }) {
  const simulation = detail.simulation;
  if (!simulation.available) {
    return (
      <UnavailableNotice
        title="This game was not simulated"
        reason={simulation.reason}
        phase={simulation.phase ?? null}
      />
    );
  }

  const card = detail.card;
  const homeName = card.home.abbreviation ?? card.home.name;
  const awayName = card.away.abbreviation ?? card.away.name;

  return (
    <div className="flex flex-col gap-4">
      <Section
        title="Simulated win probability"
        description={
          simulation.blended_with_logistic
            ? `${simulation.n_simulations.toLocaleString()} seeded games. The served probability is this blended with the logistic model in log-odds, at a weight fixed in advance.`
            : `${simulation.n_simulations.toLocaleString()} seeded games.`
        }
      >
        <ProbabilityBar
          homeProb={simulation.home_win_pct}
          homeLabel={homeName}
          awayLabel={awayName}
        />
        {simulation.blended_with_logistic && card.prediction ? (
          <dl className="mt-4 grid grid-cols-2 gap-4">
            <StatBlock
              label="Simulation alone"
              value={pct(simulation.home_win_pct)}
              sub={`${homeName} to win`}
            />
            <StatBlock
              label="Served (blended)"
              value={pct(card.prediction.home_win_prob)}
              sub={`weight ${num(simulation.blend_weight, 2)} on the simulation`}
              tone="accent"
            />
          </dl>
        ) : null}
      </Section>

      <Section
        title="How the game goes"
        description="Read off the same simulated games as the probability above, so these agree with it by construction."
      >
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatBlock
            label="Extra innings"
            value={pct(simulation.extra_innings_prob)}
            sub="tied after nine"
          />
          <StatBlock
            label="One-run game"
            value={pct(simulation.one_run_prob)}
            sub="decided by a single run"
          />
          <StatBlock
            label="Mean runs"
            value={`${num(simulation.mean_away_runs, 2)} – ${num(simulation.mean_home_runs, 2)}`}
            sub={`${awayName} – ${homeName}`}
          />
          <StatBlock
            label="Underdog wins"
            value={pct(simulation.upset_prob)}
            sub="against the simulation's own pick"
          />
        </dl>
      </Section>

      <Section
        title="Run distribution"
        description={
          simulation.max_reported_runs !== null
            ? `Chance of each run total. The final row pools ${simulation.max_reported_runs} runs or more.`
            : "Chance of each run total."
        }
      >
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <RunDistribution
            label={`${awayName} runs`}
            distribution={simulation.away_run_distribution}
            maxReported={simulation.max_reported_runs}
            tone="away"
          />
          <RunDistribution
            label={`${homeName} runs`}
            distribution={simulation.home_run_distribution}
            maxReported={simulation.max_reported_runs}
            tone="home"
          />
        </div>
      </Section>

      {simulation.likely_scores.length > 0 ? (
        <Section
          title="Most likely finals"
          description={
            simulation.likely_scores_covered !== null
              ? `These account for ${pct(simulation.likely_scores_covered)} of simulated games. Baseball's score distribution is long-tailed — the rest is spread across scores too rare to list.`
              : undefined
          }
        >
          <ul className="flex flex-col gap-2">
            {simulation.likely_scores.map((score, index) => (
              <li
                key={`${score.away}-${score.home}`}
                className="flex items-center justify-between gap-3 border-b pb-2 last:border-b-0 last:pb-0"
                style={{ borderColor: "var(--border)" }}
              >
                {/* min-w-0 + truncate: a team with no abbreviation falls back to
                    its full name, and two long names would otherwise set this
                    row's minimum width and push the page sideways on a phone. */}
                <span
                  className="numeral min-w-0 truncate text-sm"
                  style={index === 0 ? { fontWeight: 650 } : undefined}
                >
                  {awayName} {score.away} – {score.home} {homeName}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  {index === 0 ? (
                    <span
                      className="t-micro rounded-full px-2 py-0.5"
                      style={{
                        background: "var(--accent-soft)",
                        color: "var(--accent)",
                        fontWeight: 580,
                      }}
                    >
                      most likely
                    </span>
                  ) : null}
                  <span
                    className={`numeral text-sm${index === 0 ? "" : " muted"}`}
                    style={index === 0 ? { fontWeight: 650 } : undefined}
                  >
                    {pct(score.probability)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

function MarketTab({ detail }: { detail: GameDetail }) {
  const market = detail.market;
  const prediction = detail.card.prediction;
  return (
    <div className="flex flex-col gap-4">
      <Section
        title="Model fair price"
        description="The fair moneyline implied by the model probability, with no margin applied."
      >
        {prediction ? (
          <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4">
            <StatBlock
              label={`${detail.card.away.abbreviation} fair line`}
              value={moneyline(market.fair_away_moneyline)}
              sub={pct(prediction.away_win_prob)}
              tone="away"
            />
            <StatBlock
              label={`${detail.card.home.abbreviation} fair line`}
              value={moneyline(market.fair_home_moneyline)}
              sub={pct(prediction.home_win_prob)}
              tone="home"
            />
          </dl>
        ) : (
          <p className="text-sm muted">No prediction available.</p>
        )}
      </Section>
      <UnavailableNotice
        title="Market comparison is not available"
        reason={
          market.reason ??
          "Market comparison requires a licensed odds provider."
        }
        requiredSource="ODDS_PROVIDER"
        phase={3}
      />
    </div>
  );
}

function BacktestTab({ detail }: { detail: GameDetail }) {
  const evidence = detail.backtest_evidence;
  return (
    <div className="flex flex-col gap-4">
      <Section
        title="How reliable have similar predictions been?"
        description="Historical accuracy of predictions in the same probability band, from the most recent walk-forward backtest."
      >
        {evidence.available ? (
          <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 sm:grid-cols-4">
            <StatBlock label="Probability band" value={`${evidence.band}%`} />
            <StatBlock label="Games in band" value={evidence.n ?? 0} />
            <StatBlock
              label="Predicted"
              value={pct(evidence.predicted)}
              sub="Average model probability"
            />
            <StatBlock
              label="Observed"
              value={pct(evidence.observed)}
              sub="Actual win frequency"
              tone={
                evidence.observed !== null &&
                evidence.predicted !== null &&
                Math.abs(evidence.observed - evidence.predicted) < 0.03
                  ? "home"
                  : undefined
              }
            />
          </dl>
        ) : (
          <p className="text-sm muted">
            {evidence.reason ??
              "No band-level historical reliability is available for this prediction."}
          </p>
        )}
      </Section>

      <Section
        title="Overall backtest"
        description="Walk-forward evaluation across the full history. Calibration and proper scoring rules rank above accuracy."
      >
        <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 sm:grid-cols-4">
          <StatBlock
            label="Games evaluated"
            value={evidence.overall_n?.toLocaleString() ?? "—"}
          />
          <StatBlock
            label="Log loss"
            value={num(evidence.overall_log_loss, 4)}
            sub="Lower is better · 0.6931 = coin flip"
          />
          <StatBlock label="Brier score" value={num(evidence.overall_brier, 4)} />
          <StatBlock
            label="Calibration error"
            value={
              evidence.overall_calibration_error !== null
                ? pct(evidence.overall_calibration_error, 2)
                : "—"
            }
          />
        </dl>
        <p className="mt-3 text-xs muted">
          <Link href="/backtest" className="font-medium hover:underline" style={{ color: "var(--accent)" }}>
            See the full backtest report →
          </Link>
        </p>
      </Section>
    </div>
  );
}
