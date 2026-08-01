import { describe, expect, it } from "vitest";

import { normalizeApiBaseUrl } from "@/lib/api";

import {
  humanizeKey,
  moneyline,
  num,
  pct,
  record,
  relativeAge,
  shiftIsoDate,
  signedPp,
} from "@/lib/format";

describe("formatters", () => {
  it("renders probabilities as percentages and missing values as an em dash", () => {
    expect(pct(0.618)).toBe("61.8%");
    expect(pct(null)).toBe("—");
    expect(pct(undefined)).toBe("—");
    expect(pct(Number.NaN)).toBe("—");
  });

  it("always signs probability-point deltas", () => {
    expect(signedPp(6.2)).toBe("+6.2 pts");
    expect(signedPp(-3.4)).toBe("−3.4 pts");
    expect(signedPp(0)).toBe("0.0 pts");
  });

  it("formats American odds with an explicit plus for underdogs", () => {
    expect(moneyline(-162)).toBe("-162");
    expect(moneyline(162)).toBe("+162");
    expect(moneyline(null)).toBe("—");
  });

  it("returns null for an incomplete record rather than a partial string", () => {
    expect(record(55, 45)).toBe("55-45");
    expect(record(55, null)).toBeNull();
  });

  it("shifts ISO dates across month boundaries", () => {
    expect(shiftIsoDate("2026-08-01", -1)).toBe("2026-07-31");
    expect(shiftIsoDate("2026-12-31", 1)).toBe("2027-01-01");
  });

  it("describes age in the largest sensible unit", () => {
    expect(relativeAge(30)).toBe("30s ago");
    expect(relativeAge(120)).toBe("2m ago");
    expect(relativeAge(7200)).toBe("2h ago");
    expect(relativeAge(null)).toBe("never");
  });

  it("humanizes feature keys", () => {
    expect(humanizeKey("sp_fip_season_diff")).toBe("Sp Fip Season");
  });

  it("rounds numbers to a fixed precision", () => {
    expect(num(0.684049, 4)).toBe("0.6840");
    expect(num(0.68416, 4)).toBe("0.6842");
    expect(num(null)).toBe("—");
  });
});

describe("normalizeApiBaseUrl", () => {
  it("passes a complete URL through unchanged", () => {
    expect(normalizeApiBaseUrl("https://api.example.com/api/v1")).toBe(
      "https://api.example.com/api/v1",
    );
  });

  it("adds the version prefix when a root URL was given", () => {
    expect(normalizeApiBaseUrl("https://api.example.com")).toBe(
      "https://api.example.com/api/v1",
    );
    expect(normalizeApiBaseUrl("https://api.example.com/")).toBe(
      "https://api.example.com/api/v1",
    );
  });

  it("assumes http for a private host:port, which is what Render wires in", () => {
    expect(normalizeApiBaseUrl("jerry-api:10000")).toBe(
      "http://jerry-api:10000/api/v1",
    );
    expect(normalizeApiBaseUrl("127.0.0.1:8000")).toBe("http://127.0.0.1:8000/api/v1");
  });

  it("assumes https for a public hostname given without a scheme", () => {
    expect(normalizeApiBaseUrl("jerry-api.onrender.com")).toBe(
      "https://jerry-api.onrender.com/api/v1",
    );
  });

  it("falls back to the local default when the value is blank", () => {
    expect(normalizeApiBaseUrl("   ")).toBe("http://127.0.0.1:8000/api/v1");
  });
});
