/**
 * Streak payload types and the cell arithmetic.
 *
 * The API sends each continuation cell as four numbers —
 * `[n, continued, expected_win_rate, avg_next_run_diff]` — and this module
 * derives everything the page displays: raw and shrunk rates, the adjusted
 * effect, the Wilson interval, the insufficient flag. The formulas are the
 * backend's own (`app/services/streaks.py`), and a fixture test pins the two
 * implementations to the same answers, so compactness never becomes drift.
 *
 * The framing rule of the whole section lives here too: nothing in a streak
 * cell says a team is "due". Every rate is presented beside the pre-game Elo
 * expectation for those same games, and the adjusted effect is the difference
 * — usually a small number, which is the finding.
 */

export type CompactCell = [number, number, number | null, number | null];

export interface DerivedCell {
  n: number;
  continued: number;
  ended: number;
  winRate: number;
  winRateShrunk: number | null;
  pContinue: number;
  pContinueShrunk: number | null;
  expected: number | null;
  adjustedEffect: number | null;
  adjustedEffectShrunk: number | null;
  avgRunDiff: number | null;
  ciLow: number;
  ciHigh: number;
  insufficient: boolean;
}

export interface StreakGameLine {
  date: string;
  opponent: string;
  home: boolean;
  result: string;
}

export type LengthTable = Record<string, Record<string, CompactCell>>;

export interface TeamStreaks {
  team_id: number;
  abbreviation: string;
  name: string;
  current_streak: string | null;
  current_streak_start: string | null;
  streak_games: StreakGameLine[];
  home_streak: string | null;
  away_streak: string | null;
  longest_win_streak: number;
  longest_loss_streak: number;
  reach_counts_season: Record<string, number>;
  reach_counts_combined: Record<string, number>;
  games_played: number;
  continuation: Record<WindowKey, LengthTable>;
}

export interface NextGameSide {
  team_id: number;
  abbreviation: string;
  current_streak: string | null;
  history?: Record<string, number | boolean | null>;
}

export interface NextGameStreak {
  game_id: number;
  first_pitch_utc: string;
  home: NextGameSide;
  away: NextGameSide;
  model_home_win_prob: number | null;
}

export type WindowKey = "current" | "previous_three" | "combined";

export interface StreaksPayload {
  available: boolean;
  reason?: string;
  current_season: number;
  seasons: number[];
  min_occurrences: number;
  shrinkage_k: number;
  expectation_model: string;
  favorite_underdog: { available: boolean; reason: string };
  league: Record<WindowKey, LengthTable>;
  teams: TeamStreaks[];
  next_games: NextGameStreak[];
}

export const SPLIT_LABELS: Record<string, string> = {
  overall: "Overall",
  home: "Home",
  away: "Away",
  rest: "Off day before",
  no_rest: "No day off",
  opp_strong: "vs strong opponents",
  opp_average: "vs average opponents",
  opp_weak: "vs weak opponents",
  sp_strong: "vs strong starters",
  sp_average: "vs average starters",
  sp_weak: "vs weak starters",
  sp_hand_L: "vs left-handed starters",
  sp_hand_R: "vs right-handed starters",
};

/** Wilson score interval — the same arithmetic as the backend's. */
export function wilson(successes: number, n: number, z = 1.96): [number, number] {
  if (n === 0) return [0, 1];
  const p = successes / n;
  const denom = 1 + (z * z) / n;
  const centre = (p + (z * z) / (2 * n)) / denom;
  const margin =
    (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom;
  return [Math.max(0, centre - margin), Math.min(1, centre + margin)];
}

/**
 * Expand a compact cell. `sign` is +1 for winning streaks, −1 for losing —
 * a winning streak continues by winning, a losing streak by losing, and the
 * shrinkage prior for each rate is what the expectation model already said.
 */
export function deriveCell(
  cell: CompactCell,
  sign: 1 | -1,
  shrinkageK: number,
  minOccurrences: number,
): DerivedCell {
  const [n, continued, expected, avgRunDiff] = cell;
  const wins = sign > 0 ? continued : n - continued;
  const winRate = n > 0 ? wins / n : 0;
  const pContinue = n > 0 ? continued / n : 0;
  const expectedContinue =
    expected === null ? null : sign > 0 ? expected : 1 - expected;

  const shrink = (successes: number, prior: number | null) =>
    prior === null ? null : (successes + shrinkageK * prior) / (n + shrinkageK);

  const winRateShrunk = shrink(wins, expected);
  const [ciLow, ciHigh] = wilson(wins, n);
  return {
    n,
    continued,
    ended: n - continued,
    winRate,
    winRateShrunk,
    pContinue,
    pContinueShrunk: shrink(continued, expectedContinue),
    expected,
    adjustedEffect: expected === null ? null : winRate - expected,
    adjustedEffectShrunk:
      expected === null || winRateShrunk === null
        ? null
        : winRateShrunk - expected,
    avgRunDiff,
    ciLow,
    ciHigh,
    insufficient: n < minOccurrences,
  };
}

/** "W4" → { sign: 1, length: 4 }; "L10+" → { sign: -1, length: 10 }. */
export function parseStreakLabel(label: string): { sign: 1 | -1; length: number } {
  const sign = label.startsWith("W") ? 1 : -1;
  return { sign, length: parseInt(label.slice(1), 10) };
}

export const LENGTH_LABELS = [
  "2", "3", "4", "5", "6", "7", "8", "9", "10+",
] as const;
