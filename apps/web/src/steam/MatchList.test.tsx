import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";
import matchesFixture from "../test/fixtures/matches.json";
import matchesPersonal from "../test/fixtures/matches_personal.json";
import reportFixture from "../test/fixtures/report.json";
import summaryPersonal from "../test/fixtures/summary_personal.json";
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
    "GET /matches/summary?recent=50": { status: 404, body: { detail: { error: "match_not_found" } } },
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

  it("renders [W/L] MAP score · metric rows and opens timeline+Share on click (no restating tiles)", async () => {
    const faceitId = matchesPersonal.matches[0].id;
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": { status: 200, body: matchesPersonal },
      "GET /matches/summary?recent=50": { status: 200, body: summaryPersonal },
      [`GET /matches/${faceitId}`]: {
        status: 200,
        body: { ...reportFixture, match: { ...reportFixture.match, ...matchesPersonal.matches[0] }, you: {
          status: "in_match", steam_id: "1", first_side: "t", last_side: "ct", rounds: 24, won: 13,
          win_rate: 0.54, kills: 29, deaths: 17, kd: 1.71, opening_kills: 4, opening_deaths: 0, survived: 7,
          score: { you: 13, them: 11 }, result: "won",
        } },
      },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();

    expect(screen.getByText("3 MATCHES")).toBeInTheDocument();
    // Personal match: W chip + your-side score + K-D metric
    const won = screen.getByRole("button", { name: /Won · mirage · score 13–11 · K-D 29–17/i });
    expect(within(won).getByText("W")).toBeInTheDocument();
    expect(within(won).getByText("13–11")).toBeInTheDocument();
    expect(within(won).getByText("· 29–17")).toBeInTheDocument();
    expect(within(won).queryByText("CT–T")).not.toBeInTheDocument();
    expect(within(won).getByLabelText("Uploaded")).toHaveTextContent("↑");

    // Matches without personal bits: CT–T score fallback + rounds metric, no W/L chip
    const ancient = screen.getByRole("button", { name: /ancient · 6–2 · 8r/i });
    expect(within(ancient).queryByText("W")).not.toBeInTheDocument();
    expect(within(ancient).getByText("6–2")).toBeInTheDocument();
    expect(within(ancient).getByText("· 8r")).toBeInTheDocument();

    const synced = screen.getByRole("button", { name: /mirage · 2–8 · 10r/i });
    expect(within(synced).getByText("2–8")).toBeInTheDocument();

    fireEvent.click(won);
    await advance();
    expect(won).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /Share match/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Match summary")).not.toBeInTheDocument();
    expect(screen.queryByText(/^MAP$/)).not.toBeInTheDocument();
  });

  it("shows a stub glyph for matches that did not import", async () => {
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
    expect(within(row).getByLabelText("Not imported")).toHaveTextContent("⊘");
    expect(within(row).queryByText("NOT IMPORTED")).not.toBeInTheDocument();
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(row).not.toHaveTextContent(/Valve/i);
    fireEvent.click(row);
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("Demo gone from Valve — upload the .dem above if you have it.");
    expect(note.querySelectorAll("p")).toHaveLength(1);
  });

  it("renders a degraded glyph with thin-stats tooltip when the API marks the parse degraded", async () => {
    const body = {
      matches: [{
        ...matchesFixture.matches[0],
        id: "degraded1",
        map_name: "cs_rush",
        status: "imported",
        status_reason: "parse_degraded",
        degraded: {
          reason: "packet_ents_skipped",
          detail: "PacketEntities skips — positions thin; Rush may omit round_end (recovered from officially-ended).",
        },
      }],
      limit: 50,
      offset: 0,
    };
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": { status: 200, body },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();

    const row = screen.getByRole("button", { name: /rush/i });
    const tag = within(row).getByLabelText("Degraded");
    expect(tag).toHaveTextContent("⚠");
    expect(tag).toHaveAttribute(
      "title",
      "PacketEntities skips — positions thin; Rush may omit round_end (recovered from officially-ended).",
    );
    expect(within(row).queryByText("DEGRADED")).not.toBeInTheDocument();
    expect(within(row).queryByLabelText("Not imported")).not.toBeInTheDocument();
  });

  it("re-opening a report uses the copy loaded this list load (no refetch)", async () => {
    const api = installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    const id = matchesFixture.matches[0].id;
    const row = screen.getAllByRole("button", { expanded: false })[0];
    fireEvent.click(row);
    await advance();
    expect(screen.getByRole("button", { name: /Share match/i })).toBeInTheDocument();
    fireEvent.click(row);
    fireEvent.click(row);
    expect(screen.getByRole("button", { name: /Share match/i })).toBeInTheDocument();
    expect(api.count(`GET /matches/${id}`)).toBe(1);
  });

  it("shows a day group header even for a 1-match list", async () => {
    vi.setSystemTime(new Date("2026-10-06T15:00:00-05:00"));
    const body = {
      matches: [{
        ...matchesFixture.matches[0],
        id: "oneday",
        map_name: "cs_rush",
        imported_at: "2026-10-06T12:00:00-05:00",
        date: "2026-10-06T12:00:00-05:00",
        date_source: "imported",
        score: { ct: 0, t: 1 },
        rounds_count: 1,
      }],
      limit: 50,
      offset: 0,
    };
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": { status: 200, body },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByText("1 MATCH")).toBeInTheDocument();
    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /rush/i })).toBeInTheDocument();
  });

  it("bold score is your rounds won–lost, not remapped CT–T you.score", async () => {
    vi.setSystemTime(new Date("2026-10-06T15:00:00-05:00"));
    const rushId = "rush-regrade";
    const match = {
      id: rushId,
      source: "upload",
      share_code: null,
      status: "imported",
      status_reason: "parse_degraded",
      map_name: "rush_001",
      rounds_count: 1,
      imported_at: "2026-10-06T12:00:00-05:00",
      date: "2026-10-06T12:00:00-05:00",
      date_source: "imported",
      // Degraded aggregate disagrees with the 1-round T LOST table:
      score: { ct: 0, t: 2 },
      players_recorded: true,
      outdated: null,
      degraded: { reason: "packet_ents_skipped", detail: "thin" },
    };
    const summary = {
      ...summaryPersonal,
      you: {
        ...summaryPersonal.you,
        matches: 1,
        recent_form: {
          ...summaryPersonal.you.recent_form,
          matches: [{
            id: rushId,
            map_name: "rush_001",
            date: "2026-10-06T12:00:00-05:00",
            date_source: "imported",
            played_at: null,
            first_side: "t",
            rounds: 1,
            won: 0,
            win_rate: 0,
            kills: 0,
            deaths: 1,
            // Remapped CT–T (wrong for this demo) — UI must prefer rounds/won:
            score: { you: 0, them: 2 },
            result: "lost",
          }],
        },
      },
    };
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": { status: 200, body: { matches: [match], limit: 50, offset: 0 } },
      "GET /matches/summary?recent=50": { status: 200, body: summary },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();

    const row = screen.getByRole("button", { name: /Lost · rush 001 · score 0–1 · K-D 0–1/i });
    expect(within(row).getByText("L")).toBeInTheDocument();
    expect(within(row).getByText("0–1")).toBeInTheDocument();
    // Bold primary is the first strong; K-D is in small — ensure we did not show 0–2.
    expect(within(row).queryByText("0–2")).not.toBeInTheDocument();
    expect(screen.getByText("Today")).toBeInTheDocument();
  });
});
