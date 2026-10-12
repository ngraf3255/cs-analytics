import { redirectLegacyPredict } from "./steam/routes";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { HERO_AUTH_TIMEOUT_MS } from "./steam/GuestHero";
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
    expect(panel).toHaveClass("result-panel-idle"); // collapses on mobile until a prediction exists
    expect(panel.querySelector(".readout-empty")).toBeNull();
    expect(panel).not.toHaveTextContent("Logistic regression");
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
    const info = panel.querySelector("details.model-info-pop");  // tap popover, not hover-only title
    expect(info).toBeTruthy();
    expect(info!.querySelector("summary")).toHaveAccessibleName("Model info");
    expect(info!.querySelector("summary")).toHaveAttribute("role", "button");
    expect(info).toHaveTextContent("Logistic regression · pro data");
    expect(info).toHaveClass("info-pop-below");  // anchored under the icon, inside the viewport
    fireEvent.click(info!.querySelector("summary")!);
    await advance();
    expect(info).toHaveProperty("open", true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(info).toHaveProperty("open", false);
    fireEvent.click(info!.querySelector("summary")!);
    await advance();
    expect(info).toHaveProperty("open", true);
    fireEvent.mouseDown(document.body);
    expect(info).toHaveProperty("open", false);
    (info as HTMLDetailsElement).open = true;
    fireEvent.keyDown(screen.getByLabelText("Opening kill time in seconds"), { key: "Escape" });  // focus anywhere
    expect(info).toHaveProperty("open", false);
    (info as HTMLDetailsElement).open = true;
    fireEvent.pointerDown(screen.getByLabelText("Opening kill time in seconds"));  // input outside closes
    expect(info).toHaveProperty("open", false);
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
    redirectLegacyPredict(); // main.tsx runs this before the first render (no flash)
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

  it("header: badge slot reserved and MODEL ONLINE held until auth settles", async () => {
    installFakeApi(appRoutes());
    render(<App />);
    const header = document.querySelector("header.topbar")!;
    expect(header.querySelector(".account-avatar-slot")).toBeTruthy();
    expect(header.querySelector(".live-indicator")).toHaveClass("is-pending");  // laid out, not shown
    await advance();
    expect(header.querySelector(".account-avatar-slot")).toBeNull();
    expect(header.querySelector(".live-indicator")).not.toHaveClass("is-pending");
  });

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
    const teaser = screen.getByRole("figure", { name: "Example match report" });
    expect(teaser).toHaveTextContent("Example");
    expect(teaser).not.toHaveTextContent(/what you get/i);
    expect(teaser.querySelector(".teaser-report")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("link", { name: /Sign in through Steam/ })).toBeInTheDocument();
  });

  it("signed in: no teaser, the real list instead", async () => {
    installFakeApi(steamRoutes({ status: 200, body: meFixture }));
    render(<SteamSection />);
    await advance();
    expect(screen.queryByRole("figure", { name: "Example match report" })).toBeNull();
    expect(screen.getByRole("button", { name: /mirage/i })).toBeInTheDocument();
  });
});

describe("signed-in hard reload: no guest chrome, predictor ready immediately", () => {
  beforeEach(() => { vi.useFakeTimers(); window.history.replaceState(null, "", "/"); window.localStorage.clear(); });

  it("hint + signed in: no guest hero while /me is pending or after it answers", async () => {
    window.localStorage.setItem("csa_has_session", "1");
    let answer: (v: { status: number; body: unknown }) => void = () => undefined;
    installFakeApi(appRoutes({
      "GET /steam/status": { status: 200, body: { enabled: true, steam: true, upload: true } },
      "GET /me": () => new Promise((resolve) => { answer = resolve; }),
    }));
    render(<App />);
    await advance();  // status answered, /me still pending
    expect(screen.queryByText(/turning point/i)).toBeNull();
    answer({ status: 200, body: meFixture });
    await advance();
    expect(screen.queryByText(/turning point/i)).toBeNull();
  });

  it("predictor form is enabled with defaults on first render, before /options answers", () => {
    installFakeApi(appRoutes({ "GET /options": () => new Promise(() => undefined) }));
    render(<App />);
    expect(screen.getByLabelText("Map")).toBeEnabled();
    expect(screen.getByLabelText("Map")).not.toHaveValue("");
    expect(screen.getByLabelText("Opening weapon")).toHaveValue("ak47");
    expect(screen.getByRole("button", { name: /Predict/ })).toBeEnabled();
  });
});

describe("guest hero decided on first paint (session hint)", () => {
  beforeEach(() => { vi.useFakeTimers(); window.history.replaceState(null, "", "/"); window.localStorage.clear(); });
  const slowAuth = (me: () => Promise<{ status: number; body: unknown }>) => appRoutes({
    "GET /steam/status": { status: 200, body: { enabled: true, steam: true, upload: true } },
    "GET /me": me,
  });

  it("no hint: hero on the very first render, before any API answer", () => {
    installFakeApi(slowAuth(() => new Promise(() => undefined)));
    render(<App />);
    expect(screen.getByText(/turning point/i)).toBeInTheDocument();
  });

  it("hint + 401: hero appears once auth says guest, and the hint is cleared", async () => {
    window.localStorage.setItem("csa_has_session", "1");
    installFakeApi(slowAuth(async () => ({ status: 401, body: { detail: "not_signed_in" } })));
    render(<App />);
    expect(screen.queryByText(/turning point/i)).toBeNull();
    await advance();
    expect(screen.getByText(/turning point/i)).toBeInTheDocument();
    expect(window.localStorage.getItem("csa_has_session")).toBeNull();
  });

  it("hint + /me error: hero still appears", async () => {
    window.localStorage.setItem("csa_has_session", "1");
    installFakeApi(slowAuth(async () => ({ status: 500, body: { detail: "boom" } })));
    render(<App />);
    await advance();
    expect(screen.getByText(/turning point/i)).toBeInTheDocument();
  });

  it("hint + /me hangs: hero appears after the 4 s timeout", async () => {
    window.localStorage.setItem("csa_has_session", "1");
    installFakeApi(slowAuth(() => new Promise(() => undefined)));
    render(<App />);
    await advance();
    expect(screen.queryByText(/turning point/i)).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(HERO_AUTH_TIMEOUT_MS + 10); });
    expect(screen.getByText(/turning point/i)).toBeInTheDocument();
  });

  it("successful /me sets the hint", async () => {
    installFakeApi(slowAuth(async () => ({ status: 200, body: meFixture })));
    render(<App />);
    await advance();
    expect(window.localStorage.getItem("csa_has_session")).toBe("1");
    expect(screen.queryByText(/turning point/i)).toBeNull();
  });
});
