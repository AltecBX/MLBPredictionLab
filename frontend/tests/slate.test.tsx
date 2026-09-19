/**
 * The slate crosses to the browser as the card's view-model, not the API's
 * object — and the card must not be able to tell the difference.
 *
 * `toSlateCard` decides what the client receives. Two things have to hold: the
 * card rendered from its output is the card rendered from the full object, so
 * nothing the reader sees was dropped; and the output carries nothing the card
 * does not render, so the front page stops paying for driver narratives, low
 * warnings, standings and streak logs it never shows.
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { GameCardView } from "@/components/GameCard";
import type { LiveState } from "@/lib/live";
import { CARD_WARNINGS, toSlateCard } from "@/lib/slate";
import type { GameCard } from "@/lib/types";
import { driver, gameCard } from "./fixtures";

afterEach(cleanup);

function markup(game: Parameters<typeof GameCardView>[0]["game"], live?: LiveState) {
  const { container, unmount } = render(<GameCardView game={game} live={live} />);
  const html = container.innerHTML;
  unmount();
  return html;
}

/** Every key at every depth of a JSON-able value. */
function keysDeep(value: unknown, into = new Set<string>()): Set<string> {
  if (Array.isArray(value)) value.forEach((v) => keysDeep(v, into));
  else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) {
      into.add(k);
      keysDeep(v, into);
    }
  }
  return into;
}

const variants: Record<string, () => GameCard> = {
  "the fixture as it is": () => gameCard(),
  "three high warnings among five, a bullpen note, lineups unavailable": () =>
    gameCard({
      bullpen_warning: "Home bullpen is carrying a heavy recent workload.",
      lineup_status: "UNAVAILABLE",
      prediction: {
        ...gameCard().prediction!,
        warnings: [
          { code: "A", severity: "medium", message: "A medium warning." },
          { code: "B", severity: "high", message: "First high warning." },
          { code: "C", severity: "low", message: "A low warning." },
          { code: "D", severity: "high", message: "Second high warning." },
          { code: "E", severity: "high", message: "Third high warning, never shown." },
        ],
        top_drivers: [driver(), driver({ feature_key: "x", display_name: "X" }), driver({ feature_key: "y", display_name: "Y" })],
      },
    }),
  "no prediction, with the reason": () =>
    gameCard({
      prediction: null,
      prediction_unavailable: {
        available: false,
        reason: "Both teams lack enough as-of game history.",
        required_source: "boxscores",
        phase: 1,
      },
    }),
  "a final with a score": () => gameCard({ is_final: true, home_score: 5, away_score: 3, status: "Final" }),
  "an unannounced starter and a team with no context": () => {
    const game = gameCard();
    game.home_pitcher = { id: null, full_name: null, pitch_hand: null, status: "UNKNOWN" };
    game.away = { ...game.away, home_record: null, away_record: null, streak: null, wins: null, losses: null };
    return game;
  },
  "a second game of a doubleheader": () => gameCard({ doubleheader: "S" }),
};

describe("toSlateCard", () => {
  for (const [name, make] of Object.entries(variants)) {
    it(`renders the same card as the full object: ${name}`, () => {
      const full = make();
      expect(markup(toSlateCard(full))).toBe(markup(full));
    });
  }

  it("renders the same card under a live overlay", () => {
    const full = gameCard({ home_score: 2, away_score: 3, status: "Live" });
    const live: LiveState = {
      status: "Live",
      detail: "In Progress",
      awayRuns: 3,
      homeRuns: 2,
      inning: "Top 7",
    };
    expect(markup(toSlateCard(full), live)).toBe(markup(full, live));
  });

  it("carries only what the card renders or sorts on", () => {
    const full = gameCard();
    const slate = toSlateCard(full);

    expect(Object.keys(slate).sort()).toEqual(
      [
        "away", "away_pitcher", "away_score", "ballpark", "bullpen_warning", "doubleheader",
        "first_pitch_utc", "game_id", "home", "home_pitcher", "home_score", "is_final",
        "lineup_status", "prediction", "prediction_unavailable", "status", "status_detail",
      ].sort(),
    );
    expect(Object.keys(slate.prediction!).sort()).toEqual(
      [
        "away_win_prob", "confidence_label", "confidence_score", "created_at",
        "data_completeness", "home_win_prob", "predicted_winner", "projected_score",
        "recommendation", "top_drivers", "warnings",
      ].sort(),
    );

    const carried = keysDeep(slate);
    for (const key of [
      "narrative", "feature_display", "category_label", "sample_size", "is_estimated",
      "market", "component_probs", "model_agreement", "missing_data", "as_of",
      "home_win_prob_uncalibrated", "model_version", "method", "detail", "home_low",
      "standing", "games", "division_name", "location_name", "id",
      "city", "capacity", "timezone", "lineup_status_reason", "weather_status",
      "official_date", "season", "game_type", "required_source",
    ]) {
      expect(carried.has(key), `${key} reached the browser`).toBe(false);
    }
    // And it is the smaller object, which is the point.
    expect(JSON.stringify(slate).length).toBeLessThan(JSON.stringify(full).length / 2);
  });

  it("keeps the fields the sorter orders by", () => {
    const p = toSlateCard(gameCard()).prediction!;
    expect(p.home_win_prob).toBe(0.618);
    expect(p.away_win_prob).toBe(0.382);
    expect(p.confidence_score).toBe(0.71);
    expect(p.data_completeness).toBe(0.94);
  });

  it("carries the high warnings the card shows, and no other", () => {
    const full = variants["three high warnings among five, a bullpen note, lineups unavailable"]();
    const warnings = toSlateCard(full).prediction!.warnings;
    expect(warnings.map((w) => w.code)).toEqual(["B", "D"]);
    expect(warnings).toHaveLength(CARD_WARNINGS);
    expect(warnings.every((w) => w.severity === "high")).toBe(true);
  });

  it("keeps the park's coordinates for the weather chip and drops the rest", () => {
    expect(toSlateCard(gameCard()).ballpark).toEqual({
      name: "Test Park",
      latitude: 40.75,
      longitude: -73.85,
    });
  });
});
