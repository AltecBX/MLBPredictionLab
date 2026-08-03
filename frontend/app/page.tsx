import { GameCenter } from "@/components/GameCenter";
import { TodayRedirect } from "@/components/TodayRedirect";
import { buildToday } from "@/lib/window";

/**
 * The front page is today's slate.
 *
 * Every other date lives at `/d/<date>/`. This one is duplicated at the root so
 * the bare domain works and so a home-screen shortcut always opens on the
 * current day rather than the day it was saved.
 *
 * "Today" here is the build's UTC day, which from 8 PM Eastern onward is the
 * reader's tomorrow — so the browser checks its own clock after load and
 * swaps to the reader's date when the two disagree (see `TodayRedirect`).
 */
export default async function GameCenterPage() {
  const today = buildToday();
  return (
    <>
      <TodayRedirect buildDate={today} />
      <GameCenter date={today} />
    </>
  );
}
