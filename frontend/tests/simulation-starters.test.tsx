/**
 * The simulation tab says what each named starter did to the other side's
 * expected runs — and keeps "not measured" and "measured, and level" apart.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LAZY_PANELS, starterLine } from "@/components/GameTabs";
import type { SimulationDetail, StarterInSimulation } from "@/lib/types";
import { gameDetail } from "./fixtures";

const measured = (runs_delta: number): StarterInSimulation => ({
  measured: true, reason: null, runs_delta, multiplier: 1 + runs_delta / 4.4, innings: 5.7,
});
const unmeasured: StarterInSimulation = {
  measured: false, reason: "starter has no starts on record", runs_delta: null,
  multiplier: null, innings: 5.2,
};

const simulation = (over: Partial<SimulationDetail> = {}): SimulationDetail => ({
  available: true, n_simulations: 20000, home_win_pct: 0.56, away_win_pct: 0.44,
  mean_home_runs: 4.6, mean_away_runs: 4.1, home_run_distribution: [],
  away_run_distribution: [], max_reported_runs: 10, likely_scores: [],
  likely_scores_covered: null, extra_innings_prob: 0.09, one_run_prob: 0.27,
  upset_prob: 0.44, seed: 1, blend_weight: 0.5, blended_with_logistic: true,
  ...over,
});

afterEach(cleanup);

describe("starterLine", () => {
  it("says how many runs a better starter takes off the opponent", () => {
    expect(starterLine(measured(-0.312), "Ace", "NYY")).toBe(
      "Ace takes 0.31 runs off NYY's expected total over the 5.7 innings he is expected to cover.",
    );
  });

  it("says how many a worse one adds", () => {
    expect(starterLine(measured(0.2), "Fifth", "BOS")).toMatch(/^Fifth adds 0\.20 runs to BOS's/);
  });

  it("reports a measured zero as level, not as missing", () => {
    expect(starterLine(measured(0.001), "Middle", "TOR")).toMatch(/level with his rotation/);
  });

  it("reports an unmeasured starter with its reason, never as level", () => {
    const line = starterLine(unmeasured, "Rookie", "TB");
    expect(line).toBe("Rookie: not adjusted — starter has no starts on record.");
    expect(line).not.toMatch(/level|0\.00/);
  });
});

describe("the simulation tab", () => {
  it("lists both starters against the side they act on", () => {
    const detail = gameDetail({
      simulation: simulation({ home_starter: measured(-0.312), away_starter: unmeasured }),
    });
    render(<>{LAZY_PANELS.simulation(detail)}</>);
    expect(screen.getByText("Who is pitching")).toBeInTheDocument();
    const home = detail.home_detail.starter.full_name!;
    const away = detail.away_detail.starter.full_name!;
    expect(screen.getByText(new RegExp(`^${home} takes 0\\.31 runs off`))).toBeInTheDocument();
    expect(screen.getByText(`${away}: not adjusted — starter has no starts on record.`)).toBeInTheDocument();
  });

  it("says nothing about starters on a prediction issued before they were modelled", () => {
    render(<>{LAZY_PANELS.simulation(gameDetail({ simulation: simulation() }))}</>);
    expect(screen.queryByText("Who is pitching")).toBeNull();
  });
});
