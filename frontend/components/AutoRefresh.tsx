"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * Refresh the server-rendered page automatically as first pitch approaches.
 *
 * The backend clamps its cache TTL to 60 s inside the same window, so a refresh
 * here actually picks up new data rather than re-rendering the same snapshot.
 * Outside the window nothing polls, and the control is always visible so the
 * page never silently reloads under the reader.
 */
const NEAR_GAME_MS = 3 * 60 * 60 * 1000;
const INTERVAL_MS = 60_000;

export function AutoRefresh({ firstPitches }: { firstPitches: string[] }) {
  const router = useRouter();
  const [enabled, setEnabled] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const nearGame = firstPitches.some((iso) => {
    const delta = new Date(iso).getTime() - Date.now();
    // From three hours before first pitch until four hours after.
    return delta < NEAR_GAME_MS && delta > -4 * 60 * 60 * 1000;
  });

  useEffect(() => {
    if (!enabled || !nearGame) return;
    const id = setInterval(() => {
      router.refresh();
      setLastRefresh(new Date());
    }, INTERVAL_MS);
    return () => clearInterval(id);
  }, [enabled, nearGame, router]);

  if (!nearGame) return null;

  return (
    <span className="t-micro flex shrink-0 items-center gap-2 subtle">
      {/*
       * The label is abbreviated on a phone and written out on a pointer
       * device. The accessible name never changes — it is the full phrase, on
       * the input, in both cases — so the control reads the same to a screen
       * reader and to the test suite while costing a third of the width in the
       * place where width is scarce.
       */}
      <label
        className="pill flex cursor-pointer items-center gap-1.5 px-2 py-1"
        title="Auto-refresh near first pitch"
      >
        {/*
         * A 12px checkbox is not a touch target. The `after:` pseudo-element
         * extends what the browser hit-tests out to 44pt without changing the
         * box the layout sees — the same trick the info icon uses, and the one
         * the iPhone audit in e2e/mobile.spec.ts knows how to measure. It is
         * switched off above `sm`, where a cursor does not need it.
         */}
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          aria-label="Auto-refresh near first pitch"
          className="relative size-3 accent-[var(--accent)] after:absolute after:-inset-[16px] after:content-[''] sm:after:hidden"
        />
        <span aria-hidden className="whitespace-nowrap">
          <span className="sm:hidden">Auto-refresh</span>
          <span className="hidden sm:inline">Auto-refresh near first pitch</span>
        </span>
      </label>
      {lastRefresh ? (
        <span className="tnum hidden sm:inline">
          refreshed{" "}
          {lastRefresh.toLocaleTimeString("en-US", {
            hour: "numeric",
            minute: "2-digit",
          })}
        </span>
      ) : null}
    </span>
  );
}
