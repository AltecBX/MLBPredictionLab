import { Badge } from "@/components/Badge";
import { Section } from "@/components/StatBlock";
import { UnavailableNotice } from "@/components/UnavailableNotice";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Methodology" };

export default async function MethodologyPage() {
  const result = await api.features();

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Methodology</h1>
        <p className="mt-1 max-w-prose text-sm muted">
          Every input the model uses, what it means, the window it is computed over
          and the minimum sample below which it is shrunk toward a baseline. Inputs
          that are not yet available are listed here too, with the source that would
          enable them — they are never filled with invented values.
        </p>
      </header>

      <Section
        title="How a prediction is built"
        description="Six steps, each auditable."
      >
        <ol className="flex flex-col gap-3 text-sm">
          {[
            [
              "As-of feature build",
              "Every value is computed from dated game logs, filtered to facts knowable strictly before the prediction timestamp. Season-aggregate endpoints are never consulted, because using today's season totals to predict an April game would embed that game's own result.",
            ],
            [
              "Shrinkage",
              "Small samples are regressed toward a stated league or team baseline, with the stabilization constant chosen per statistic. Recent form enters as a bounded deviation from the stabilized baseline, so a hot streak can move a prediction but cannot erase a season of evidence.",
            ],
            [
              "Model",
              "An L2-regularized logistic regression on home-minus-away differences. The regularization strength is selected by walk-forward validation, never by in-sample fit and never by a random cross-validation that would mix future into past.",
            ],
            [
              "Calibration",
              "A calibrator fit on a validation window later than training and earlier than test, applied — never re-fit — at prediction time. Both the pre- and post-calibration probability are stored.",
            ],
            [
              "Confidence",
              "A weighted score over data completeness, input confirmation, historical calibration in this probability band, prediction stability under perturbation, and model agreement. Distance from 50% contributes at most a tenth of the score, so a confident-looking number built on missing inputs never outranks a modest one built on confirmed inputs.",
            ],
            [
              "Immutable record",
              "The prediction, its full feature vector, its explanation and its warnings are written as an append-only snapshot. A new prediction supersedes the old one; both stay queryable, so a historical prediction can be evaluated exactly as it was issued.",
            ],
          ].map(([title, body], index) => (
            <li key={title} className="flex gap-3">
              <span
                className="tnum mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[0.7rem] font-semibold"
                style={{
                  background: "color-mix(in srgb, var(--accent) 14%, transparent)",
                  color: "var(--accent)",
                }}
              >
                {index + 1}
              </span>
              <span>
                <strong className="font-semibold">{title}.</strong>{" "}
                <span className="muted">{body}</span>
              </span>
            </li>
          ))}
        </ol>
      </Section>

      {result.ok ? (
        <>
          <Section
            title={`Active model inputs (${result.data.active.length})`}
            description={`Feature set ${result.data.feature_set_version}. A positive value always favors the home team; inputs where a lower raw value is better are assembled as away minus home so the sign convention holds throughout.`}
          >
            <div className="scroll-x edge-cue">
              <table className="data sticky-label min-w-[560px]">
                <thead>
                  <tr>
                    <th scope="col">Input</th>
                    <th scope="col">Category</th>
                    <th scope="col">Window</th>
                    <th scope="col" className="num">Min sample</th>
                    <th scope="col">What it measures</th>
                  </tr>
                </thead>
                <tbody>
                  {result.data.active.map((spec) => (
                    <tr key={spec.key}>
                      <th scope="row" className="font-normal">
                        {spec.display_name}
                        <div className="font-mono text-[0.65rem] subtle">{spec.key}</div>
                      </th>
                      <td className="muted">{spec.category_label}</td>
                      <td className="subtle">{spec.window ?? "—"}</td>
                      <td className="num tnum subtle">{spec.min_sample || "—"}</td>
                      <td className="max-w-[380px] text-xs muted">{spec.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section
            title={`Inputs not yet available (${result.data.deferred.length})`}
            description="Registered in the feature dictionary, reported as unavailable in the UI, and never populated with placeholder values."
          >
            <div className="scroll-x edge-cue">
              <table className="data sticky-label min-w-[560px]">
                <thead>
                  <tr>
                    <th scope="col">Input</th>
                    <th scope="col">Category</th>
                    <th scope="col">Required source</th>
                    <th scope="col">Phase</th>
                    <th scope="col">What it would measure</th>
                  </tr>
                </thead>
                <tbody>
                  {result.data.deferred.map((spec) => (
                    <tr key={spec.key}>
                      <th scope="row" className="font-normal">
                        {spec.display_name}
                        <div className="font-mono text-[0.65rem] subtle">{spec.key}</div>
                      </th>
                      <td className="muted">{spec.category_label}</td>
                      <td className="font-mono text-xs subtle">{spec.source_category}</td>
                      <td>
                        <Badge tone="muted">Phase {spec.phase}</Badge>
                      </td>
                      <td className="max-w-[380px] text-xs muted">{spec.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </>
      ) : (
        <UnavailableNotice
          title="Feature dictionary unavailable"
          reason={result.message}
          requiredSource="backend at API_BASE_URL"
        />
      )}

      <Section
        title="What this model deliberately does not do"
        description="Constraints that keep the probability honest."
      >
        <ul className="flex flex-col gap-2 text-sm muted">
          <li>
            It does not use closing odds as an input for predictions made before
            close, and closing-line value is computed in a code path that cannot
            write into a feature vector.
          </li>
          <li>
            It does not use any postgame statistic — box score, final weather, or
            attendance — for the game being predicted.
          </li>
          <li>
            It does not pull season-to-date aggregates from a stats endpoint. Every
            aggregate is rebuilt from dated game logs.
          </li>
          <li>
            It does not let a six-game head-to-head record outweigh hundreds of games
            of team quality. Head-to-head is shrunk with k=40 games.
          </li>
          <li>
            It does not report a probability without an accompanying completeness and
            freshness state, and it never labels a team a lock.
          </li>
        </ul>
      </Section>
    </div>
  );
}
