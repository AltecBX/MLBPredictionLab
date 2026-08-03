"use client";

import { useState } from "react";

import { GameCardView } from "@/components/GameCard";
import { InfoIcon, Tooltip } from "@/components/Tooltip";
import { EmptyState } from "@/components/UnavailableNotice";
import { longDate } from "@/lib/format";
import type { LiveMap } from "@/lib/live";
import { GROUP_HINT, GROUP_LABEL, groupSlate } from "@/lib/status";
import type { GameCard } from "@/lib/types";

/**
 * Sorting moved from the server to the browser, and it had to.
 *
 * Sort used to be `?sort=`, resolved server-side. A static export has no server
 * to resolve it: a query string is not part of a file's path, so every value
 * would return the same pre-rendered page. The alternatives were pre-rendering
 * six orderings of every date — the same slate six times, differing only in the
 * order of a list already in the browser — or doing it where the data is.
 *
 * It is also just the better place for it. Reordering a dozen cards is instant
 * and needs no round trip, so the chips respond immediately instead of costing
 * a navigation.
 */
const SORTS = [
  { key: "game_time", label: "Game time" },
  { key: "win_probability", label: "Highest win probability" },
  { key: "confidence", label: "Highest confidence" },
  { key: "closest", label: "Closest game" },
  { key: "completeness", label: "Data completeness" },
] as const;

type SortKey = (typeof SORTS)[number]["key"];

// Offered but inert until a licensed odds provider is configured. Shown rather
// than hidden so the capability and its prerequisite are both visible.
const UNAVAILABLE_SORT = {
  key: "model_edge",
  label: "Largest model edge",
  reason:
    "Model edge is the gap between the model probability and the de-vigged market price. It needs a licensed odds provider — set ODDS_PROVIDER to enable it.",
} as const;

/**
 * How far from a coin flip the model is. Used by two sorts in opposite
 * directions, so it is written once: a game at .500 is the closest game there
 * is and the least confident pick there is.
 */
function edgeFromEven(game: GameCard): number | null {
  const p = game.prediction?.home_win_prob;
  return p == null ? null : Math.abs(p - 0.5);
}

/**
 * Games with no prediction sort last under every ordering.
 *
 * A missing prediction is not a zero. Letting it compare as one would file an
 * unpredicted game as the closest game on the slate, which is the same
 * confusion between UNAVAILABLE and a measured value that the rest of this
 * product refuses to make.
 */
function compareBy(key: SortKey) {
  return (a: GameCard, b: GameCard): number => {
    if (key === "game_time") {
      return a.first_pitch_utc.localeCompare(b.first_pitch_utc);
    }

    const value = (game: GameCard): number | null => {
      const p = game.prediction;
      if (!p) return null;
      switch (key) {
        case "win_probability":
          return Math.max(p.home_win_prob, p.away_win_prob);
        case "confidence":
          return p.confidence_score;
        case "completeness":
          return p.data_completeness;
        case "closest":
          return edgeFromEven(game);
      }
    };

    const av = value(a);
    const bv = value(b);
    if (av == null && bv == null) {
      return a.first_pitch_utc.localeCompare(b.first_pitch_utc);
    }
    if (av == null) return 1;
    if (bv == null) return -1;
    // "Closest" wants the smallest distance from even; everything else wants
    // the largest value.
    const ordered = key === "closest" ? av - bv : bv - av;
    // Ties break on first pitch so the order is a function of the data alone.
    // Array.prototype.sort is stable, but the input order is the API's and a
    // reshuffle between two identical slates reads as the model changing its
    // mind when nothing changed.
    return ordered !== 0 ? ordered : a.first_pitch_utc.localeCompare(b.first_pitch_utc);
  };
}

