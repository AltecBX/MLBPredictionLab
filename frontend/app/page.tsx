import { GameCenter } from "@/components/GameCenter";
import { buildToday } from "@/lib/window";

/**
 * The front page is today's slate.
 *
 * Every other date lives at `/d/<date>/`. This one is duplicated at the root so
 * the bare domain works and so a home-screen shortcut always opens on the
 * current day rather than the day it was saved.
 */
export default async function GameCenterPage() {
  return <GameCenter date={buildToday()} />;
}
