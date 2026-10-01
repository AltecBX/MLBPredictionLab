import { api } from "./api";
import { buildDates } from "./window";

/**
 * Every game the export builds for: each game on each date in the window.
 *
 * Shared by the game page and its detail file (`/game/<id>/detail.json`), so
 * the two are built for exactly the same ids — a page whose file did not exist
 * would have nine tabs that could never open. A static export requires the
 * function on each route, so both call this, and this answers the same for
 * both or stops the build:
 *
 * **All or nothing.** The first version skipped a slate it could not read.
 * On a cold API the page's call and the route's call together were a burst of
 * twenty-eight requests, one of them failed on the page's side, that date's
 * games were left out, and the export came out with 103 game pages and 104
 * detail files — which the publish workflow refused. So a slate that fails
 * while others succeed now fails the build, with the dates named. A build
 * where no slate can be read still answers with no ids: the end-to-end suite
 * builds with no API on purpose, and the publish workflow already refuses an
 * export with no game pages.
 *
 * The slates are asked for a few at a time rather than all at once, which is
 * what keeps the burst inside the API's connection pool, and within an export
 * the answer is memoised so the second caller does not ask at all.
 */
const SLATES_AT_A_TIME = 4;

let decidedForExport: Promise<{ id: string }[]> | null = null;

export function builtGameIds(): Promise<{ id: string }[]> {
  if (process.env.NEXT_STATIC_EXPORT === "1") {
    decidedForExport ??= decide();
    return decidedForExport;
  }
  return decide();
}

async function decide(): Promise<{ id: string }[]> {
  const dates = buildDates();
  const ids = new Set<number>();
  const failed: string[] = [];
  let answered = 0;
  for (let i = 0; i < dates.length; i += SLATES_AT_A_TIME) {
    const batch = dates.slice(i, i + SLATES_AT_A_TIME);
    const slates = await Promise.all(batch.map((date) => api.games(date)));
    slates.forEach((slate, j) => {
      if (slate.ok) {
        answered += 1;
        for (const game of slate.data.games) ids.add(game.game_id);
      } else {
        failed.push(`${batch[j]} (${slate.message})`);
      }
    });
  }
  if (answered > 0 && failed.length > 0) {
    throw new Error(
      `${failed.length} of ${dates.length} slates could not be read while deciding ` +
        `which game pages to build: ${failed.join("; ")}. A build that cannot see a ` +
        "date's games would publish cards whose links 404 and pages without their " +
        "detail file, so it stops instead.",
    );
  }
  return [...ids].map((id) => ({ id: String(id) }));
}
