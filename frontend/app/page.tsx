import Link from "next/link";

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
    <div className="flex flex-col gap-5">
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Daily Game Center</h1>
            <p className="mt-0.5 text-sm muted">{longDate(date)}</p>
          </div>
          <nav aria-label="Date" className="flex items-center gap-1 text-sm">
            <Link
              href={`/?date=${shiftIsoDate(date, -1)}&sort=${sort}`}
              className="rounded border px-2.5 py-1.5 muted transition-colors hover:text-[var(--text)]"
              style={{ borderColor: "var(--border)" }}
            >
              ← Prev
            </Link>
            <Link
              href={`/?date=${isoDate(new Date())}&sort=${sort}`}
              className="rounded border px-2.5 py-1.5 muted transition-colors hover:text-[var(--text)]"
              style={{ borderColor: "var(--border)" }}
            >
              Today
            </Link>
            <Link
              href={`/?date=${shiftIsoDate(date, 1)}&sort=${sort}`}
              className="rounded border px-2.5 py-1.5 muted transition-colors hover:text-[var(--text)]"
              style={{ borderColor: "var(--border)" }}
            >
              Next →
            </Link>
          </nav>
        </div>

        <div className="scroll-x">
          <ul className="flex min-w-max items-center gap-1 text-xs">
            <li className="mr-1 subtle">Sort</li>
            {SORTS.map((option) => (
              <li key={option.key}>
                <Link
                  href={`/?date=${date}&sort=${option.key}`}
                  aria-current={sort === option.key ? "true" : undefined}
                  className={`block whitespace-nowrap rounded border px-2 py-1 transition-colors ${
                    sort === option.key
                      ? "font-medium"
                      : "muted hover:text-[var(--text)]"
                  }`}
                  style={{
                    borderColor:
                      sort === option.key ? "var(--accent)" : "var(--border)",
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
                className="flex cursor-not-allowed items-center gap-1 whitespace-nowrap rounded border border-dashed px-2 py-1 subtle"
              >
                {UNAVAILABLE_SORT.label}
                <Tooltip label={UNAVAILABLE_SORT.reason}>
                  <InfoIcon />
                </Tooltip>
              </span>
            </li>
          </ul>
        </div>
      </header>

      {result.ok ? (
        <>
          <section
            className="surface flex flex-col gap-2 px-4 py-3"
            aria-label="Data freshness"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
              <span className="font-medium">Data freshness by source</span>
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
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
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
