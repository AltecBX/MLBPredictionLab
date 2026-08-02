import Link from "next/link";

import { AutoRefresh } from "@/components/AutoRefresh";
import { FreshnessStrip } from "@/components/FreshnessStrip";
import { InfoIcon, Tooltip } from "@/components/Tooltip";
import { GameCardView } from "@/components/GameCard";
import { EmptyState, UnavailableNotice } from "@/components/UnavailableNotice";
import { api, looksLikeColdStart, retryBudgetSeconds } from "@/lib/api";
import { WakeRetry } from "@/components/WakeRetry";
import {
  longDate,
  mediumDate,
  shiftIsoDate,
  timestamp,
  todayIsoDate,
  weekdayShort,
} from "@/lib/format";
import { GROUP_HINT, GROUP_LABEL, groupSlate } from "@/lib/status";

export const dynamic = "force-dynamic";

const SORTS = [
  { key: "game_time", label: "Game time" },
  { key: "win_probability", label: "Highest win probability" },
  { key: "confidence", label: "Highest confidence" },
  { key: "closest", label: "Closest game" },
  { key: "completeness", label: "Data completeness" },
] as const;

// Offered but inert until a licensed odds provider is configured. Shown rather
// than hidden so the capability and its prerequisite are both visible.
const UNAVAILABLE_SORT = {
  key: "model_edge",
  label: "Largest model edge",
  reason:
    "Model edge is the gap between the model probability and the de-vigged market price. It needs a licensed odds provider — set ODDS_PROVIDER to enable it.",
} as const;

