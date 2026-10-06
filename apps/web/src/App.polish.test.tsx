import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { advance, installFakeApi } from "./test/fakeApi";
import meFixture from "./test/fixtures/me.json";
import matchesFixture from "./test/fixtures/matches.json";
import summaryPersonal from "./test/fixtures/summary_personal.json";
import { SteamSection } from "./steam/SteamSection";

const OPTIONS = { maps: ["de_mirage"], weapons: ["ak47"], opening_kill_sides: ["ct", "t"] };

function appRoutes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /options": { status: 200, body: OPTIONS },
    "GET /steam/status": { status: 200, body: { enabled: false } },
    "POST /predict": { status: 200, body: { predicted_winner: "ct", probabilities: { ct: 0.643, t: 0.357 } } },
    ...extra,
  };
}

const readout = () => screen.getByText("READOUT").closest(".result-panel") as HTMLElement;

describe("design review polish", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.history.replaceState(null, "", "/");
  });
  afterEach(() => { window.history.replaceState(null, "", "/"); });

  it("readout: a real empty state before any prediction (no 50/50 bars, no dashes)", async () => {
    installFakeApi(appRoutes());
    render(<App />);
    await advance();
    const panel = readout();
    expect(within(panel).getByText("Predict")).toBeInTheDocument();
    expect(panel.querySelector(".readout-empty")?.textContent).toBe("Predict");
    expect(panel).not.toHaveTextContent("NO PREDICTION YET");
    expect(panel.querySelector(".prob-track")).toBeNull();
    expect(panel.querySelector(".probability-card")).toBeNull();
    expect(panel).not.toHaveTextContent("—");
  });

  it("readout: shows the bars once the model has answered", async () => {
    installFakeApi(appRoutes());
    render(<App />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /Predict/ }));
    await advance();
    const panel = readout();
    expect(within(panel).queryByText("Predict")).toBeNull();
    expect(panel).toHaveTextContent("Counter-Terrorists");
    expect(panel).toHaveTextContent("64.3%");
    expect(panel.querySelectorAll(".prob-track")).toHaveLength(2);
  });

  it("nav: a menu button keeps every link reachable and closes on use / Escape", async () => {
    window.history.replaceState(null, "", "/");
    installFakeApi(appRoutes());
    render(<App />);
    await advance();
    const toggle = screen.getByRole("button", { name: "Open menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "main-nav");
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).getByRole("link", { name: "Matches" })).toHaveAttribute("href", "/");

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelector(".topbar")).toHaveClass("menu-open");
    fireEvent.click(within(nav).getByRole("link", { name: "Matches" }));
    expect(document.querySelector(".topbar")).not.toHaveClass("menu-open");

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-expanded", "false");
  });
});

describe("home restores the round predictor", () => {
  beforeEach(() => { vi.useFakeTimers(); window.history.replaceState(null, "", "/"); });
  afterEach(() => { window.history.replaceState(null, "", "/"); });

  it("signed-out home: predictor + readout sit above matches chrome", async () => {
    installFakeApi(appRoutes());
    render(<App />);
    await advance();
    const predictor = document.getElementById("predictor") as HTMLElement;
    expect(predictor).toHaveClass("workspace");
    expect(within(predictor).getByText("READOUT")).toBeInTheDocument();
    expect(within(predictor).getByRole("button", { name: /Predict/ })).toBeInTheDocument();
    const matches = screen.getByRole("region", { name: "Your CS2 matches" });
    expect(predictor.compareDocumentPosition(matches) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Model / About stay off home
    expect(screen.queryByText("THE MODEL")).not.toBeInTheDocument();
    expect(screen.queryByText("HELD-OUT ACCURACY")).not.toBeInTheDocument();
  });

  it("signed-in home: predictor remains the primary block above matches", async () => {
    installFakeApi(appRoutes({
      "GET /steam/status": { status: 200, body: { enabled: true } },
      "GET /me": { status: 200, body: meFixture },
      "GET /matches?limit=50&offset=0": { status: 200, body: matchesFixture },
      "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
      "GET /steam/sync": { status: 200, body: { ...meFixture.sync, jobs: [] } },
      "GET /matches/summary": { status: 200, body: summaryPersonal },
    }));
    render(<App />);
    await advance();
    const predictor = document.getElementById("predictor");
    expect(predictor).toBeTruthy();
    expect(predictor!.querySelector(".result-panel")).toBeTruthy();
    const matches = screen.getByRole("region", { name: "Your CS2 matches" });
    expect(predictor!.compareDocumentPosition(matches) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Predictor" })).not.toBeInTheDocument(); // no standalone /predict page
  });

  it("/predict redirects to home #predictor and keeps the workspace", async () => {
    window.history.replaceState(null, "", "/predict");
    installFakeApi(appRoutes());
    render(<App />);
    await advance();
    expect(window.location.pathname + window.location.hash).toBe("/#predictor");
    expect(screen.queryByRole("heading", { name: "Predictor" })).not.toBeInTheDocument();
    expect(document.getElementById("predictor")).toHaveClass("workspace");
    expect(screen.getByRole("region", { name: "Round winner predictor" })).toHaveAttribute("id", "predictor");
  });
});

describe("nav: Account stays reachable", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { window.history.replaceState(null, "", "/"); });

  it("home: Account is a menu link (not hidden by the narrow-screen rules)", async () => {
    installFakeApi(appRoutes());
    render(<App />);
    await advance();
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(nav).toHaveClass("main-nav");
    expect(within(nav).getByRole("link", { name: "Account" })).toHaveAttribute("href", "/account");
    expect(within(nav).getByRole("link", { name: "Predictor" })).toHaveAttribute("href", "/#predictor");
    expect(within(nav).getByRole("link", { name: "Stats" })).toHaveAttribute("href", "/stats");
    expect(within(nav).getByRole("link", { name: "About" })).toHaveAttribute("href", "/about");
  });

  it("/account: same menu button; Account is current, sections link to pages", async () => {
    window.history.replaceState(null, "", "/account");
    installFakeApi(appRoutes());
    render(<App />);
    await advance();
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(nav).toHaveClass("main-nav");
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-controls", "main-nav");
    expect(within(nav).getByRole("link", { name: "Account" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Matches" })).toHaveAttribute("href", "/");
  });
});

describe("matches teaser", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  const steamRoutes = (me: { status: number; body?: unknown }) => ({
    "GET /steam/status": { status: 200, body: { enabled: true } },
    "GET /me": me,
    "GET /matches?limit=50&offset=0": { status: 200, body: matchesFixture },
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...meFixture.sync, jobs: [] } },
  });

  it("signed out: a labelled sample report under the sign-in card", async () => {
    installFakeApi(steamRoutes({ status: 401, body: { detail: "not signed in" } }));
    render(<SteamSection />);
    await advance();
    const teaser = screen.getByRole("figure", { name: "Sample match report" });
    expect(teaser).toHaveTextContent("SAMPLE REPORT");
    expect(teaser).not.toHaveTextContent(/what you get/i);
    expect(teaser.querySelector(".teaser-report")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("link", { name: /Sign in through Steam/ })).toBeInTheDocument();
  });

  it("signed in: no teaser, the real list instead", async () => {
    installFakeApi(steamRoutes({ status: 200, body: meFixture }));
    render(<SteamSection />);
    await advance();
    expect(screen.queryByRole("figure", { name: "Sample match report" })).toBeNull();
    expect(screen.getByRole("button", { name: /mirage/i })).toBeInTheDocument();
  });
});
