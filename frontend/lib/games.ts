import { api } from "./api";
import { buildDates } from "./window";

/**
 * Every game the export builds for: each game on each date in the window.
 *
 * Shared by the game page and its detail file (`/game/<id>/detail.json`), so
 * the two are built for exactly the same ids — a page whose file did not exist
 * would have nine tabs that could never open. Slates are fetched in parallel:
 * fifteen dates one after another is fifteen round trips to a service that may
 * be waking up, and the build waits for all of them either way.
 */
export async function builtGameIds(): Promise<{ id: string }[]> {
  const slates = await Promise.all(buildDates().map((date) => api.games(date)));
  const ids = new Set<number>();
  for (const slate of slates) {
    if (!slate.ok) continue;
    for (const game of slate.data.games) ids.add(game.game_id);
  }
  return [...ids].map((id) => ({ id: String(id) }));
}
