import { Badge } from "@/components/Badge";
import { CalibrationChart } from "@/components/CalibrationChart";
import { Section, StatBlock } from "@/components/StatBlock";
import { InfoIcon, Tooltip } from "@/components/Tooltip";
import { UnavailableNotice } from "@/components/UnavailableNotice";
import { api } from "@/lib/api";
import { num, pct, timestamp } from "@/lib/format";
import type { BacktestSlice } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata = { title: "Backtest" };

const SLICE_TITLES: Record<string, { title: string; description: string }> = {
  season: { title: "By season", description: "Out-of-sample performance year by year." },
  month: { title: "By month", description: "Seasonality in model performance." },
  probability_band: {
    title: "By probability band",
    description:
      "The direct answer to “how reliable have similar predictions been?” Predicted probability against the frequency the favorite actually won.",
  },
  favorite_underdog: {
    title: "Favorite and underdog",
    description: "Split by whether the model favored the home team.",
  },
  home_away: { title: "Home and away", description: "Which side the model favored." },
  starter_quality: {
    title: "By starting pitcher quality",
    description: "Quartiles of the better starter's fielding-independent quality.",
  },
  lineup_confirmed: {
    title: "Before and after lineup confirmation",
    description:
      "Pregame lineup confirmation requires the Phase 2 lineup poller, so every historical row is honestly unconfirmed.",
  },
  starters_known: {
    title: "By starter availability",
    description: "Whether both probable starters were known at prediction time.",
  },
};

