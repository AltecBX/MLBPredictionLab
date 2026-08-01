import { Badge, Dot } from "@/components/Badge";
import { Section, StatBlock } from "@/components/StatBlock";
import { UnavailableNotice } from "@/components/UnavailableNotice";
import { api } from "@/lib/api";
import { num, relativeAge, timestamp } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Diagnostics" };

const FRESHNESS_TONE = {
  FRESH: "ok",
  AGING: "warn",
  STALE: "bad",
  UNAVAILABLE: "off",
} as const;

export default async function DiagnosticsPage() {
  const result = await api.diagnostics();

  if (!result.ok) {
    // Keep the heading. Landing on an untitled page that only says something
    // failed leaves the reader unsure which screen they are even on — and the
    // backtest page already does it this way.
    return (
      <div className="flex flex-col gap-5">
        <header>
          <h1 className="text-xl font-semibold tracking-tight">Diagnostics</h1>
          <p className="mt-1 max-w-prose text-sm muted">
            Internal health: failed jobs, missing data, stale sources, model state.
          </p>
        </header>
        <UnavailableNotice
          title="Diagnostics unavailable"
          reason={result.message}
          requiredSource="backend at API_BASE_URL"
        />
      </div>
    );
  }

  const d = result.data;
  const model = d.model.active as Record<string, unknown> | null;
  const oos = (model?.["out_of_sample"] ?? {}) as Record<string, number | null>;
  const missing = d.missing_data;
  const predictions = d.predictions as Record<string, unknown>;
  const backtest = d.backtest as Record<string, unknown>;

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Diagnostics</h1>
        <p className="mt-1 text-sm muted">
          Internal health: failed jobs, missing data, stale sources, model state.
          Generated {timestamp(d.generated_at)} · environment {d.environment}.
        </p>
      </header>

      <Section title="Infrastructure">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatBlock
            label="Database"
            value={d.database.reachable ? "Reachable" : "Down"}
            sub={d.database.server_version ?? d.database.detail}
            tone={d.database.reachable ? "home" : "away"}
          />
          <StatBlock
            label="Cache"
            value={
              !d.cache.configured
                ? "Disabled"
                : d.cache.reachable
                  ? "Reachable"
                  : "Unreachable"
            }
            sub={d.cache.version ?? d.cache.detail}
            tone={d.cache.reachable ? "home" : undefined}
          />
          <StatBlock
            label="Games stored"
            value={(missing["total_games"] ?? 0).toLocaleString()}
            sub={`${(missing["final_games"] ?? 0).toLocaleString()} final`}
          />
          <StatBlock
            label="Player game rows"
            value={(missing["player_game_rows"] ?? 0).toLocaleString()}
            sub={`${(missing["raw_payloads"] ?? 0).toLocaleString()} raw payloads stored`}
          />
        </dl>
      </Section>

      <Section
        title="Data sources"
        description="Last successful update, freshness class and configured provider for every category."
      >
        <div className="scroll-x edge-cue">
          <table className="data sticky-label min-w-[560px]">
            <thead>
              <tr>
                <th scope="col">Category</th>
                <th scope="col">Provider</th>
                <th scope="col">Status</th>
                <th scope="col">Freshness</th>
                <th scope="col">Last success</th>
                <th scope="col" className="num">Failures</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {d.sources.map((source) => (
                <tr key={`${source.source_name}-${source.category}`}>
                  <th scope="row" className="font-normal">
                    {source.category.replace(/_/g, " ")}
                  </th>
                  <td className="font-mono text-xs subtle">
                    {source.configured_provider ?? "not configured"}
                  </td>
                  <td>
                    <Badge
                      tone={
                        source.status === "OK"
                          ? "home"
                          : source.status === "DEGRADED"
                            ? "warn"
                            : "muted"
                      }
                    >
                      {source.status}
                    </Badge>
                  </td>
                  <td>
                    <span className="inline-flex items-center gap-1.5">
                      <Dot tone={FRESHNESS_TONE[source.freshness] ?? "off"} />
                      {source.freshness}
                    </span>
                  </td>
                  <td className="tnum subtle">
                    {source.last_success_at ? timestamp(source.last_success_at) : "never"}
                  </td>
                  <td className="num tnum">{source.consecutive_failures}</td>
                  <td className="max-w-[260px] truncate text-xs muted" title={source.last_error ?? source.detail ?? ""}>
                    {source.last_error ?? source.detail ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Missing data inventory">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatBlock
            label="Final games missing boxscore"
            value={(missing["final_games_missing_boxscore"] ?? 0).toLocaleString()}
            tone={missing["final_games_missing_boxscore"] ? "away" : "home"}
          />
          <StatBlock
            label="Upcoming games"
            value={(missing["upcoming_games"] ?? 0).toLocaleString()}
          />
          <StatBlock
            label="Upcoming without a starter"
            value={(missing["upcoming_missing_probable_starter"] ?? 0).toLocaleString()}
            tone={missing["upcoming_missing_probable_starter"] ? "away" : "home"}
          />
          <StatBlock
            label="Games with boxscore"
            value={(missing["games_with_boxscore"] ?? 0).toLocaleString()}
          />
        </dl>
      </Section>

      <Section title="Active model">
        {model ? (
          <>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatBlock
                label="Version"
                value={`${model["name"]}:${model["version"]}`}
                sub={String(model["algorithm"])}
              />
              <StatBlock
                label="Trained"
                value={timestamp(String(model["trained_at"]))}
                sub={`${Number(model["train_rows"] ?? 0).toLocaleString()} rows`}
              />
              <StatBlock
                label="Out-of-sample log loss"
                value={num(oos["log_loss"], 4)}
                sub={`Brier ${num(oos["brier_score"], 4)}`}
              />
              <StatBlock
                label="Calibration"
                value={String(model["calibration_method"] ?? "—")}
                sub={`error ${oos["calibration_error"] !== null && oos["calibration_error"] !== undefined ? (oos["calibration_error"] * 100).toFixed(2) + "%" : "—"}`}
              />
            </dl>
            <p className="mt-3 text-xs subtle">
              Feature set {String(model["feature_set_version"])} · trained on{" "}
              {String(model["train_start_date"])} to {String(model["train_end_date"])} ·
              artifact <code className="font-mono">{String(model["artifact_sha256"] ?? "").slice(0, 12)}</code>
            </p>
          </>
        ) : (
          <UnavailableNotice
            compact
            title="No active model version"
            reason={d.model.unavailable_reason ?? "Run `make train`."}
            requiredSource="make train"
          />
        )}
      </Section>

      <Section title="Prediction generation">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatBlock
            label="Total predictions"
            value={Number(predictions["total_predictions"] ?? 0).toLocaleString()}
          />
          <StatBlock
            label="Last generated"
            value={
              predictions["latest_created_at"]
                ? timestamp(String(predictions["latest_created_at"]))
                : "never"
            }
            tone={predictions["is_stale"] ? "away" : undefined}
          />
          <StatBlock
            label="Upcoming (48h)"
            value={Number(predictions["upcoming_games_next_48h"] ?? 0)}
          />
          <StatBlock
            label="Upcoming unpredicted"
            value={Number(predictions["upcoming_games_without_prediction"] ?? 0)}
            tone={
              Number(predictions["upcoming_games_without_prediction"] ?? 0) > 0
                ? "away"
                : "home"
            }
          />
        </dl>
      </Section>

      <Section
        title="Recent jobs"
        description="Every ingest, training and prediction run, with failures recorded rather than swallowed."
      >
        <div className="scroll-x edge-cue">
          <table className="data sticky-label min-w-[500px]">
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Status</th>
                <th scope="col">Started</th>
                <th scope="col" className="num">Duration</th>
                <th scope="col" className="num">Rows</th>
                <th scope="col">Error</th>
              </tr>
            </thead>
            <tbody>
              {d.jobs.map((job) => (
                <tr key={job.id}>
                  <th scope="row" className="font-normal">{job.job_name}</th>
                  <td>
                    <Badge
                      tone={
                        job.status === "SUCCESS"
                          ? "home"
                          : job.status === "FAILED"
                            ? "danger"
                            : "muted"
                      }
                    >
                      {job.status}
                    </Badge>
                  </td>
                  <td className="tnum subtle">{timestamp(job.started_at)}</td>
                  <td className="num tnum">
                    {job.duration_ms !== null ? `${(job.duration_ms / 1000).toFixed(1)}s` : "—"}
                  </td>
                  <td className="num tnum">{job.rows_written?.toLocaleString() ?? "—"}</td>
                  <td className="max-w-[240px] truncate text-xs muted" title={job.error ?? ""}>
                    {job.error ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Model performance and drift">
        {backtest["available"] ? (
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatBlock
              label="Latest backtest"
              value={timestamp(String(backtest["created_at"]))}
              sub={`${Number(backtest["n_games"] ?? 0).toLocaleString()} games`}
            />
            <StatBlock label="Log loss" value={num(backtest["log_loss"] as number, 4)} />
            <StatBlock label="Brier" value={num(backtest["brier_score"] as number, 4)} />
            <StatBlock
              label="Sanity flags"
              value={(backtest["sanity_flags"] as unknown[])?.length ?? 0}
              tone={
                ((backtest["sanity_flags"] as unknown[])?.length ?? 0) > 0
                  ? "away"
                  : "home"
              }
            />
          </dl>
        ) : (
          <p className="text-sm muted">{String(backtest["reason"] ?? "No backtest yet.")}</p>
        )}
        <div className="mt-4">
          <UnavailableNotice
            compact
            title="Drift monitoring is not available"
            reason={d.drift.reason}
            phase={4}
          />
        </div>
      </Section>

      <Section title="API usage">
        <p className="text-xs muted">
          {String((d.api_usage as Record<string, unknown>)["note"] ?? "")}
        </p>
        <ul className="mt-2 flex flex-col gap-1 text-sm">
          {Object.entries(
            ((d.api_usage as Record<string, unknown>)["distinct_payloads_last_24h"] ??
              {}) as Record<string, number>,
          ).map(([source, count]) => (
            <li key={source} className="tnum">
              <span className="font-mono text-xs">{source}</span>{" "}
              <span className="muted">{count.toLocaleString()} payloads / 24h</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
