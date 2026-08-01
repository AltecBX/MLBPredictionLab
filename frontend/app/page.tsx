import Link from "next/link";

import { AutoRefresh } from "@/components/AutoRefresh";
import { FreshnessStrip } from "@/components/FreshnessStrip";
import { InfoIcon, Tooltip } from "@/components/Tooltip";
import { GameCardView } from "@/components/GameCard";
import { EmptyState, UnavailableNotice } from "@/components/UnavailableNotice";
import { api } from "@/lib/api";
import { isoDate, longDate, shiftIsoDate, timestamp } from "@/lib/format";

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
  const date = params.date ?? isoDate(new Date());
  const sort = params.sort ?? "game_time";
  const result = await api.games(date, sort);

  return (
    <div className="flex flex-col gap-4 sm:gap-5">
      <h1 className="sr-only sm:not-sr-only sm:text-xl sm:font-semibold sm:tracking-tight">
        Daily Game Center
      </h1>

      {/*
       * Only the date row sticks. Changing date is the most repeated action on
       * this screen, so on a phone it must stay reachable without scrolling
       * back past a twelve-game slate — but pinning the sort chips too would
       * cost another 48px of a 844px viewport for a control used once a visit.
       */}
      <header
        className="sticky z-20 -mx-4 border-b px-4 py-1.5 backdrop-blur"
        style={{
          top: "var(--header-h)",
          borderColor: "var(--border)",
          background: "color-mix(in srgb, var(--surface-sunken) 92%, transparent)",
        }}
      >
        <nav
          aria-label="Date"
          className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2"
        >
          <Link
            href={`/?date=${shiftIsoDate(date, -1)}&sort=${sort}`}
            className="tap-sq rounded-lg border text-sm muted transition-colors hover:text-[var(--text)]"
            style={{ borderColor: "var(--border)" }}
          >
            <span aria-hidden>←</span>
            <span className="sr-only">← Prev</span>
          </Link>

          <Link
            href={`/?date=${isoDate(new Date())}&sort=${sort}`}
            className="tap min-w-0 flex-col justify-center rounded-lg text-center"
            title="Jump to today"
          >
            <span className="truncate text-sm font-semibold sm:text-base">
              {longDate(date)}
            </span>
            <span className="text-[0.65rem] subtle">tap for today</span>
          </Link>

          <Link
            href={`/?date=${shiftIsoDate(date, 1)}&sort=${sort}`}
            className="tap-sq rounded-lg border text-sm muted transition-colors hover:text-[var(--text)]"
            style={{ borderColor: "var(--border)" }}
          >
            <span aria-hidden>→</span>
            <span className="sr-only">Next →</span>
          </Link>
        </nav>
      </header>

      <div className="-mt-1">
        <div className="scroll-x no-bar snap-x-strip -mx-4 px-4">
          <ul className="flex min-w-max items-center gap-1.5 text-xs">
            <li className="hidden pr-0.5 subtle sm:block">Sort</li>
            {SORTS.map((option) => (
              <li key={option.key}>
                <Link
                  href={`/?date=${date}&sort=${option.key}`}
                  aria-current={sort === option.key ? "true" : undefined}
                  className={`tap whitespace-nowrap rounded-full border px-3 transition-colors ${
                    sort === option.key
                      ? "font-medium"
                      : "muted hover:text-[var(--text)]"
                  }`}
                  style={{
                    borderColor:
                      sort === option.key ? "var(--accent)" : "var(--border)",
                    background:
                      sort === option.key
                        ? "color-mix(in srgb, var(--accent) 12%, transparent)"
                        : undefined,
                    color: sort === option.key ? "var(--accent)" : undefined,
                  }}
                >
                  {option.label}
                </Link>
              </li>
            ))}
            <li>
              <span
                aria-disabled="true"
                title={UNAVAILABLE_SORT.reason}
                className="tap cursor-not-allowed whitespace-nowrap rounded-full border border-dashed px-3 subtle"
              >
                {UNAVAILABLE_SORT.label}
                <Tooltip label={UNAVAILABLE_SORT.reason}>
                  <InfoIcon />
                </Tooltip>
              </span>
            </li>
          </ul>
        </div>
      </div>

      {result.ok ? (
        <>
          <section
            className="surface flex flex-col gap-2 px-4 py-3"
            aria-label="Data freshness"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-xs">
              <span className="font-medium">Data freshness by source</span>
              <AutoRefresh
                firstPitches={result.data.games.map((g) => g.first_pitch_utc)}
              />
              <span className="subtle">
                {result.data.model_version
                  ? `Model ${result.data.model_version}`
                  : "No active model"}
                {" · "}
                Generated {timestamp(result.data.generated_at)}
              </span>
            </div>
            <FreshnessStrip entries={result.data.freshness} />
          </section>

          {result.data.games.length ? (
            <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {result.data.games.map((game) => (
                <GameCardView key={game.game_id} game={game} />
              ))}
            </div>
          ) : (
            <EmptyState title="No games scheduled on this date">
              The MLB schedule has no games for {longDate(date)}. Try another date.
            </EmptyState>
          )}
        </>
      ) : (
        <UnavailableNotice
          title="The prediction API is unavailable"
          reason={result.message}
          requiredSource="backend at API_BASE_URL"
        />
      )}
    </div>
  );
}
