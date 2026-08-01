import type { GameCard } from "./types";

/**
 * Slate grouping.
 *
 * A day's games are not one list. A game in the sixth inning, a game at 10pm,
 * a finished game and a rained-out game each want a different amount of the
 * reader's attention, and mixing them means scanning every card to find the
 * one that is actually live.
 *
 * Derived from the status the schedule feed already gives us — `status`
 * (abstractGameState) and `status_detail` (detailedState). Nothing is inferred
 * from the clock, because a delayed game still reads as "Live" long after its
 * scheduled first pitch and that is correct.
 */

export type SlateGroup = "LIVE" | "UPCOMING" | "FINAL" | "POSTPONED";

/** detailedState values that mean the game is not going to be played today. */
const NOT_PLAYED = new Set([
  "Postponed",
  "Cancelled",
  "Canceled",
  "Suspended",
  "Completed Early",
]);

export function slateGroup(game: GameCard): SlateGroup {
  const detail = game.status_detail ?? "";
  if (NOT_PLAYED.has(detail)) return "POSTPONED";
  if (game.is_final || game.status === "Final") return "FINAL";
  if (game.status === "Live") return "LIVE";
  return "UPCOMING";
}

export const GROUP_ORDER: SlateGroup[] = ["LIVE", "UPCOMING", "FINAL", "POSTPONED"];

export const GROUP_LABEL: Record<SlateGroup, string> = {
  LIVE: "Live",
  UPCOMING: "Upcoming",
  FINAL: "Final",
  POSTPONED: "Postponed",
};

/**
 * Why a group is empty is worth saying, because "no live games" and "the API
 * returned nothing" look identical otherwise.
 */
export const GROUP_HINT: Record<SlateGroup, string> = {
  LIVE: "In progress now.",
  UPCOMING: "Not yet started. Predictions update as pitchers and lineups firm up.",
  FINAL: "Completed. The prediction shown is the last one issued before first pitch.",
  POSTPONED: "Not being played as scheduled. Any prediction is retained but moot.",
};

export function groupSlate(games: GameCard[]): [SlateGroup, GameCard[]][] {
  const buckets = new Map<SlateGroup, GameCard[]>();
  for (const game of games) {
    const group = slateGroup(game);
    const bucket = buckets.get(group);
    if (bucket) bucket.push(game);
    else buckets.set(group, [game]);
  }
  // Preserve the caller's sort inside each group; only the groups are ordered.
  return GROUP_ORDER.filter((g) => buckets.has(g)).map((g) => [g, buckets.get(g)!]);
}
