import { api } from "@/lib/api";
import { builtGameIds } from "@/lib/games";

/**
 * A game's full detail as a static file beside its page.
 *
 * The game page pre-renders only the Prediction tab; the other nine panels
 * are rendered in the browser from this file when a reader opens one. Those
 * nine used to be the larger part of every game page, rendered into the HTML
 * and again into the hydration payload — three hundred kilobytes a game, most
 * of it never looked at. This is thirty-seven kilobytes, eight compressed,
 * fetched once and cached.
 *
 * It is the same `api.game` call the page makes, so the build's data cache
 * answers it from the one request. When the API cannot be reached for a game
 * the file says so rather than not existing — a fetch that 404s looks like a
 * bug; a payload that answers "unavailable, and here is why" is a state.
 */
export const dynamic = "force-static";

export function generateStaticParams() {
  return builtGameIds();
}

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await api.game(id);
  const body = result.ok ? result.data : { available: false, reason: result.message };
  return Response.json(body);
}
