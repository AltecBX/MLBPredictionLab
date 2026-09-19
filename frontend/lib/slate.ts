import type {
  ConfidenceLabel,
  GameCard,
  PitcherRef,
  RecordSplit,
  Recommendation,
  WarningEntry,
} from "./types";

/**
 * What a day's slate hands the browser.
 *
 * The date pages are rendered on a server that holds the whole game list from
 * the API, but the cards themselves are rendered by client components: the
 * sorter needs the browser to reorder them and the live overlay needs it to
 * update them. Every prop that crosses that boundary is written into the page
 * twice — once as the HTML the reader sees and once as the hydration payload
 * React needs to take the tree over.
 *
 * The full API object for a game is about 5.5 KB of JSON, and the card renders
 * roughly a fifth of it. The rest was three driver narratives nobody on the
 * slate reads, every warning of every severity where the card shows at most two
 * of the high ones, a market block saying the odds provider is not configured,
 * the component probabilities, each team's standing and its streak game log,
 * and fourteen ballpark fields for one name and two coordinates. Fifteen games
 * of that put 82 KB of unrendered data into a front page of a quarter of a
 * megabyte.
 *
 * So the server maps each game to exactly what the client renders or sorts on,
 * and nothing else crosses. These types are the contract: a field the card
 * needs is added here on purpose, and a field the API grows does not reach the
 * browser by default. `tests/slate.test.tsx` holds the card to it — the card
 * rendered from a `SlateCard` is the same markup as the card rendered from the
 * full object.
 *
 * Structurally, every `Slate*` type is a subset of its API counterpart, so the
 * full object still satisfies it — a test fixture or the game page can hand a
 * component the whole `GameCard` and nothing has to be converted first.
 */

export interface SlateStreak {
  kind: "W" | "L";
  length: number;
  label: string;
}

export interface SlateTeam {
  name: string;
  abbreviation: string;
  team_name: string | null;
  wins: number | null;
  losses: number | null;
  home_record: RecordSplit | null;
  away_record: RecordSplit | null;
  /** Kind, length and label only. The games behind the streak are for the
   *  game page and the Streaks explorer, not the card. */
  streak: SlateStreak | null;
}

export interface SlatePitcher {
  full_name: string | null;
  pitch_hand: string | null;
  status: PitcherRef["status"];
}

/** A driver as the card lists it: name, contribution, and a key to list it by. */
export interface SlateDriver {
  feature_key: string;
  display_name: string;
  contribution_pp: number;
}

export interface SlatePrediction {
  created_at: string;
  home_win_prob: number;
  away_win_prob: number;
  predicted_winner: "HOME" | "AWAY";
  confidence_score: number;
  confidence_label: ConfidenceLabel;
  recommendation: Recommendation;
  data_completeness: number;
  projected_score: { home_runs: number | null; away_runs: number | null };
  top_drivers: SlateDriver[];
  /** The high-severity warnings only, at most the two the card shows. A
   *  medium or low warning is the game page's to explain. */
  warnings: WarningEntry[];
}

/** The park by name, for the card, and by coordinates, for the weather chip. */
export interface SlateBallpark {
  name: string | null;
  latitude: number | null;
  longitude: number | null;
}

export interface SlateCard {
  game_id: number;
  first_pitch_utc: string;
  status: string;
  status_detail: string | null;
  doubleheader: string | null;
  home: SlateTeam;
  away: SlateTeam;
  ballpark: SlateBallpark;
  home_pitcher: SlatePitcher;
  away_pitcher: SlatePitcher;
  lineup_status: string;
  bullpen_warning: string | null;
  home_score: number | null;
  away_score: number | null;
  is_final: boolean;
  prediction: SlatePrediction | null;
  prediction_unavailable: { reason: string } | null;
}

/** How many high-severity warnings a card shows. */
export const CARD_WARNINGS = 2;

function team(ref: GameCard["home"]): SlateTeam {
  return {
    name: ref.name,
    abbreviation: ref.abbreviation,
    team_name: ref.team_name,
    wins: ref.wins,
    losses: ref.losses,
    home_record: ref.home_record,
    away_record: ref.away_record,
    streak: ref.streak
      ? { kind: ref.streak.kind, length: ref.streak.length, label: ref.streak.label }
      : null,
  };
}

function pitcher(ref: PitcherRef): SlatePitcher {
  return { full_name: ref.full_name, pitch_hand: ref.pitch_hand, status: ref.status };
}

function prediction(p: NonNullable<GameCard["prediction"]>): SlatePrediction {
  return {
    created_at: p.created_at,
    home_win_prob: p.home_win_prob,
    away_win_prob: p.away_win_prob,
    predicted_winner: p.predicted_winner,
    confidence_score: p.confidence_score,
    confidence_label: p.confidence_label,
    recommendation: p.recommendation,
    data_completeness: p.data_completeness,
    projected_score: {
      home_runs: p.projected_score.home_runs,
      away_runs: p.projected_score.away_runs,
    },
    top_drivers: p.top_drivers.map((d) => ({
      feature_key: d.feature_key,
      display_name: d.display_name,
      contribution_pp: d.contribution_pp,
    })),
    warnings: p.warnings
      .filter((w) => w.severity === "high")
      .slice(0, CARD_WARNINGS)
      .map((w) => ({ code: w.code, severity: w.severity, message: w.message })),
  };
}

/** The card's view of a game: what it renders and what it is sorted by, and
 *  nothing else. Built once, on the server, per game on the slate. */
export function toSlateCard(game: GameCard): SlateCard {
  return {
    game_id: game.game_id,
    first_pitch_utc: game.first_pitch_utc,
    status: game.status,
    status_detail: game.status_detail,
    doubleheader: game.doubleheader,
    home: team(game.home),
    away: team(game.away),
    ballpark: {
      name: game.ballpark.name,
      latitude: game.ballpark.latitude,
      longitude: game.ballpark.longitude,
    },
    home_pitcher: pitcher(game.home_pitcher),
    away_pitcher: pitcher(game.away_pitcher),
    lineup_status: game.lineup_status,
    bullpen_warning: game.bullpen_warning,
    home_score: game.home_score,
    away_score: game.away_score,
    is_final: game.is_final,
    prediction: game.prediction ? prediction(game.prediction) : null,
    prediction_unavailable: game.prediction_unavailable
      ? { reason: game.prediction_unavailable.reason }
      : null,
  };
}
