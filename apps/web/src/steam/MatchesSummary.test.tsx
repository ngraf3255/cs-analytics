import { act as rtlAct, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, FakeXHR, installFakeApi } from "../test/fakeApi";
import matchesFixture from "../test/fixtures/matches.json";
import matchesFour from "../test/fixtures/matches_four.json";
import meFixture from "../test/fixtures/me.json";
import summaryEmpty from "../test/fixtures/summary_empty.json";
import summaryOne from "../test/fixtures/summary_one.json";
import summaryFixture from "../test/fixtures/summary.json";
import summaryRecent2 from "../test/fixtures/summary_recent2.json";
import uploadDone from "../test/fixtures/upload_done.json";
import { Matches } from "./Matches";
import type { Me } from "./types";

// Fixtures: real GET /matches/summary responses of the local API (SQLite, 2026-10-05): 4 imported
// matches (Valve MM de_ancient 8r via fake-Valve sync, HLTV de_nuke 18r, demoparser2 de_mirage 10r,
// FACEIT de_mirage 25r uploads), the same with ?recent=2, one match (de_mirage 10r), and a new user.
const me = meFixture as Me;
const EMPTY_LIST = { matches: [], limit: 50, offset: 0 };

function routes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /matches?limit=50&offset=0": { status: 200, body: matchesFour },
    "GET /matches/summary": { status: 200, body: summaryFixture },
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...me.sync, jobs: [] } },
    ...extra,
  };
}

const panel = () => screen.getByRole("region", { name: "Across your matches" });
const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);

