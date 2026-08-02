/**
 * Which dates get a page built for them.
 *
 * A static site has no "any date you ask for" — every reachable date must exist
 * as a file when the build runs. That is the one real cost of not having a
 * server, and it is bounded: a reader browsing the slate moves a day at a time
 * around today, not to an arbitrary date in 2019.
 *
 * The window is deliberately asymmetric. Yesterday's finals are read the morning
 * after and last week's are not, while tomorrow's probables go up the day
 * before. Both bounds are generous enough that the arrows never dead-end inside
 * a normal visit, and small enough that the build stays quick.
 */
export const DAYS_BACK = 10;
export const DAYS_FORWARD = 3;

/** `YYYY-MM-DD` for a UTC day offset from a reference date. */
export function shiftUtcIsoDate(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const at = new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1));
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
}

/**
 * The build's notion of today.
 *
 * `BUILD_DATE` is set by the publishing workflow so the window is a property of
 * the run rather than of whenever the process happened to start — two pages
 * built either side of midnight UTC must agree on which day is today, or the
 * "Today" pill lands on a date that has no page.
 */
export function buildToday(env: Record<string, string | undefined> = process.env): string {
  const pinned = env.BUILD_DATE;
  if (pinned && /^\d{4}-\d{2}-\d{2}$/.test(pinned)) return pinned;
  return new Date().toISOString().slice(0, 10);
}

/** Every date the export builds a page for, oldest first. */
export function buildDates(today: string = buildToday()): string[] {
  const out: string[] = [];
  for (let offset = -DAYS_BACK; offset <= DAYS_FORWARD; offset += 1) {
    out.push(shiftUtcIsoDate(today, offset));
  }
  return out;
}

/**
 * Whether a date has a page. The arrows are rendered disabled at the edges
 * rather than linking somewhere that 404s — a dead link is a worse answer than
 * a stop.
 */
export function isBuilt(date: string, today: string = buildToday()): boolean {
  return buildDates(today).includes(date);
}
