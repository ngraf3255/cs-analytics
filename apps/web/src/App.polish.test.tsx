import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { advance, installFakeApi } from "./test/fakeApi";
import meFixture from "./test/fixtures/me.json";
import matchesFixture from "./test/fixtures/matches.json";
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

const readout = () => screen.getByText("MODEL READOUT").closest(".result-panel") as HTMLElement;

describe("design review polish", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("readout: a real empty state before any prediction (no 50/50 bars, no dashes)", async () => {
    installFakeApi(appRoutes());
    render(<App />);
    await advance();
    const panel = readout();
    expect(within(panel).getByText("Predict to see the odds")).toBeInTheDocument();
    expect(panel.querySelector(".readout-empty")?.textContent).toBe("Predict to see the odds");
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
    expect(within(panel).queryByText("Predict to see the odds")).toBeNull();
    expect(panel).toHaveTextContent("Counter-Terrorists");
    expect(panel).toHaveTextContent("64.3%");
    expect(panel.querySelectorAll(".prob-track")).toHaveLength(2);
  });

  it("nav: a menu button keeps every link (incl. My matches) reachable and closes on use / Escape", async () => {
    installFakeApi(appRoutes());
    render(<App />);
    await advance();
    const toggle = screen.getByRole("button", { name: "Open menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "main-nav");
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).getByRole("link", { name: "My matches" })).toHaveAttribute("href", "#matches");

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelector(".topbar")).toHaveClass("menu-open");
    fireEvent.click(within(nav).getByRole("link", { name: "My matches" }));
    expect(document.querySelector(".topbar")).not.toHaveClass("menu-open");

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-expanded", "false");
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
    // decorative sample: it must not look like (or be read out as) the user's real data
    expect(teaser.querySelector(".teaser-report")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("table")).toBeNull();
    // Sign in through Steam is still the way in
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
