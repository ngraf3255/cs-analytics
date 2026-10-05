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
    expect(screen.getByText("SteamID", { exact: false })).toBeInTheDocument();
    const item = screen.getByRole("button", { name: /mirage/i });
    expect(item).toHaveTextContent("10 rounds");
    expect(item).toHaveTextContent("UPLOADED");
    expect(api.count(`GET /matches/${MATCH.id}`)).toBe(0);

    fireEvent.click(item);
    await advance();
    expect(api.count(`GET /matches/${MATCH.id}`)).toBe(1);
    expect(screen.getByRole("note")).toHaveTextContent("Retrospective estimate, not calibrated for your games.");
    expect(screen.getByRole("note")).toHaveTextContent(reportFixture.model.note);
    expect(screen.getByText("9 of 10 rounds could be scored. The model’s favourite won 8 of 9.")).toBeInTheDocument();

    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(10);
    const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);
    // round 1: T p250 opening at 20.7 s, T won, model favoured T 71%
    expect(cells(rows[0])).toEqual(["1", "T · p250 · 20.7s", "T", "T favoured · CT 29% / T 71%"]);
    // round 5: T opened but CT won (a model miss)
    expect(cells(rows[4])[0]).toBe("5");
    expect(cells(rows[4])[2]).toBe("CT");
    expect(cells(rows[4])[3]).toMatch(/^T favoured/);
    // round 10: no opening kill -> unscored, reason in plain language
    expect(cells(rows[9])).toEqual(["10", "—", "T", "Unscored: No opening kill recorded in this round."]);
    expect(rows[9]).toHaveClass("unscored-row");

    fireEvent.click(item);  // collapses
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows unavailable / stub matches without a report", async () => {
    const stub = { ...MATCH, id: "stub", source: "steam_sync", status: "unavailable", status_reason: "demo_unavailable", map_name: null, rounds_count: 0 };
    const api = installFakeApi(routes({ "GET /matches?limit=50&offset=0": { status: 200, body: { matches: [stub], limit: 50, offset: 0 } } }));
    render(<SteamSection />);
    await advance();
    const item = screen.getByRole("button", { name: /Unknown map/ });
    expect(item).toHaveTextContent("Demo is no longer available from Valve.");
    fireEvent.click(item);
    await advance();
    expect(api.count("GET /matches/stub")).toBe(0);
  });

  it("signed out: offers Steam sign-in and no match list", async () => {
    installFakeApi(routes({ "GET /me": { status: 401, body: { detail: "not_authenticated" } } }));
    render(<SteamSection />);
    await advance();
    expect(screen.getByRole("link", { name: /Sign in through Steam/ })).toHaveAttribute("href", expect.stringContaining("/auth/steam/login?next=%2F%23matches"));
    expect(screen.queryByText("STEP 3 · IMPORT MATCHES")).not.toBeInTheDocument();
  });

  it("renders nothing when the deployment has Steam features disabled", async () => {
    installFakeApi(routes({ "GET /steam/status": { status: 200, body: { enabled: false } } }));
    const { container } = render(<SteamSection />);
    await advance();
    expect(container).toBeEmptyDOMElement();
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
