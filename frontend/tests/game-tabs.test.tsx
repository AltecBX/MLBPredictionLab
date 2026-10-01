/**
 * The game page's tabs: one panel in the page, nine from the detail file.
 *
 * What has to hold: the pre-rendered panel shows at once and nothing is
 * fetched for it; a tab opened for the first time is rendered from the file,
 * fetched once, and stays mounted afterwards; a deep link and the back button
 * still select a tab; and when the file is not there to render from, the
 * panel says so — a loading state, the file's own reason, or a failure with a
 * way to try again — never a blank, never a placeholder number.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GameTabPanels } from "@/components/GameTabPanels";
import type { TabDef } from "@/components/Tabs";
import { gameDetail } from "./fixtures";

const TABS: TabDef[] = [
  { key: "prediction", label: "Prediction" },
  { key: "pitchers", label: "Starting pitchers", shortLabel: "Pitchers" },
  { key: "simulation", label: "Simulation", shortLabel: "Sim" },
];
const URL_ = "/MLBPredictionLab/game/1/detail.json";

function answer(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
}

function mount() {
  return render(
    <GameTabPanels tabs={TABS} basePath="/game/1" detailUrl={URL_}>
      <p>The prediction, pre-rendered.</p>
    </GameTabPanels>,
  );
}

const tab = (name: RegExp) => screen.getByRole("button", { name });

beforeEach(() => {
  window.history.replaceState(null, "", "/game/1");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("GameTabPanels", () => {
  it("shows the pre-rendered panel at once and fetches nothing for it", () => {
    const fetchMock = answer(gameDetail());
    vi.stubGlobal("fetch", fetchMock);
    mount();
    expect(screen.getByText("The prediction, pre-rendered.")).toBeVisible();
    expect(tab(/^Prediction Prediction$/)).toHaveAttribute("aria-current", "page");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders an opened tab from the detail file, fetched once, and keeps it mounted", async () => {
    const fetchMock = answer(gameDetail());
    vi.stubGlobal("fetch", fetchMock);
    mount();

    await userEvent.click(tab(/Starting pitchers/));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(URL_);
    await waitFor(() => expect(screen.getByText("Starter comparison")).toBeVisible());
    expect(screen.getByText("Home Starter")).toBeVisible();
    expect(window.location.search).toBe("?tab=pitchers");

    // Back to the first tab: the pitchers panel is hidden, not unmounted.
    await userEvent.click(tab(/^Prediction Prediction$/));
    expect(screen.getByText("The prediction, pre-rendered.")).toBeVisible();
    expect(screen.getByText("Starter comparison").closest("div[hidden]")).not.toBeNull();
    expect(window.location.search).toBe("");

    // And opening it again costs nothing.
    await userEvent.click(tab(/Starting pitchers/));
    expect(screen.getByText("Starter comparison")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("opens the tab a deep link names, and follows the back button", async () => {
    vi.stubGlobal("fetch", answer(gameDetail()));
    window.history.replaceState(null, "", "/game/1?tab=simulation");
    mount();
    await waitFor(() => expect(screen.getByText("This game was not simulated")).toBeVisible());
    expect(tab(/^Sim Simulation$/)).toHaveAttribute("aria-current", "page");

    window.history.replaceState(null, "", "/game/1?tab=pitchers");
    window.dispatchEvent(new PopStateEvent("popstate"));
    await waitFor(() => expect(screen.getByText("Starter comparison")).toBeVisible());

    window.history.replaceState(null, "", "/game/1");
    window.dispatchEvent(new PopStateEvent("popstate"));
    await waitFor(() => expect(screen.getByText("The prediction, pre-rendered.")).toBeVisible());
    expect(tab(/^Prediction Prediction$/)).toHaveAttribute("aria-current", "page");
  });

  it("says while the file is on its way, then renders", async () => {
    let release: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { release = resolve; })),
    );
    mount();
    await userEvent.click(tab(/Starting pitchers/));
    expect(screen.getByRole("status")).toHaveTextContent(/Loading starting pitchers/);
    release(new Response(JSON.stringify(gameDetail())));
    await waitFor(() => expect(screen.getByText("Starter comparison")).toBeVisible());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("says so when the file cannot be loaded, and tries again on request", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(new Response(JSON.stringify(gameDetail())));
    vi.stubGlobal("fetch", fetchMock);
    mount();
    await userEvent.click(tab(/Starting pitchers/));
    await waitFor(() =>
      expect(screen.getByText("This section could not be loaded")).toBeVisible(),
    );
    expect(screen.getByText(/did not load \(offline\)/)).toBeVisible();
    expect(screen.queryByText("Starter comparison")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("Starter comparison")).toBeVisible());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("renders the file's own reason when the build could not reach the API", async () => {
    vi.stubGlobal(
      "fetch",
      answer({ available: false, reason: "Cannot reach the prediction API at http://127.0.0.1:8000/api/v1" }),
    );
    mount();
    await userEvent.click(tab(/Starting pitchers/));
    await waitFor(() => expect(screen.getByText("This section is not available")).toBeVisible());
    expect(screen.getByText(/Cannot reach the prediction API/)).toBeVisible();
    expect(screen.queryByText("Starter comparison")).not.toBeInTheDocument();
  });

  it("treats a bad status as a failure, not as an empty section", async () => {
    vi.stubGlobal("fetch", answer({ detail: "Not Found" }, 404));
    mount();
    await userEvent.click(tab(/^Sim Simulation$/));
    await waitFor(() =>
      expect(screen.getByText("This section could not be loaded")).toBeVisible(),
    );
    expect(screen.getByText(/did not load \(404\)/)).toBeVisible();
  });
});
