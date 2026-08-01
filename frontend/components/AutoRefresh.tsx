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
    <span className="flex items-center gap-2 text-[0.7rem] subtle">
      <label className="flex cursor-pointer items-center gap-1.5">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="size-3 accent-[var(--accent)]"
        />
        Auto-refresh near first pitch
      </label>
      {lastRefresh ? (
        <span className="tnum">
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
