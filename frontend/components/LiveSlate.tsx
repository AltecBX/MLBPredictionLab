"use client";

import type { ReactNode } from "react";

import { SlateSorter } from "@/components/SlateSorter";
import { useLiveScores } from "@/components/useLiveScores";
import { WeatherNow } from "@/components/WeatherNow";
import { weatherTarget } from "@/lib/live";
import type { SlateCard } from "@/lib/slate";

/**
 * The client shell of a day page: one poll of MLB's schedule feed, shared by
 * everything on the screen that must be current at view time.
 *
 * The weather chip and the game cards both depend on live state — the chip to
 * know which park matters *now*, the cards to carry scores — and sharing one
 * poll between them is this component's reason to exist. The sticky date
 * header and the freshness strip need no liveness, so they pass through as
 * server-rendered children between the two.
 *
 * Before any live data arrives (or if the feed is unreachable) `weatherTarget`
 * falls back to the build-time game states, which is exactly what the page
 * said before this component existed — live data sharpens the answer, it is
 * never required for one.
 */
const WHY: Record<"LIVE" | "NEXT" | "DONE", string> = {
  LIVE: "the first game still in progress",
  NEXT: "the next game to start",
  DONE: "the day's first park — every game is settled",
};

export function LiveSlate({
  games,
  date,
  children,
}: {
  /** The card's view of each game (lib/slate), not the API object: this is
   *  the client boundary, and everything here is serialised into the page. */
  games: SlateCard[];
  date: string;
  /** Server-rendered: the sticky date header and the freshness section. */
  children: ReactNode;
}) {
  const live = useLiveScores(date, games);
  const pin = weatherTarget(games, live);

  return (
    <>
      {pin?.game.ballpark ? (
        <div className="pt-1.5 pb-3">
          <WeatherNow
            latitude={pin.game.ballpark.latitude}
            longitude={pin.game.ballpark.longitude}
            place={pin.game.ballpark.name ?? "the ballpark"}
            context={WHY[pin.why]}
          />
        </div>
      ) : (
        <div className="pb-3" />
      )}
      {children}
      <SlateSorter games={games} date={date} live={live} />
    </>
  );
}
