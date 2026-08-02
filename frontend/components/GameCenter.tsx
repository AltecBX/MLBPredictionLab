import Link from "next/link";

import { AutoRefresh } from "@/components/AutoRefresh";
import { FreshnessStrip } from "@/components/FreshnessStrip";
import { SlateSorter } from "@/components/SlateSorter";
import { UnavailableNotice } from "@/components/UnavailableNotice";
import { WakeRetry } from "@/components/WakeRetry";
import { WeatherNow } from "@/components/WeatherNow";
import { api, looksLikeColdStart, retryBudgetSeconds } from "@/lib/api";
import { longDate, mediumDate, timestamp, weekdayShort } from "@/lib/format";
import { buildToday, isBuilt, shiftUtcIsoDate } from "@/lib/window";

/**
 * One day's slate, rendered at build time.
 *
 * The date arrives as a route parameter rather than a query string, because a
 * static site addresses pages by path — `?date=` cannot select a file. Every
 * date the arrows can reach has its own page (see `lib/window`), and the arrows
 * stop rather than link past the edge of what was built: a dead link is a worse
 * answer than a disabled control.
 */
export async function GameCenter({ date }: { date: string }) {
  const today = buildToday();
  const result = await api.games(date);
  const isToday = date === today;
  const prev = shiftUtcIsoDate(date, -1);
  const next = shiftUtcIsoDate(date, 1);

  /*
   * Where "current weather" points. One reading, from the slate's most
   * relevant park — the first game still to be decided, or the first game at
   * all on a finished day. Every reading is named with its park, because 74°
   * is only information somewhere in particular.
   */
  const weatherGame = result.ok
    ? (result.data.games.find((g) => !g.is_final) ?? result.data.games[0])
    : undefined;

  return (
    <div className="flex flex-col">
      <div className="flex items-baseline justify-between gap-3">
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

      {weatherGame?.ballpark ? (
        <div className="pt-1.5 pb-3">
          <WeatherNow
            latitude={weatherGame.ballpark.latitude}
            longitude={weatherGame.ballpark.longitude}
            place={weatherGame.ballpark.name ?? "the ballpark"}
          />
        </div>
      ) : (
        <div className="pb-3" />
      )}

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
        className="glass sticky z-20 -mx-4 border-b px-4 py-2 sm:-mx-6 sm:px-6"
        style={{
          top: "calc(var(--header-h) - 1px)",
          borderColor: "color-mix(in srgb, var(--border) 78%, transparent)",
        }}
      >
        <nav
          aria-label="Date"
          className="mx-auto grid max-w-[1240px] grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2"
        >
          <DateArrow dir="left" date={prev} today={today} label="Previous day" />

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
                href={`/d/${today}/`}
                className="pill tap t-micro shrink-0 px-2.5"
                style={{ fontWeight: 580 }}
              >
                Today
              </Link>
            ) : (
              <span
                className="t-micro shrink-0 rounded-full px-2 py-0.5"
                style={{
                  background: "var(--accent-soft)",
                  color: "var(--accent)",
                  fontWeight: 580,
                }}
              >
                Today
              </span>
            )}
          </div>

          <DateArrow dir="right" date={next} today={today} label="Next day" />
        </nav>
      </header>

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

          <SlateSorter games={result.data.games} date={date} />
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

/**
 * An arrow to a neighbouring day, or a stop at the edge of the built window.
 *
 * Rendered as a disabled span rather than a link when the target has no page.
 * On a server the arrow could always be followed; here it cannot, and pretending
 * otherwise would hand the reader a 404 instead of an honest boundary.
 */
function DateArrow({
  dir,
  date,
  today,
  label,
}: {
  dir: "left" | "right";
  date: string;
  today: string;
  label: string;
}) {
  if (!isBuilt(date, today)) {
    return (
      <span
        aria-disabled="true"
        title={`No page for ${longDate(date)} — outside the published window`}
        className="icon-btn tap-sq cursor-not-allowed opacity-40"
      >
        <Chevron dir={dir} />
      </span>
    );
  }
  return (
    <Link href={`/d/${date}/`} className="icon-btn tap-sq" title={label}>
      <Chevron dir={dir} />
      <span className="sr-only">{dir === "left" ? "← Prev" : "Next →"}</span>
    </Link>
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
