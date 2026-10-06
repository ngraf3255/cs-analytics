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
  it("always shows CT–T (not winner-first) and keeps CT/T detail", () => {
    expect(scoreLine({ ct: 2, t: 8 })).toEqual({ primary: "2–8", detail: "CT 2 · T 8" });
    expect(scoreLine({ ct: 13, t: 11 })).toEqual({ primary: "13–11", detail: "CT 13 · T 11" });
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
    expect(within(first).getByText("CT–T")).toBeInTheDocument();
    expect(screen.queryByText(/Tap a match/i)).not.toBeInTheDocument();
    const ancient = screen.getByRole("button", { name: /ancient/i });
    expect(within(ancient).getByText("6–2")).toBeInTheDocument();
    const second = screen.getAllByRole("button", { name: /mirage/i })[1];
    expect(within(second).getByText("2–8")).toBeInTheDocument();
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
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(row).not.toHaveTextContent(/Valve/i);
    fireEvent.click(row);
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("Demo gone from Valve — upload the .dem above if you have it.");
    expect(note.querySelectorAll("p")).toHaveLength(1);
  });

  it("re-opening a report uses the copy loaded this list load (no refetch)", async () => {
    const api = installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    const id = matchesFixture.matches[0].id;
    const row = screen.getAllByRole("button", { expanded: false })[0];
    fireEvent.click(row);
    await advance();
    expect(screen.getByLabelText("Match summary")).toBeInTheDocument();
    fireEvent.click(row);
    fireEvent.click(row);
    expect(screen.getByLabelText("Match summary")).toBeInTheDocument();
    expect(api.count(`GET /matches/${id}`)).toBe(1);
  });
});
