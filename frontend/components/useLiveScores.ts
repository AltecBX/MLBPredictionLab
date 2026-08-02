"use client";

import { useEffect, useState } from "react";

import { fetchLiveStates, slateIsActive, type LiveMap } from "@/lib/live";
import type { GameCard } from "@/lib/types";

const POLL_MS = 45_000;

/**
 * Poll MLB's schedule feed while any game on the slate could be in progress.
 *
 * Quiet by design: it starts only inside the live window, stops itself when
 * every game is final, backs off silently on a failed fetch (the next tick
 * retries), and pauses while the tab is hidden — a phone in a pocket should
 * not be spending its battery on a bar it is not showing.
 */
export function useLiveScores(date: string, games: GameCard[]): LiveMap {
  const [live, setLive] = useState<LiveMap>(new Map());

  const active = slateIsActive(
    games.map((g) => g.first_pitch_utc),
    games.map((g) => g.is_final),
  );

  useEffect(() => {
    if (!active) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      if (cancelled) return;
      if (document.visibilityState === "visible") {
        try {
          const next = await fetchLiveStates(date);
          if (!cancelled && next.size) setLive(next);
        } catch {
          // A blip in the feed is not information. Keep the last known state
          // and let the next tick try again.
        }
      }
      timer = setTimeout(tick, POLL_MS);
    };

    void tick();
    const onVisible = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [date, active]);

  return live;
}
