/**
 * The streak cell arithmetic, pinned to the backend's answers.
 *
 * The API sends four numbers per cell and this library derives the rest. The
 * first fixture below is a real cell produced by `app/services/streaks.py`
 * (Yankees after L4, combined window) with the backend's own outputs asserted
 * to four decimals — if either side changes its formulas, this test is what
 * notices before a reader does.
 */

import { describe, expect, it } from "vitest";

import { deriveCell, parseStreakLabel, wilson } from "@/lib/streaks";

const K = 10;
const MIN_N = 10;

describe("deriveCell", () => {
  it("matches the backend on the NYY-after-L4 fixture", () => {
    // Backend produced: raw 0.5, shrunk 0.5247, p_continue 0.5 / 0.4753,
    // adjusted -0.0544 / -0.0297, CI [0.2538, 0.7462], insufficient false.
    const cell = deriveCell([12, 6, 0.5544, 1.33], -1, K, MIN_N);
    expect(cell.n).toBe(12);
    expect(cell.continued).toBe(6);
    expect(cell.ended).toBe(6);
    expect(cell.winRate).toBeCloseTo(0.5, 9);
    expect(cell.winRateShrunk!).toBeCloseTo(0.5247, 4);
    expect(cell.pContinue).toBeCloseTo(0.5, 9);
    expect(cell.pContinueShrunk!).toBeCloseTo(0.4753, 4);
    expect(cell.adjustedEffect!).toBeCloseTo(-0.0544, 4);
    expect(cell.adjustedEffectShrunk!).toBeCloseTo(-0.0297, 4);
    expect(cell.ciLow).toBeCloseTo(0.2538, 4);
    expect(cell.ciHigh).toBeCloseTo(0.7462, 4);
    expect(cell.insufficient).toBe(false);
    expect(cell.avgRunDiff).toBe(1.33);
  });

  it("a winning streak continues by winning", () => {
    const cell = deriveCell([10, 7, 0.55, 0.5], 1, K, MIN_N);
    expect(cell.pContinue).toBeCloseTo(0.7, 9);
    expect(cell.winRate).toBeCloseTo(0.7, 9);
  });

  it("shrinks a perfect two-game sample instead of printing 100%", () => {
    const cell = deriveCell([2, 2, 0.5, 1.0], 1, K, MIN_N);
    expect(cell.winRate).toBe(1.0);
    expect(cell.winRateShrunk!).toBeCloseTo((2 + K * 0.5) / (2 + K), 9);
    expect(cell.insufficient).toBe(true);
  });

  it("propagates a missing expectation as null, never as a number", () => {
    const cell = deriveCell([5, 3, null, null], 1, K, MIN_N);
    expect(cell.winRateShrunk).toBeNull();
    expect(cell.adjustedEffect).toBeNull();
    expect(cell.adjustedEffectShrunk).toBeNull();
  });
});

describe("wilson", () => {
  it("brackets the raw rate and stays inside [0, 1]", () => {
    const [low, high] = wilson(6, 12);
    expect(low).toBeGreaterThan(0);
    expect(low).toBeLessThan(0.5);
    expect(high).toBeGreaterThan(0.5);
    expect(high).toBeLessThan(1);
  });

  it("answers [0, 1] for an empty sample", () => {
    expect(wilson(0, 0)).toEqual([0, 1]);
  });
});

describe("parseStreakLabel", () => {
  it("reads sign and length, including the pooled bucket", () => {
    expect(parseStreakLabel("W4")).toEqual({ sign: 1, length: 4 });
    expect(parseStreakLabel("L10+")).toEqual({ sign: -1, length: 10 });
  });
});
