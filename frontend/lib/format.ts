/** Display formatting. Probabilities and deltas are always signed and tabular. */

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function signedPp(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(digits)} pts`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function moneyline(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value > 0 ? `+${value}` : `${value}`;
}

export function record(wins: number | null, losses: number | null): string | null {
  if (wins === null || losses === null) return null;
  return `${wins}-${losses}`;
}

const TIME_FORMATTER = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  timeZone: "America/New_York",
  timeZoneName: "short",
});

export function gameTime(iso: string): string {
  try {
    return TIME_FORMATTER.format(new Date(iso));
  } catch {
    return iso;
  }
}

export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Today's slate, in the league's own terms.
 *
 * `isoDate(new Date())` resolves in UTC, which rolls over at 8pm US Eastern —
 * so a server running in UTC would swap to tomorrow's empty slate while that
 * evening's games were still in the third inning. MLB's `officialDate` follows
 * US Eastern, so that is what "today" has to mean here.
 */
export function todayIsoDate(now: Date = new Date()): string {
  // en-CA formats as YYYY-MM-DD, which is the shape we want.
  return now.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

export function shiftIsoDate(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const base = new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1));
  base.setUTCDate(base.getUTCDate() + days);
  return base.toISOString().slice(0, 10);
}

export function longDate(iso: string, options?: { weekday?: boolean }): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1)).toLocaleDateString("en-US", {
    ...(options?.weekday === false ? {} : { weekday: "long" as const }),
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * "Fri" — the weekday alone.
 *
 * Split out from `longDate` so the date bar can set the weekday and the date at
 * two different weights on one line. Reading "which day is this" and "which date
 * is this" are separate jobs, and giving them the same emphasis makes the reader
 * do both every time.
 */
export function weekdayShort(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1)).toLocaleDateString("en-US", {
    weekday: "short",
    timeZone: "UTC",
  });
}

export function relativeAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function timestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZone: "America/New_York",
      timeZoneName: "short",
    });
  } catch {
    return iso;
  }
}

export const RECOMMENDATION_LABEL: Record<string, string> = {
  STRONG_LEAN: "Strong lean",
  MODERATE_LEAN: "Moderate lean",
  SMALL_LEAN: "Small lean",
  NO_MEANINGFUL_ADVANTAGE: "No meaningful advantage",
  INSUFFICIENT_DATA: "Insufficient data",
};

export const CONFIDENCE_LABEL: Record<string, string> = {
  HIGH: "High confidence",
  MODERATE: "Moderate confidence",
  LOW: "Low confidence",
  VERY_LOW: "Very low confidence",
  INSUFFICIENT_DATA: "Insufficient data",
};

export const FRESHNESS_LABEL: Record<string, string> = {
  FRESH: "Fresh",
  AGING: "Aging",
  STALE: "Stale",
  UNAVAILABLE: "Unavailable",
};

/** Humanize a feature key when no registry entry is at hand. */
export function humanizeKey(key: string): string {
  return key
    .replace(/_diff$/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
