/**
 * The live layer: real data, from the right window, never faked.
 *
 * The published site is static HTML built on the hour, so scores during a game
 * and "current" weather can only come from the reader's browser. These tests
 * pin the three properties that make that honest: the poll only runs while it
 * could matter, the parse only trusts states it recognises, and a failed fetch
 * renders as nothing rather than as a placeholder pretending to be a reading.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GameCardView } from "@/components/GameCard";
import { WeatherNow } from "@/components/WeatherNow";
import {
  fetchLiveStates,
  slateIsActive,
  weatherTarget,
  type LiveMap,
  type LiveState,
} from "@/lib/live";
import { gameCard } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("slateIsActive", () => {
  const noon = new Date("2026-08-02T16:00:00Z");

  it("is quiet before the window and after it", () => {
    expect(slateIsActive(["2026-08-02T23:00:00Z"], [false], noon)).toBe(false);
    expect(slateIsActive(["2026-08-01T16:00:00Z"], [false], noon)).toBe(false);
  });

  it("wakes half an hour before first pitch and for a finished-late game", () => {
    expect(slateIsActive(["2026-08-02T16:20:00Z"], [false], noon)).toBe(true);
    expect(slateIsActive(["2026-08-02T12:00:00Z"], [false], noon)).toBe(true);
  });

  it("never polls for a slate that is entirely final", () => {
    expect(slateIsActive(["2026-08-02T15:00:00Z"], [true], noon)).toBe(false);
  });
});

describe("fetchLiveStates", () => {
  it("parses a live linescore into inning and score", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            dates: [
              {
                games: [
                  {
                    gamePk: 777,
                    status: { abstractGameState: "Live", detailedState: "In Progress" },
                    linescore: {
                      currentInning: 7,
                      inningState: "Top",
                      teams: { away: { runs: 3 }, home: { runs: 2 } },
                    },
                  },
                ],
              },
            ],
          }),
        ),
      ),
    );

    const map = await fetchLiveStates("2026-08-02");
    const state = map.get(777)!;
    expect(state.status).toBe("Live");
    expect(state.inning).toBe("Top 7");
    expect(state.awayRuns).toBe(3);
    expect(state.homeRuns).toBe(2);
  });

  it("ignores states it does not recognise rather than guessing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            dates: [
              { games: [{ gamePk: 1, status: { abstractGameState: "Other" } }] },
            ],
          }),
        ),
      ),
    );
    expect((await fetchLiveStates("2026-08-02")).size).toBe(0);
  });
});

describe("weatherTarget", () => {
  const state = (status: LiveState["status"]): LiveState => ({
    status,
    detail: status,
    awayRuns: null,
    homeRuns: null,
    inning: null,
  });
  // Early game in the east, late game out west — deliberately out of order so
  // the selection is proven to sort by first pitch, not trust the input order.
  const west = gameCard({ game_id: 2, first_pitch_utc: "2026-08-02T02:10:00Z" });
  const east = gameCard({ game_id: 1, first_pitch_utc: "2026-08-01T23:05:00Z" });
  const slate = [west, east];

  it("repoints to the live game once the early one ends — the Baltimore case", () => {
    // Build time: neither final, so the build would have pinned the east park.
    // View time: the feed says the east game ended and the west one is on.
    const live: LiveMap = new Map([
      [1, state("Final")],
      [2, state("Live")],
    ]);
    expect(weatherTarget(slate, live)).toEqual({ game: west, why: "LIVE" });
  });

  it("falls back to build-time states when no live data has arrived", () => {
    expect(weatherTarget(slate, new Map())).toEqual({ game: east, why: "NEXT" });
  });

  it("prefers a game already in progress over a later one still to start", () => {
    const started = { ...east, status: "Live" };
    expect(weatherTarget([west, started], new Map())).toEqual({
      game: started,
      why: "LIVE",
    });
  });

  it("settles on the day's first park once every game is over", () => {
    const live: LiveMap = new Map([
      [1, state("Final")],
      [2, state("Final")],
    ]);
    expect(weatherTarget(slate, live)).toEqual({ game: east, why: "DONE" });
  });

  it("never hosts the reading at a postponed game's park", () => {
    const rained = { ...east, status_detail: "Postponed" };
    expect(weatherTarget([west, rained], new Map())).toEqual({
      game: west,
      why: "NEXT",
    });
  });

  it("returns nothing for an empty slate", () => {
    expect(weatherTarget([], new Map())).toBeUndefined();
  });
});

describe("GameCardView with a live state", () => {
  it("shows the inning and the current score, and says where they come from", () => {
    const game = gameCard({ home_score: 2, away_score: 3, status: "Live" });
    render(
      <GameCardView
        game={game}
        live={{
          status: "Live",
          detail: "In Progress",
          awayRuns: 3,
          homeRuns: 2,
          inning: "Top 7",
        }}
      />,
    );
    expect(screen.getByText(/Live · Top 7/)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText(/Live score from MLB/)).toBeInTheDocument();
  });
});

describe("WeatherNow", () => {
  it("renders the reading with its place once the fetch lands", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            current: { temperature_2m: 74.2, weather_code: 0, wind_speed_10m: 11.3 },
          }),
        ),
      ),
    );
    render(<WeatherNow latitude={43.64} longitude={-79.39} place="Rogers Centre" />);
    await waitFor(() => expect(screen.getByText("74°")).toBeInTheDocument());
    expect(screen.getByText(/Clear/)).toBeInTheDocument();
    expect(screen.getByText(/wind 11 mph/)).toBeInTheDocument();
    expect(screen.getByText(/Rogers Centre/)).toBeInTheDocument();
  });

  it("explains its choice of park in the tooltip", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ current: { temperature_2m: 60.1, weather_code: 3 } })),
      ),
    );
    render(
      <WeatherNow
        latitude={43.64}
        longitude={-79.39}
        place="Rogers Centre"
        context="the next game to start"
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByTitle("Current conditions at Rogers Centre — the next game to start"),
      ).toBeInTheDocument(),
    );
  });

  it("drops the old park's reading the moment the target moves", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ current: { temperature_2m: 74.2, weather_code: 61 } })),
        )
        // The second park's fetch never settles: the chip must show nothing,
        // not the first park's temperature under the second park's name.
        .mockImplementation(() => new Promise(() => {})),
    );
    const { container, rerender } = render(
      <WeatherNow latitude={39.28} longitude={-76.62} place="Camden Yards" />,
    );
    await waitFor(() => expect(screen.getByText(/Camden Yards/)).toBeInTheDocument());
    rerender(<WeatherNow latitude={34.07} longitude={-118.24} place="Dodger Stadium" />);
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing at all when the fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { container } = render(
      <WeatherNow latitude={43.64} longitude={-79.39} place="Rogers Centre" />,
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing without coordinates rather than a reading from nowhere", () => {
    const { container } = render(
      <WeatherNow latitude={null} longitude={null} place="somewhere" />,
    );
    expect(container.innerHTML).toBe("");
  });
});
