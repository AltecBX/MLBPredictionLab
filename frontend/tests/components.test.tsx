import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CalibrationChart } from "@/components/CalibrationChart";
import { DriverList } from "@/components/DriverList";
import { GameCardView } from "@/components/GameCard";
import { MatchupBars } from "@/components/MatchupBars";
import { ProbabilityBar } from "@/components/ProbabilityBar";
import { UnavailableNotice } from "@/components/UnavailableNotice";
import { FreshnessStrip } from "@/components/FreshnessStrip";
import { calibrationBins, driver, gameCard, matchupBars } from "./fixtures";

describe("ProbabilityBar", () => {
  it("shows both sides and labels the split for screen readers", () => {
    render(<ProbabilityBar homeProb={0.618} homeLabel="HME" awayLabel="AWY" />);
    expect(screen.getByText("61.8%")).toBeInTheDocument();
    expect(screen.getByText("38.2%")).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /AWY 38.2%, HME 61.8%/ }),
    ).toBeInTheDocument();
  });

  it("never renders a probability above 100% for out-of-range input", () => {
    render(<ProbabilityBar homeProb={1.4} homeLabel="H" awayLabel="A" />);
    expect(screen.getByText("100.0%")).toBeInTheDocument();
    expect(screen.getByText("0.0%")).toBeInTheDocument();
  });
});

describe("GameCardView", () => {
  it("answers who is favored, by how much and why, in one view", () => {
    render(<GameCardView game={gameCard()} />);
    // The compact card shows the short club name plus the abbreviation.
    expect(screen.getByText("Club")).toBeInTheDocument();
    expect(screen.getByText("Visitors")).toBeInTheDocument();
    // The abbreviation appears in the team row and again on the probability bar.
    expect(screen.getAllByText("HME").length).toBeGreaterThan(0);
    expect(screen.getAllByText("AWY").length).toBeGreaterThan(0);
    expect(screen.getByText("55-45")).toBeInTheDocument();
    expect(screen.getByText("61.8%")).toBeInTheDocument();
    expect(screen.getByText("Strong lean")).toBeInTheDocument();
    expect(screen.getByText("Starter FIP edge")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Full breakdown/ })).toHaveAttribute(
      "href",
      "/game/1",
    );
  });

  it("names an unannounced starter rather than leaving it blank", () => {
    const game = gameCard();
    game.home_pitcher = { id: null, full_name: null, pitch_hand: null, status: "UNKNOWN" };
    render(<GameCardView game={game} />);
    expect(screen.getByText("Starter not announced")).toBeInTheDocument();
  });

  it("shows an explicit reason when no prediction exists", () => {
    const game = gameCard({
      prediction: null,
      prediction_unavailable: {
        available: false,
        reason: "Both teams lack enough as-of game history.",
      },
    });
    render(<GameCardView game={game} />);
    expect(
      screen.getByText(/lack enough as-of game history/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Strong lean")).not.toBeInTheDocument();
  });

  it("surfaces high-severity warnings on the card", () => {
    const game = gameCard();
    game.prediction!.warnings = [
      { code: "HOME_STARTER_UNCONFIRMED", severity: "high", message: "Home starter is not announced." },
    ];
    render(<GameCardView game={game} />);
    expect(screen.getByText("Home starter is not announced.")).toBeInTheDocument();
  });

  it("shows the final score for a completed game", () => {
    const game = gameCard({ is_final: true, home_score: 5, away_score: 3 });
    render(<GameCardView game={game} />);
    expect(screen.getByText("Final")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });
});

describe("DriverList", () => {
  it("lists contributions in probability points with sample size", () => {
    render(<DriverList drivers={[driver()]} tone="home" emptyMessage="none" />);
    expect(screen.getByText("+6.2")).toBeInTheDocument();
    expect(screen.getByText("Starter FIP edge")).toBeInTheDocument();
    expect(screen.getByText("n=18")).toBeInTheDocument();
  });

  it("marks estimated inputs", () => {
    render(
      <DriverList drivers={[driver({ is_estimated: true })]} tone="home" emptyMessage="none" />,
    );
    expect(screen.getByText(/estimated/)).toBeInTheDocument();
  });

  it("renders the empty message rather than an empty list", () => {
    render(<DriverList drivers={[]} tone="away" emptyMessage="No contributions recorded." />);
    expect(screen.getByText("No contributions recorded.")).toBeInTheDocument();
  });
});

describe("MatchupBars", () => {
  it("names the side holding each advantage", () => {
    render(<MatchupBars bars={matchupBars} homeLabel="HME" awayLabel="AWY" />);
    expect(
      screen.getByRole("img", { name: /Starting pitching: HME by 5.1 points/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Bullpen: AWY by 2.8 points/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Offense: even/ })).toBeInTheDocument();
  });
});

describe("CalibrationChart", () => {
  it("renders bins and reports the calibration error", () => {
    render(<CalibrationChart bins={calibrationBins} ece={0.011} mce={0.03} />);
    expect(
      screen.getByRole("img", { name: /Calibration chart/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("1.10%")).toBeInTheDocument();
  });

  it("says so when no bins are populated", () => {
    render(<CalibrationChart bins={[]} ece={null} mce={null} />);
    expect(screen.getByText(/No calibration bins are populated/)).toBeInTheDocument();
  });
});

describe("UnavailableNotice", () => {
  it("names the reason and the source that would fix it", () => {
    render(
      <UnavailableNotice
        title="Weather is not available"
        reason="No weather provider is configured."
        requiredSource="WEATHER_PROVIDER"
        phase={2}
      />,
    );
    expect(screen.getByText("Weather is not available")).toBeInTheDocument();
    expect(screen.getByText("No weather provider is configured.")).toBeInTheDocument();
    expect(screen.getByText("WEATHER_PROVIDER")).toBeInTheDocument();
    expect(screen.getByText(/Phase 2/)).toBeInTheDocument();
  });
});

describe("FreshnessStrip", () => {
  it("reports freshness per category, not globally", () => {
    render(
      <FreshnessStrip
        entries={[
          {
            category: "schedule", label: "Schedule", status: "OK", freshness: "FRESH",
            last_success_at: "2026-08-01T20:00:00Z", age_seconds: 120, provider: "mlb_statsapi",
            detail: null,
          },
          {
            category: "weather", label: "Weather", status: "UNAVAILABLE",
            freshness: "UNAVAILABLE", last_success_at: null, age_seconds: null,
            provider: null, detail: "No provider configured for this category.",
          },
        ]}
      />,
    );
    expect(screen.getByText("Schedule")).toBeInTheDocument();
    expect(screen.getByText("2m ago")).toBeInTheDocument();
    expect(screen.getByText("Weather")).toBeInTheDocument();
    expect(screen.getByText("unavailable")).toBeInTheDocument();
  });
});