describe("summary across previous matches (GET /matches/summary)", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("shows totals, model accuracy, sides, opening kills and per-map rows above the match list", async () => {
    const api = installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(api.count("GET /matches/summary")).toBe(1);  // once per list load, not before it

    const tiles = within(panel()).getByLabelText("Previous matches summary");
    expect(tiles).toHaveTextContent("MATCHES461 rounds · 59 scored");
    expect(tiles).toHaveTextContent("MODEL HIT RATE88%52 of 59 rounds · opening-kill side 86%");
    expect(tiles).toHaveTextContent("BRIER SCORE0.133Lower is better · coin flip 0.25");
    expect(tiles).toHaveTextContent("CT / T ROUNDS56% / 44%CT side won 34 of 61");
    expect(tiles).toHaveTextContent("OPENING KILL WINS86%Round won by the side with the first kill · CT 90% · T 82%");
    expect(panel()).toHaveTextContent(
      "Last 4 matches: the model’s favourite won 88% of scored rounds. Import more than 10 matches to see a trend.");

    const maps = within(within(panel()).getByRole("table", { name: "By map" })).getAllByRole("row").slice(1);
    expect(maps.map(cells)).toEqual([
      ["mirage", "2", "35", "54%", "85% (29/34)", "0.144"],
      ["nuke", "1", "18", "50%", "89% (16/18)", "0.131"],
      ["ancient", "1", "8", "75%", "100% (7/7)", "0.088"],
    ]);
    const calibration = within(within(panel()).getByRole("table", { name: "Model calibration" })).getAllByRole("row").slice(1);
    expect(calibration.map(cells)).toEqual([  // empty confidence bins are left out
      ["50–60%", "1", "51%", "100%"], ["60–70%", "7", "65%", "100%"], ["70–80%", "47", "73%", "85%"], ["80–90%", "4", "84%", "100%"],
    ]);
    const weapons = within(within(panel()).getByRole("table", { name: "Opening weapons" })).getAllByRole("row").slice(1);
    expect(weapons.map((row) => cells(row)[0])).toEqual(["AK-47", "M4A1-S", "AWP", "MAC-10", "USP-S"]);
    expect(panel()).toHaveTextContent("Unscored rounds: 2 × No opening kill recorded in this round.");
    expect(panel()).toHaveTextContent("your own team isn’t tracked yet");

    // Above the list of matches.
    const firstMatch = screen.getAllByRole("button", { name: /mirage/i })[0];
    expect(panel().compareDocumentPosition(firstMatch) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
  });

  it("recent form compares the last N matches with the ones before", async () => {
    installFakeApi(routes({ "GET /matches/summary": { status: 200, body: summaryRecent2 } }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(panel()).toHaveTextContent(
      "Last 2 matches: the model’s favourite won 85% of scored rounds, vs 92% over the 2 matches before (−7 pts).");
  });

  it("no matches: no panel, just the empty list", async () => {
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": { status: 200, body: EMPTY_LIST },
      "GET /matches/summary": { status: 200, body: summaryEmpty },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByText("No imported matches yet.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Across your matches" })).not.toBeInTheDocument();
  });

  it("only stub matches (demo unavailable): says nothing could be analysed yet", async () => {
    const stubs = { ...summaryEmpty, totals: { ...summaryEmpty.totals, matches: 2, not_imported_matches: 2 } };
    installFakeApi(routes({ "GET /matches/summary": { status: 200, body: stubs } }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByText(/None of your matches could be analysed yet/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Across your matches" })).not.toBeInTheDocument();
  });

  it("no scored rounds: dashes instead of rates", async () => {
    const unscored = {
      ...summaryOne,
      prediction: { ...summaryEmpty.prediction },
      maps: summaryOne.maps.map((m) => ({ ...m, scored_rounds: 0, correct: 0, hit_rate: null, brier_score: null })),
    };
    installFakeApi(routes({ "GET /matches/summary": { status: 200, body: unscored } }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    const tiles = within(panel()).getByLabelText("Previous matches summary");
    expect(tiles).toHaveTextContent("MODEL HIT RATE—No scorable rounds yet");
    expect(tiles).toHaveTextContent("BRIER SCORE—");
    const row = within(within(panel()).getByRole("table", { name: "By map" })).getAllByRole("row")[1];
    expect(cells(row)).toEqual(["mirage", "1", "10", "20%", "Not scored", "—"]);
    expect(within(panel()).queryByRole("table", { name: "Model calibration" })).not.toBeInTheDocument();
  });

  it("an older API without the route (404) shows no panel; other errors are quiet, not alerts", async () => {
    installFakeApi(routes({ "GET /matches/summary": { status: 404, body: { detail: "match_not_found" } } }));
    const { unmount } = render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.queryByRole("region", { name: "Across your matches" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Analytics across your matches/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    unmount();

    installFakeApi(routes({ "GET /matches/summary": { status: 0 } }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByText(/Analytics across your matches couldn’t be loaded. Can’t reach the cs-analytics server/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("refreshes after an import adds a match", async () => {
    FakeXHR.install();
    const api = installFakeApi(routes({
      "GET /matches?limit=50&offset=0": [{ status: 200, body: EMPTY_LIST }, { status: 200, body: matchesFixture }],
      "GET /matches/summary": [{ status: 200, body: summaryEmpty }, { status: 200, body: summaryOne }],
      [`GET /matches/${matchesFixture.matches[0].id}`]: { status: 404, body: { detail: "match_not_found" } },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.queryByRole("region", { name: "Across your matches" })).not.toBeInTheDocument();

    const input = document.querySelector("label.upload-button input[type=file]") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["demo"], "match.dem")] } });
    await rtlAct(async () => FakeXHR.last().respond(200, uploadDone));  // already stored: finished at once
    await advance();
    expect(api.count("GET /matches/summary")).toBe(2);
    const tiles = within(panel()).getByLabelText("Previous matches summary");
    expect(tiles).toHaveTextContent("MATCHES110 rounds · 9 scored");
    expect(tiles).toHaveTextContent("MODEL HIT RATE89%8 of 9 rounds");
    expect(tiles).toHaveTextContent("CT / T ROUNDS20% / 80%CT side won 2 of 10");
  });
});
