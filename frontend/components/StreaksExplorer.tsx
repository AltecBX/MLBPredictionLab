"use client";

import { useEffect, useMemo, useState } from "react";

import { InfoIcon, Tooltip } from "@/components/Tooltip";
import { asset } from "@/lib/asset";
import { pct } from "@/lib/format";
import {
  deriveCell,
  parseStreakLabel,
  SPLIT_LABELS,
  type LengthTable,
  type StreaksPayload,
  type WindowKey,
} from "@/lib/streaks";

/**
 * The continuation-history explorer.
 *
 * The full thirty-team history is a few hundred kilobytes, so it lives in a
 * separately cached `data.json` built at publish time and is fetched here on
 * mount — the page itself stays light. Until it arrives (or if it cannot),
 * the explorer says so; it never renders placeholder numbers.
 *
 * Every rate in the table is derived client-side from the compact cells with
 * the same formulas the backend uses (`lib/streaks.ts`, pinned by a fixture
 * test), and the shrunk value is always shown beside the raw one — a 2-for-2
 * streak is 100% raw and ~58% shrunk, and showing both is the point.
 */

type SplitKey = keyof typeof SPLIT_LABELS;

const WINDOW_LABEL: Record<WindowKey, (current: number) => string> = {
  current: (current) => `${current} only`,
  previous_three: (current) => `${current - 3}–${current - 1}`,
  combined: () => "All seasons",
};