export function SlateSorter({
  games,
  date,
  live,
}: {
  games: GameCard[];
  date: string;
  /** One shared poll of MLB's feed, owned by `LiveSlate` above this. */
  live: LiveMap;
}) {
  const [sort, setSort] = useState<SortKey>("game_time");

  /*
   * Live overlay. The HTML is built on the hour; the reader may be watching in
   * the sixth inning. States fetched from MLB's own feed are merged over the
   * built cards so a game that has started moves itself into the Live group
   * and carries its current score — the page stays truthful between builds.
   */
  const merged = games.map((game) => {
    const state = live.get(game.game_id);
    if (!state) return game;
    return {
      ...game,
      status:
        state.status === "Live"
          ? "Live"
          : state.status === "Final"
            ? "Final"
            : game.status,
      status_detail: state.detail ?? game.status_detail,
      home_score: state.homeRuns ?? game.home_score,
      away_score: state.awayRuns ?? game.away_score,
      is_final: game.is_final || state.status === "Final",
    };
  });

  const sorted = [...merged].sort(compareBy(sort));

  return (
    <>
      <div className="scroll-x no-bar fade-edges snap-x-strip -mx-4 mt-3 px-4 sm:-mx-6 sm:px-6">
        <ul className="flex min-w-max items-center gap-1.5">
          <li className="eyebrow hidden pr-1 sm:block">Sort</li>
          {SORTS.map((option) => (
            <li key={option.key}>
              <button
                type="button"
                onClick={() => setSort(option.key)}
                aria-pressed={sort === option.key}
                className={`pill tap t-small whitespace-nowrap px-3 ${
                  sort === option.key ? "pill-active" : ""
                }`}
              >
                {option.label}
              </button>
            </li>
          ))}
          <li>
            <span
              aria-disabled="true"
              title={UNAVAILABLE_SORT.reason}
              className="pill tap t-small cursor-not-allowed gap-1 whitespace-nowrap border-dashed px-3 subtle"
            >
              {UNAVAILABLE_SORT.label}
              <Tooltip label={UNAVAILABLE_SORT.reason}>
                <InfoIcon />
              </Tooltip>
            </span>
          </li>
        </ul>
      </div>

      {sorted.length ? (
        /* Live first, then upcoming, then what is already settled. Mixing them
           means scanning every card to find the one in progress. */
        <div className="mt-6 flex flex-col gap-8">
          {groupSlate(sorted).map(([group, grouped]) => (
            <section key={group} aria-labelledby={`slate-${group}`}>
              <div className="mb-3 flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <h2 id={`slate-${group}`} className="t-heading flex items-center gap-2">
                  {group === "LIVE" ? (
                    <span
                      aria-hidden
                      className="live-dot inline-block size-2 rounded-full"
                      style={{
                        background: "var(--color-danger-500)",
                        boxShadow:
                          "0 0 0 3px color-mix(in srgb, var(--color-danger-500) 22%, transparent)",
                      }}
                    />
                  ) : null}
                  {GROUP_LABEL[group]}
                  <span
                    className="t-micro numeral rounded-full px-1.5 py-0.5"
                    style={{
                      background: "var(--surface-inset)",
                      color: "var(--text-muted)",
                    }}
                  >
                    {grouped.length}
                  </span>
                </h2>
                <p className="t-micro subtle">{GROUP_HINT[group]}</p>
              </div>
              <div className="stagger grid grid-cols-[minmax(0,1fr)] gap-3.5 sm:grid-cols-2 sm:gap-4 xl:grid-cols-3">
                {grouped.map((game, i) => (
                  <div
                    key={game.game_id}
                    className="flex"
                    // Capped so the last card of a fifteen-game slate is not
                    // still waiting when the reader has scrolled to it.
                    style={{ ["--i" as string]: Math.min(i, 9) }}
                  >
                    <GameCardView game={game} live={live.get(game.game_id)} />
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="mt-6">
          <EmptyState title="No games scheduled on this date">
            The MLB schedule has no games for {longDate(date)}. Try another date.
          </EmptyState>
        </div>
      )}
    </>
  );
}