export default async function GameCenterPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string; sort?: string }>;
}) {
  const params = await searchParams;
  const date = params.date ?? todayIsoDate();
  const sort = params.sort ?? "game_time";
  const result = await api.games(date, sort);
  const isToday = date === todayIsoDate();

  return (
    <div className="flex flex-col">
      <div className="flex items-baseline justify-between gap-3 pb-3">
        <h1 className="t-display">Daily Game Center</h1>
        {result.ok ? (
          <p className="t-micro hidden shrink-0 subtle sm:block">
            {result.data.model_version
              ? `Model ${result.data.model_version}`
              : "No active model"}
            {" · "}
            {timestamp(result.data.generated_at)}
          </p>
        ) : null}
      </div>

      {/*
       * Only the date row sticks. Changing date is the most repeated action on
       * this screen, so on a phone it must stay reachable without scrolling back
       * past a twelve-game slate — but pinning the sort chips too would cost
       * another 48px of an 844px viewport for a control used once a visit.
       *
       * One line, not two: the previous version spent 110px of a phone screen
       * saying one date. The weekday carries the "which day" reading and the
       * numeric part carries the rest, so both fit on a single baseline.
       */}
      <header
        className="sticky z-20 -mx-4 border-b px-4 py-2 sm:-mx-6 sm:px-6"
        style={{
          top: "calc(var(--header-h) - 1px)",
          borderColor: "var(--border)",
          background: "color-mix(in srgb, var(--surface-sunken) 82%, transparent)",
          backdropFilter: "blur(14px) saturate(1.6)",
          WebkitBackdropFilter: "blur(14px) saturate(1.6)",
        }}
      >
        <nav
          aria-label="Date"
          className="mx-auto grid max-w-[1240px] grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2"
        >
          <Link
            href={`/?date=${shiftIsoDate(date, -1)}&sort=${sort}`}
            className="icon-btn tap-sq"
            title="Previous day"
          >
            <Chevron dir="left" />
            <span className="sr-only">← Prev</span>
          </Link>

          <div className="flex min-w-0 items-center justify-center gap-2">
            <p className="t-heading min-w-0 truncate text-center">
              <span className="subtle">{weekdayShort(date)}</span>{" "}
              <span style={{ fontWeight: 640 }}>
                {/* The 375px iPhone truncates the long month and swallows the
                    year with it. Three letters of month is the cheaper loss. */}
                <span className="min-[420px]:hidden">{mediumDate(date)}</span>
                <span className="hidden min-[420px]:inline">
                  {longDate(date, { weekday: false })}
                </span>
              </span>
            </p>
            {!isToday ? (
              <Link
                href={`/?date=${todayIsoDate()}&sort=${sort}`}
                className="pill tap t-micro shrink-0 px-2.5"
                style={{ fontWeight: 580 }}
              >
                Today
              </Link>
            ) : (
              <span
                className="t-micro shrink-0 rounded-full px-2 py-0.5"
                style={{ background: "var(--accent-soft)", color: "var(--accent)", fontWeight: 580 }}
              >
                Today
              </span>
            )}
          </div>

          <Link
            href={`/?date=${shiftIsoDate(date, 1)}&sort=${sort}`}
            className="icon-btn tap-sq"
            title="Next day"
          >
            <Chevron dir="right" />
            <span className="sr-only">Next →</span>
          </Link>
        </nav>
      </header>

      <div className="scroll-x no-bar fade-edges snap-x-strip -mx-4 mt-3 px-4 sm:-mx-6 sm:px-6">
        <ul className="flex min-w-max items-center gap-1.5">
          <li className="eyebrow hidden pr-1 sm:block">Sort</li>
          {SORTS.map((option) => (
            <li key={option.key}>
              <Link
                href={`/?date=${date}&sort=${option.key}`}
                aria-current={sort === option.key ? "true" : undefined}
                className={`pill tap t-small whitespace-nowrap px-3 ${
                  sort === option.key ? "pill-active" : ""
                }`}
              >
                {option.label}
              </Link>
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

      {result.ok ? (
        <>
          {/*
           * Freshness is ambient. It used to be a card the same weight as a
           * game, which put "when did the schedule last update" beside "who
           * wins tonight" as equals. It is a strip on the page background now:
           * present, scannable, and clearly not the subject.
           */}
          <section className="mt-5 flex flex-col gap-2" aria-label="Data freshness">
            <div className="flex items-center justify-between gap-3">
              <span className="eyebrow shrink-0">
                Data freshness<span className="hidden sm:inline"> by source</span>
              </span>
              <AutoRefresh
                firstPitches={result.data.games.map((g) => g.first_pitch_utc)}
              />
            </div>
            <FreshnessStrip entries={result.data.freshness} />
            <p className="t-micro subtle sm:hidden">
              {result.data.model_version
                ? `Model ${result.data.model_version}`
                : "No active model"}
              {" · "}
              {timestamp(result.data.generated_at)}
            </p>
          </section>

          {result.data.games.length ? (
            /* Live first, then upcoming, then what is already settled. Mixing
               them means scanning every card to find the one in progress. */
            <div className="mt-6 flex flex-col gap-8">
              {groupSlate(result.data.games).map(([group, games]) => (
                <section key={group} aria-labelledby={`slate-${group}`}>
                  <div className="mb-3 flex flex-wrap items-center gap-x-2.5 gap-y-1">
                    <h2
                      id={`slate-${group}`}
                      className="t-heading flex items-center gap-2"
                    >
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
                        {games.length}
                      </span>
                    </h2>
                    <p className="t-micro subtle">{GROUP_HINT[group]}</p>
                  </div>
                  <div className="stagger grid grid-cols-[minmax(0,1fr)] gap-3.5 sm:grid-cols-2 sm:gap-4 xl:grid-cols-3">
                    {games.map((game, i) => (
                      <div
                        key={game.game_id}
                        className="flex"
                        // Capped so the last card of a fifteen-game slate is not
                        // still waiting when the reader has scrolled to it.
                        style={{ ["--i" as string]: Math.min(i, 9) }}
                      >
                        <GameCardView game={game} />
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
      ) : (
        <div className="mt-6">
          <UnavailableNotice
            title={
              looksLikeColdStart(result.status)
                ? "The prediction API is still waking up"
                : "The prediction API is unavailable"
            }
            reason={
              looksLikeColdStart(result.status)
                ? `${result.message} — this deployment sleeps when idle and takes about a minute to come back.${
                    retryBudgetSeconds() > 0
                      ? ` It was retried for ${retryBudgetSeconds()} seconds and had not answered yet;`
                      : ""
                  }`
                : result.message
            }
            requiredSource="backend at API_BASE_URL"
          />
          {looksLikeColdStart(result.status) ? <WakeRetry /> : null}
        </div>
      )}
    </div>
  );
}

function Chevron({ dir }: { dir: "left" | "right" }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.1"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={dir === "left" ? "M15 5l-7 7 7 7" : "M9 5l7 7-7 7"} />
    </svg>
  );
}
