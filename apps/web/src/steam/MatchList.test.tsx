import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";
import matchesFixture from "../test/fixtures/matches.json";
import matchesPersonal from "../test/fixtures/matches_personal.json";
import reportFixture from "../test/fixtures/report.json";
import { Matches } from "./Matches";
import { scoreLine } from "./format";
import type { Me } from "./types";

const me = meFixture as Me;

function routes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /matches?limit=50&offset=0": { status: 200, body: matchesFixture },
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...me.sync, jobs: [] } },
    [`GET /matches/${matchesFixture.matches[0].id}`]: { status: 200, body: reportFixture },
    "GET /matches/summary": { status: 404, body: { detail: { error: "match_not_found" } } },
    ...extra,
  };
}

describe("scoreLine", () => {
  it("puts the higher side first and keeps CT/T detail", () => {
    expect(scoreLine({ ct: 2, t: 8 })).toEqual({ primary: "8–2", detail: "CT 2 · T 8" });
    expect(scoreLine(null)).toBeNull();
  });
});

describe("match list UX", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("shows a loading skeleton before the first list response (not the empty state)", async () => {
    let resolve!: (r: { status: number; body: unknown }) => void;
    const pending = new Promise<{ status: number; body: unknown }>((r) => { resolve = r; });
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": async () => pending,
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    expect(screen.getByLabelText("Loading matches")).toBeInTheDocument();
    expect(screen.queryByLabelText("No matches yet")).not.toBeInTheDocument();

    resolve({ status: 200, body: { matches: [], limit: 50, offset: 0 } });
    await advance();
    expect(screen.queryByLabelText("Loading matches")).not.toBeInTheDocument();
    expect(screen.getByLabelText("No matches yet")).toBeInTheDocument();
  });

  it("shows an error with Try again when the list fails, and recovers on retry", async () => {
    const api = installFakeApi(routes({
      "GET /matches?limit=50&offset=0": [
        { status: 500, body: { detail: "internal_error" } },
        { status: 200, body: matchesFixture },
      ],
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("No matches yet")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await advance();
    expect(api.count("GET /matches?limit=50&offset=0")).toBe(2);
    expect(screen.getByRole("button", { name: /mirage/i })).toBeInTheDocument();
    expect(screen.getByText("1 MATCH")).toBeInTheDocument();
  });

  it("renders score-first rows and opens the report on click", async () => {
    const faceitId = matchesPersonal.matches[0].id;
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": { status: 200, body: matchesPersonal },
      [`GET /matches/${faceitId}`]: {
        status: 200,
        body: { ...reportFixture, match: { ...reportFixture.match, ...matchesPersonal.matches[0] } },
      },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();

    expect(screen.getByText("3 MATCHES")).toBeInTheDocument();
    const first = screen.getAllByRole("button", { name: /mirage/i })[0];
    expect(within(first).getByText("13–11")).toBeInTheDocument();
    expect(within(first).getByText("CT 13 · T 11")).toBeInTheDocument();
    expect(within(first).getByText("24 rounds")).toBeInTheDocument();

    fireEvent.click(first);
    await advance();
    expect(first).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Match summary")).toBeInTheDocument();
  });

  it("shows a stub note for matches that did not import", async () => {
    const stub = {
      matches: [{
        id: "stub1",
        source: "steam_sync",
        share_code: "CSGO-xxxx",
        status: "unavailable",
        status_reason: "demo_unavailable",
        map_name: "de_dust2",
        rounds_count: 0,
        imported_at: "2026-10-05T09:48:31.857028Z",
        date: "2026-10-05T09:48:31.857028Z",
        date_source: "played",
        score: null,
      }],
      limit: 50,
      offset: 0,
    };
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": { status: 200, body: stub },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();

    const row = screen.getByRole("button", { name: /dust2/i });
    expect(within(row).getByText("NOT IMPORTED")).toBeInTheDocument();
    fireEvent.click(row);
    expect(screen.getByRole("note")).toHaveTextContent(/no longer available from Valve/i);
    expect(screen.getByRole("note")).toHaveTextContent(/upload it above/i);
  });
});
