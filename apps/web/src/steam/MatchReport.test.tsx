import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import matchesFixture from "../test/fixtures/matches.json";
import meFixture from "../test/fixtures/me.json";
import reportFixture from "../test/fixtures/report.json";
import { SteamSection } from "./SteamSection";

const MATCH = matchesFixture.matches[0];

function routes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /steam/status": { status: 200, body: { enabled: true } },
    "GET /me": { status: 200, body: meFixture },
    "GET /matches?limit=50&offset=0": { status: 200, body: matchesFixture },
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...meFixture.sync, jobs: [] } },
    [`GET /matches/${MATCH.id}`]: { status: 200, body: reportFixture },
    ...extra,
  };
}

describe("match list and per-round report (real API response shapes)", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("signed in: lists the match and renders every round of its report", async () => {
    const api = installFakeApi(routes());
    render(<SteamSection />);
    await advance();
    expect(screen.getByText("Signed in as", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Account settings" })[0]).toHaveAttribute("href", "/account");
    expect(screen.queryByRole("form", { name: "Link match history" })).toBeNull();
    const item = screen.getByRole("button", { name: /mirage/i });
    expect(item).toHaveTextContent("10 rounds");
    expect(item).toHaveTextContent("UPLOADED");
    expect(api.count(`GET /matches/${MATCH.id}`)).toBe(0);

    fireEvent.click(item);
    await advance();
    expect(api.count(`GET /matches/${MATCH.id}`)).toBe(1);
    // header: map, score, date, source (no essay captions)
    const header = screen.getByLabelText("Match summary");
    expect(header).toHaveTextContent("MAPmirage");
    expect(header).toHaveTextContent("SCORE8 – 2");
    expect(header).not.toHaveTextContent("sides at the end");
    expect(header).toHaveTextContent(`DATE${new Date(reportFixture.match.imported_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}`);
    expect(header).toHaveTextContent("SOURCEUpload");

    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(10);
    const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);
    // round 1: T p250 opening at 20.7 s, T won, model favoured T 71%
    expect(cells(rows[0])).toEqual(["1", "T · P250 · 20.7s", "T", "T favoured · CT 29% / T 71%"]);
    // weapon codes are shown with their in-game names
    const openings = rows.map((row) => cells(row)[1]).join(" ");
    expect(openings).not.toMatch(/m4a1_silencer|usp_silencer|galilar|p250/);
    // round 5: T opened but CT won (a model miss)
    expect(cells(rows[4])[0]).toBe("5");
    expect(cells(rows[4])[2]).toBe("CT");
    expect(cells(rows[4])[3]).toMatch(/^T favoured/);
    // round 10: no opening kill -> unscored, reason in plain language
    expect(cells(rows[9])).toEqual(["10", "—", "T", "—"]);
    expect(screen.getByText(/Some rounds unscored/)).toBeInTheDocument();
    expect(rows[9]).toHaveClass("unscored-row");

    fireEvent.click(item);  // collapses
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("report header falls back when the score is unknown and names Steam sync as the source", async () => {
    const synced = { ...reportFixture, match: { ...reportFixture.match, source: "steam_sync", score: null } };
    installFakeApi(routes({ [`GET /matches/${MATCH.id}`]: { status: 200, body: synced } }));
    render(<SteamSection />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /mirage/i }));
    await advance();
    const header = screen.getByLabelText("Match summary");
    expect(header).toHaveTextContent("SCORE—");
    expect(header).toHaveTextContent("SOURCESteam sync");
  });

  it("shows unavailable / stub matches without a report", async () => {
    const stub = { ...MATCH, id: "stub", source: "steam_sync", status: "unavailable", status_reason: "demo_unavailable", map_name: null, rounds_count: 0 };
    const api = installFakeApi(routes({ "GET /matches?limit=50&offset=0": { status: 200, body: { matches: [stub], limit: 50, offset: 0 } } }));
    render(<SteamSection />);
    await advance();
    const item = screen.getByRole("button", { name: /Unknown map/ });
    expect(item).toHaveTextContent("NOT IMPORTED");
    expect(item).not.toHaveTextContent(/Valve/);
    fireEvent.click(item);
    await advance();
    expect(screen.getByRole("note")).toHaveTextContent("Demo gone from Valve — upload the .dem above if you have it.");
    expect(api.count("GET /matches/stub")).toBe(0);
  });

  it("signed out: offers Steam sign-in and no match list", async () => {
    installFakeApi(routes({ "GET /me": { status: 401, body: { detail: "not_authenticated" } } }));
    render(<SteamSection />);
    await advance();
    expect(screen.getByRole("link", { name: /Sign in through Steam/ })).toHaveAttribute("href", expect.stringContaining("/auth/steam/login?next=%2F%23matches"));
    expect(screen.queryByText("STEP 3 · IMPORT MATCHES")).not.toBeInTheDocument();
  });

  it("shows a coming-soon empty state (no sign-in, no upload) when the deployment has Steam features disabled", async () => {
    const api = installFakeApi(routes({ "GET /steam/status": { status: 200, body: { enabled: false } } }));
    render(<SteamSection />);
    await advance();
    expect(screen.getByText("Match reports are on the way")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Sign in through Steam/ })).toBeNull();
    expect(api.count("GET /me")).toBe(0);
  });

  it("says the match service is offline when GET /steam/status can't be reached", async () => {
    installFakeApi({ "GET /steam/status": { status: 0 } });
    render(<SteamSection />);
    await advance();
    expect(screen.getByText("Can’t reach the match service")).toBeInTheDocument();
  });

  it("blames our server, not Valve, when the API itself cannot be reached", async () => {
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": [{ status: 200, body: matchesFixture }],
      [`GET /matches/${MATCH.id}`]: { status: 0 },
    }));
    render(<SteamSection />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /mirage/i }));
    await advance();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Can’t reach the cs-analytics server.");
    expect(alert).not.toHaveTextContent("Valve");
  });
});