export function StreaksExplorer({
  teams,
  seasons,
  currentSeason,
  minOccurrences,
  shrinkageK,
  favoriteUnavailableReason,
  expectationModel,
}: {
  teams: { team_id: number; abbreviation: string; name: string }[];
  seasons: number[];
  currentSeason: number;
  minOccurrences: number;
  shrinkageK: number;
  favoriteUnavailableReason: string;
  expectationModel: string;
}) {
  const [payload, setPayload] = useState<StreaksPayload | null>(null);
  const [failed, setFailed] = useState(false);
  const [teamId, setTeamId] = useState<number | "league">("league");
  const [sign, setSign] = useState<"W" | "L">("W");
  const [window_, setWindow] = useState<WindowKey>("combined");
  const [split, setSplit] = useState<SplitKey>("overall");

  useEffect(() => {
    let cancelled = false;
    fetch(asset("/streaks/data.json"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: StreaksPayload) => {
        if (!cancelled) setPayload(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const table: LengthTable | undefined = useMemo(() => {
    if (!payload?.available) return undefined;
    const source =
      teamId === "league"
        ? payload.league
        : payload.teams.find((t) => t.team_id === teamId)?.continuation;
    return source?.[window_];
  }, [payload, teamId, window_]);

  const selectedTeam =
    payload?.available && teamId !== "league"
      ? payload.teams.find((t) => t.team_id === teamId)
      : undefined;

  return (
    <section className="card min-w-0 p-4 sm:p-5" aria-label="Streak history explorer">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="t-heading">Continuation history</h2>
          <p className="t-small mt-1 max-w-prose muted">
            What happened the game after a streak of each length. Shrunk rates
            pull small samples toward the expectation ({shrinkageK.toFixed(0)}{" "}
            pseudo-games); fewer than {minOccurrences} occurrences is flagged
            rather than trusted.{" "}
            <Tooltip label={expectationModel}>
              <InfoIcon />
            </Tooltip>
          </p>
        </div>
      </div>
      <hr className="rule-soft my-3.5" />

      {/* Filters */}
      <div className="flex flex-col gap-2.5">
        <div className="scroll-x no-bar -mx-4 px-4 sm:mx-0 sm:px-0">
          <div className="flex min-w-max items-center gap-1.5">
            <label className="eyebrow pr-1" htmlFor="streak-team">
              Team
            </label>
            <select
              id="streak-team"
              className="pill tap t-small bg-transparent px-3"
              value={teamId === "league" ? "league" : String(teamId)}
              onChange={(e) =>
                setTeamId(e.target.value === "league" ? "league" : Number(e.target.value))
              }
            >
              <option value="league">All teams</option>
              {teams.map((t) => (
                <option key={t.team_id} value={t.team_id}>
                  {t.abbreviation} — {t.name}
                </option>
              ))}
            </select>

            {(["W", "L"] as const).map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={sign === s}
                onClick={() => setSign(s)}
                className={`pill tap t-small px-3 ${sign === s ? "pill-active" : ""}`}
              >
                {s === "W" ? "Winning" : "Losing"}
              </button>
            ))}

            {(Object.keys(WINDOW_LABEL) as WindowKey[]).map((w) => (
              <button
                key={w}
                type="button"
                aria-pressed={window_ === w}
                onClick={() => setWindow(w)}
                className={`pill tap t-small whitespace-nowrap px-3 ${
                  window_ === w ? "pill-active" : ""
                }`}
              >
                {WINDOW_LABEL[w](currentSeason)}
              </button>
            ))}
          </div>
        </div>

        <div className="scroll-x no-bar -mx-4 px-4 sm:mx-0 sm:px-0">
          <div className="flex min-w-max items-center gap-1.5">
            <span className="eyebrow pr-1">Split</span>
            {(Object.keys(SPLIT_LABELS) as SplitKey[]).map((key) => (
              <button
                key={key}
                type="button"
                aria-pressed={split === key}
                onClick={() => setSplit(key)}
                className={`pill tap t-small whitespace-nowrap px-3 ${
                  split === key ? "pill-active" : ""
                }`}
              >
                {SPLIT_LABELS[key]}
              </button>
            ))}
            <span
              aria-disabled="true"
              title={favoriteUnavailableReason}
              className="pill tap t-small cursor-not-allowed gap-1 whitespace-nowrap border-dashed px-3 subtle"
            >
              Favorite / underdog
              <Tooltip label={favoriteUnavailableReason}>
                <InfoIcon />
              </Tooltip>
            </span>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="mt-4">
        {failed ? (
          <p className="text-sm muted">
            The streak history file could not be loaded. It is generated with
            each publish; try reloading.
          </p>
        ) : !payload ? (
          <p className="text-sm subtle">Loading streak history…</p>
        ) : !payload.available ? (
          <p className="text-sm muted">{payload.reason}</p>
        ) : !table || Object.keys(table).length === 0 ? (
          <p className="text-sm muted">
            No qualifying streaks in this window. ({seasons.join(", ")} are on
            record.)
          </p>
        ) : (
          <ContinuationTable
            table={table}
            sign={sign}
            split={split}
            minOccurrences={minOccurrences}
            shrinkageK={shrinkageK}
          />
        )}
      </div>

      {selectedTeam ? (
        <ReachCounts
          team={selectedTeam.abbreviation}
          season={currentSeason}
          seasonCounts={selectedTeam.reach_counts_season}
          combinedCounts={selectedTeam.reach_counts_combined}
        />
      ) : null}
    </section>
  );
}

function ContinuationTable({
  table,
  sign,
  split,
  minOccurrences,
  shrinkageK,
}: {
  table: LengthTable;
  sign: "W" | "L";
  split: SplitKey;
  minOccurrences: number;
  shrinkageK: number;
}) {
  const labels = Object.keys(table)
    .filter((label) => label.startsWith(sign))
    .sort((a, b) => parseStreakLabel(a).length - parseStreakLabel(b).length);

  if (!labels.length) {
    return (
      <p className="text-sm muted">
        No {sign === "W" ? "winning" : "losing"} streaks of length 2+ in this
        window.
      </p>
    );
  }

  return (
    <div className="scroll-x edge-cue">
      <table className="data sticky-label min-w-[640px]">
        <thead>
          <tr>
            <th scope="col">After</th>
            <th scope="col" className="num">n</th>
            <th scope="col" className="num">Cont.</th>
            <th scope="col" className="num">Ended</th>
            <th scope="col" className="num">Continues (raw)</th>
            <th scope="col" className="num">Continues (shrunk)</th>
            <th scope="col" className="num">Next-game win</th>
            <th scope="col" className="num">Expected</th>
            <th scope="col" className="num">Adj. effect</th>
            <th scope="col" className="num">Next run diff</th>
            <th scope="col">95% CI (win)</th>
          </tr>
        </thead>
        <tbody>
          {labels.map((label) => {
            const compact = table[label][split];
            if (!compact) {
              return (
                <tr key={label}>
                  <th scope="row" className="font-normal numeral">{label}</th>
                  <td className="num subtle" colSpan={10}>
                    no games in this split
                  </td>
                </tr>
              );
            }
            const cell = deriveCell(
              compact,
              parseStreakLabel(label).sign,
              shrinkageK,
              minOccurrences,
            );
            return (
              <tr key={label}>
                <th scope="row" className="font-normal">
                  <span className="numeral">{label}</span>
                  {cell.insufficient ? (
                    <span className="ml-1.5 text-[0.65rem] italic subtle">
                      insufficient sample
                    </span>
                  ) : null}
                </th>
                <td className="num tnum">{cell.n}</td>
                <td className="num tnum">{cell.continued}</td>
                <td className="num tnum">{cell.ended}</td>
                <td className="num tnum">{pct(cell.pContinue)}</td>
                <td className="num tnum" style={{ fontWeight: 600 }}>
                  {cell.pContinueShrunk === null ? "—" : pct(cell.pContinueShrunk)}
                </td>
                <td className="num tnum">{pct(cell.winRate)}</td>
                <td className="num tnum muted">
                  {cell.expected === null ? "—" : pct(cell.expected)}
                </td>
                <td
                  className="num tnum"
                  style={{
                    color:
                      cell.adjustedEffectShrunk === null
                        ? undefined
                        : cell.adjustedEffectShrunk > 0.005
                          ? "var(--home)"
                          : cell.adjustedEffectShrunk < -0.005
                            ? "var(--away)"
                            : "var(--text-muted)",
                  }}
                >
                  {cell.adjustedEffectShrunk === null
                    ? "—"
                    : `${cell.adjustedEffectShrunk >= 0 ? "+" : "−"}${Math.abs(
                        cell.adjustedEffectShrunk * 100,
                      ).toFixed(1)}pp`}
                </td>
                <td className="num tnum">
                  {cell.avgRunDiff === null
                    ? "—"
                    : `${cell.avgRunDiff >= 0 ? "+" : ""}${cell.avgRunDiff.toFixed(2)}`}
                </td>
                <td className="tnum subtle">
                  {pct(cell.ciLow, 0)}–{pct(cell.ciHigh, 0)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ReachCounts({
  team,
  season,
  seasonCounts,
  combinedCounts,
}: {
  team: string;
  season: number;
  seasonCounts: Record<string, number>;
  combinedCounts: Record<string, number>;
}) {
  const order = (counts: Record<string, number>, prefix: "W" | "L") =>
    Object.entries(counts)
      .filter(([k, v]) => k.startsWith(prefix) && v > 0)
      .sort(
        (a, b) => parseStreakLabel(a[0]).length - parseStreakLabel(b[0]).length,
      );
  return (
    <div className="mt-4">
      <p className="eyebrow mb-2">
        {team} — times each streak length was reached
      </p>
      {(
        [
          [`${season}`, seasonCounts],
          ["All seasons", combinedCounts],
        ] as const
      ).map(([label, counts]) => (
        <p key={label} className="t-small mt-1 muted">
          <span className="font-medium">{label}:</span>{" "}
          {(["W", "L"] as const).map((prefix, i) => {
            const entries = order(counts, prefix);
            return (
              <span key={prefix}>
                {i > 0 ? " · " : ""}
                {entries.length
                  ? entries
                      .map(([k, v]) => `${k}×${v}`)
                      .join("  ")
                  : `no ${prefix === "W" ? "winning" : "losing"} streaks of 2+`}
              </span>
            );
          })}
        </p>
      ))}
    </div>
  );
}
