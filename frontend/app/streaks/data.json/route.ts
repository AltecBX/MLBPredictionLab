import { api } from "@/lib/api";

/**
 * The full streaks payload as a static asset.
 *
 * The continuation history for thirty teams is a few hundred kilobytes —
 * useful to a reader who opens the explorer, dead weight inside the page HTML
 * for everyone else. It is emitted as its own file at build time and fetched
 * by the explorer on demand, so the page stays light and the history stays
 * one cacheable request. When the API cannot be reached at build time the
 * file says so rather than not existing — a fetch that 404s looks like a bug;
 * a payload that answers "unavailable, and here is why" is a state.
 */
export const dynamic = "force-static";

export async function GET() {
  const result = await api.streaks();
  const body = result.ok
    ? result.data
    : { available: false, reason: result.message };
  return Response.json(body);
}