function SliceTable({ rows }: { rows: BacktestSlice[] }) {
  const usable = rows.filter((r) => r.n_games > 0);
  if (!usable.length) return <p className="text-sm muted">No rows in this slice.</p>;
  return (
    <div className="scroll-x">
      <table className="data min-w-[560px]">
        <thead>
          <tr>
            <th scope="col">Slice</th>
            <th scope="col" className="num">Games</th>
            <th scope="col" className="num">Log loss</th>
            <th scope="col" className="num">Brier</th>
            <th scope="col" className="num">Calib. error</th>
            <th scope="col" className="num">AUC</th>
            <th scope="col" className="num">Accuracy</th>
          </tr>
        </thead>
        <tbody>
          {usable.map((row) => (
            <tr key={`${row.slice_type}-${row.slice_key}`}>
              <th scope="row" className="font-normal">
                {row.slice_key}
                {row.log_loss === null ? (
                  <span className="ml-1.5 text-[0.65rem] subtle" title="Sample too small to report metrics">
                    n&lt;30
                  </span>
                ) : null}
              </th>
              <td className="num tnum">{row.n_games.toLocaleString()}</td>
              <td className="num tnum">{num(row.log_loss, 4)}</td>
              <td className="num tnum">{num(row.brier_score, 4)}</td>
              <td className="num tnum">
                {row.calibration_error !== null ? pct(row.calibration_error, 2) : "—"}
              </td>
              <td className="num tnum">{num(row.roc_auc, 3)}</td>
              <td className="num tnum">
                {row.accuracy !== null ? pct(row.accuracy, 1) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BandTable({ rows }: { rows: BacktestSlice[] }) {
  const usable = rows.filter((r) => r.n_games > 0);
  if (!usable.length) return <p className="text-sm muted">No bands populated.</p>;
  return (
    <div className="scroll-x">
      <table className="data min-w-[520px]">
        <thead>
          <tr>
            <th scope="col">Band (favorite)</th>
            <th scope="col" className="num">Games</th>
            <th scope="col" className="num">Model said</th>
            <th scope="col" className="num">Actually won</th>
            <th scope="col" className="num">Gap</th>
          </tr>
        </thead>
        <tbody>
          {usable.map((row) => {
            const predicted = row.extra?.["mean_predicted"] as number | undefined;
            const observed = row.extra?.["observed"] as number | undefined;
            const gap =
              predicted !== undefined && observed !== undefined
                ? observed - predicted
                : null;
            return (
              <tr key={row.slice_key}>
                <th scope="row" className="font-normal">
                  {row.slice_key}%
                </th>
                <td className="num tnum">{row.n_games.toLocaleString()}</td>
                <td className="num tnum">{pct(predicted ?? null)}</td>
                <td className="num tnum">{pct(observed ?? null)}</td>
                <td
                  className="num tnum"
                  style={{
                    color:
                      gap !== null && Math.abs(gap) > 0.05
                        ? "var(--color-warn-500)"
                        : undefined,
                  }}
                >
                  {gap !== null
                    ? `${gap > 0 ? "+" : "−"}${(Math.abs(gap) * 100).toFixed(1)} pts`
                    : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function AblationTable({ rows }: { rows: BacktestSlice[] }) {
  if (!rows.length) return <p className="text-sm muted">No ablation results recorded.</p>;
  const ordered = [...rows].sort((a, b) => {
    const av = (a.extra?.["delta_log_loss"] as number | null) ?? -Infinity;
    const bv = (b.extra?.["delta_log_loss"] as number | null) ?? -Infinity;
    return bv - av;
  });
  return (
    <div className="scroll-x">
      <table className="data min-w-[620px]">
        <thead>
          <tr>
            <th scope="col">Feature group</th>
            <th scope="col" className="num">Removed</th>
            <th scope="col" className="num">Δ log loss</th>
            <th scope="col" className="num">Δ Brier</th>
            <th scope="col" className="num">Δ calib.</th>
            <th scope="col">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((row) => {
            const extra = row.extra as Record<string, unknown>;
            const verdict = String(extra["verdict"] ?? "—");
            const tone =
              verdict === "IMPROVES"
                ? "home"
                : verdict === "HURTS"
                  ? "away"
                  : verdict === "UNAVAILABLE"
                    ? "muted"
                    : "neutral";
            return (
              <tr key={row.slice_key}>
                <th scope="row" className="font-normal">
                  {String(row.slice_key).replace(/_/g, " ")}
                  {extra["note"] ? (
                    <Tooltip label={String(extra["note"])}>
                      <span className="ml-1 inline-flex">
                        <InfoIcon />
                      </span>
                    </Tooltip>
                  ) : null}
                </th>
                <td className="num tnum">{String(extra["n_features_removed"] ?? "—")}</td>
                <td className="num tnum">
                  {extra["delta_log_loss"] !== null && extra["delta_log_loss"] !== undefined
                    ? (extra["delta_log_loss"] as number) > 0
                      ? `+${(extra["delta_log_loss"] as number).toFixed(4)}`
                      : (extra["delta_log_loss"] as number).toFixed(4)
                    : "—"}
                </td>
                <td className="num tnum">
                  {extra["delta_brier"] !== null && extra["delta_brier"] !== undefined
                    ? (extra["delta_brier"] as number).toFixed(4)
                    : "—"}
                </td>
                <td className="num tnum">
                  {extra["delta_calibration_error"] !== null &&
                  extra["delta_calibration_error"] !== undefined
                    ? (extra["delta_calibration_error"] as number).toFixed(4)
                    : "—"}
                </td>
                <td>
                  <Badge tone={tone as never}>{verdict}</Badge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-xs muted">
        Δ is the change when the group is removed and the entire walk-forward is refit.
        A positive Δ log loss means removing the group made predictions worse, so the
        group earns its place. A group that does not clear the noise band is a candidate
        for removal — that is a measured decision, not a preference.
      </p>
    </div>
  );
}

export default async function BacktestPage() {
  const result = await api.backtest();

  if (!result.ok) {
    return (
      <UnavailableNotice
        title="No backtest report available"
        reason={result.message}
        requiredSource="run `make backtest`"
      />
    );
  }

  const report = result.data;
  const overall = report.overall;
  const flags = report.sanity_flags ?? [];

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Walk-forward backtest</h1>
        <p className="mt-1 max-w-prose text-sm muted">
          The model is trained only on games before each prediction date. Log loss,
          Brier score and calibration rank above accuracy — a 60% prediction has to
          win about 60% of the time for the number to be usable.
        </p>
      </header>

      {flags.length ? (
        <div
          className="rounded-md border p-4"
          style={{
            borderColor: "var(--color-danger-500)",
            background: "color-mix(in srgb, var(--color-danger-500) 8%, transparent)",
          }}
        >
          <p className="text-sm font-semibold" style={{ color: "var(--color-danger-500)" }}>
            Sanity gate tripped — treat these numbers with suspicion
          </p>
          <ul className="mt-2 flex flex-col gap-1.5 text-xs">
            {flags.map((flag) => (
              <li key={`${flag.code}-${flag.gate}`}>
                <span className="font-medium">{flag.code}</span> · {flag.gate}{" "}
                <span className="tnum">
                  {flag.value.toFixed(4)} vs threshold {flag.threshold}
                </span>
                <span className="muted"> — {flag.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <Section
        title="Headline"
        description={`${report.n_games.toLocaleString()} games from ${report.start_date} to ${report.end_date}, ${report.n_steps} walk-forward steps${
          report.n_steps_skipped ? `, ${report.n_steps_skipped} skipped for insufficient training data` : ""
        }.`}
      >
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <StatBlock
            label="Log loss"
            value={num(overall?.log_loss, 4)}
            sub={`baseline ${report.baseline_log_loss.toFixed(4)}`}
            tone={
              overall?.log_loss !== null &&
              overall?.log_loss !== undefined &&
              overall.log_loss < report.baseline_log_loss
                ? "home"
                : "away"
            }
          />
          <StatBlock label="Brier score" value={num(overall?.brier_score, 4)} />
          <StatBlock
            label="Calibration error"
            value={overall?.calibration_error !== null && overall?.calibration_error !== undefined ? pct(overall.calibration_error, 2) : "—"}
            sub={
              overall?.max_calibration_error !== null && overall?.max_calibration_error !== undefined
                ? `max ${pct(overall.max_calibration_error, 2)}`
                : undefined
            }
          />
          <StatBlock label="ROC AUC" value={num(overall?.roc_auc, 3)} />
          <StatBlock
            label="Accuracy"
            value={overall?.accuracy !== null && overall?.accuracy !== undefined ? pct(overall.accuracy, 1) : "—"}
            sub="Reported, never optimized"
          />
          <StatBlock label="Games" value={overall?.n_games.toLocaleString() ?? "—"} />
        </dl>
      </Section>

      <Section
        title="Calibration"
        description="Predicted probability against observed win frequency."
      >
        <CalibrationChart
          bins={report.calibration_bins}
          ece={overall?.calibration_error ?? null}
          mce={overall?.max_calibration_error ?? null}
        />
      </Section>

      {report.slices["probability_band"] ? (
        <Section
          title={SLICE_TITLES.probability_band.title}
          description={SLICE_TITLES.probability_band.description}
        >
          <BandTable rows={report.slices["probability_band"]} />
        </Section>
      ) : null}

      {report.slices["ablation"] ? (
        <Section
          title="Feature group ablation"
          description="Whether each feature group improves or reduces out-of-sample performance."
        >
          <AblationTable rows={report.slices["ablation"]} />
        </Section>
      ) : null}

      {(["season", "month", "favorite_underdog", "home_away", "starter_quality", "starters_known", "lineup_confirmed"] as const).map(
        (key) =>
          report.slices[key] ? (
            <Section
              key={key}
              title={SLICE_TITLES[key]?.title ?? key}
              description={SLICE_TITLES[key]?.description}
            >
              <SliceTable rows={report.slices[key]} />
            </Section>
          ) : null,
      )}

      <Section title="Return on investment and closing line value">
        <UnavailableNotice
          compact
          title="Odds-dependent metrics are omitted"
          reason={report.odds_dependent_metrics.reason}
          requiredSource="ODDS_PROVIDER"
          phase={3}
        />
      </Section>

      <Section title="Run metadata" description="Everything needed to reproduce this run.">
        <div className="scroll-x">
          <table className="data min-w-[420px]">
            <tbody>
              <tr>
                <th scope="row" className="font-normal">Run id</th>
                <td className="font-mono text-xs">{report.run_id}</td>
              </tr>
              <tr>
                <th scope="row" className="font-normal">Model</th>
                <td>
                  {report.model_name} · {report.algorithm}
                </td>
              </tr>
              <tr>
                <th scope="row" className="font-normal">Feature set</th>
                <td>{report.feature_set_version}</td>
              </tr>
              <tr>
                <th scope="row" className="font-normal">As-of policy</th>
                <td>{report.as_of_policy}</td>
              </tr>
              <tr>
                <th scope="row" className="font-normal">Step / validation</th>
                <td className="tnum">
                  {report.step_days} day steps · {report.validation_days} validation days ·
                  min {report.min_train_rows} training rows
                </td>
              </tr>
              <tr>
                <th scope="row" className="font-normal">Seed</th>
                <td className="tnum">{report.seed}</td>
              </tr>
              <tr>
                <th scope="row" className="font-normal">Git SHA</th>
                <td className="font-mono text-xs">{report.git_sha ?? "unknown"}</td>
              </tr>
              <tr>
                <th scope="row" className="font-normal">Created</th>
                <td>{timestamp(report.created_at)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
