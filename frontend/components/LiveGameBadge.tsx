"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/Badge";
import { fetchLiveStates, slateIsActive, type LiveState } from "@/lib/live";

const POLL_MS = 45_000;

/**
 * The live overlay for a single game's hero.
 *
 * The page is a file built on the hour; this asks MLB's own feed at view time
 * and, while the game is in progress, replaces the built "Scheduled" chip with
 * the inning and score. It renders nothing outside the live window, so on a
 * finished or future game the server-rendered chips stand untouched.
 */
export function LiveGameBadge({
  gameId,
  date,
  firstPitch,
  isFinal,
}: {
  gameId: number;
  date: string;
  firstPitch: string;
  isFinal: boolean;
}) {
  const [state, setState] = useState<LiveState | null>(null);
  const active = slateIsActive([firstPitch], [isFinal]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      if (cancelled) return;
      if (document.visibilityState === "visible") {
        try {
          const map = await fetchLiveStates(date);
          const next = map.get(gameId);
          if (!cancelled && next) setState(next);
        } catch {
          // Keep the last known state; the next tick retries.
        }
      }
      timer = setTimeout(tick, POLL_MS);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [active, date, gameId]);

  if (!state) return null;

  if (state.status === "Live") {
    return (
      <Badge tone="danger" title={state.detail}>
        <span
          aria-hidden
          className="live-dot inline-block size-1.5 rounded-full"
          style={{ background: "currentColor" }}
        />
        {state.inning ? `Live · ${state.inning}` : "Live"}
        {state.awayRuns != null && state.homeRuns != null
          ? ` · ${state.awayRuns}–${state.homeRuns}`
          : ""}
      </Badge>
    );
  }
  if (state.status === "Final" && !isFinal) {
    // The feed has gone final ahead of the next site build.
    return (
      <Badge tone="muted">
        Final{state.awayRuns != null ? ` ${state.awayRuns}–${state.homeRuns}` : ""}
      </Badge>
    );
  }
  return null;
}
