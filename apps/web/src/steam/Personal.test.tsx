import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import matchesPersonal from "../test/fixtures/matches_personal.json";
import meFixture from "../test/fixtures/me_personal.json";
import reportNotInMatch from "../test/fixtures/report_not_in_match.json";
import reportPersonal from "../test/fixtures/report_personal.json";
import summaryFixture from "../test/fixtures/summary.json";
import summaryNotInDemos from "../test/fixtures/summary_not_in_demos.json";
import summaryPersonal from "../test/fixtures/summary_personal.json";
import { Matches } from "./Matches";
import type { Me } from "./types";

// Fixtures: real responses of the local API (SQLite, 2026-10-05) for SteamID 76561198157151718, a
// player IN the FACEIT de_mirage demo (uploaded; 29 K / 17 D, started T, won 13-11), who also has two
// fake-Valve Steam syncs they are not in (MM de_ancient 8r, demoparser2 de_mirage 10r; the fake Game
// Coordinator sends match times, so those show when they were played). summary_not_in_demos.json:
// user ...001, in none of their demos (synced MM ancient + uploaded HLTV nuke).
const me = meFixture as Me;
const [FACEIT, SYNCED_MIRAGE] = matchesPersonal.matches;
const day = (iso: string, opts: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric" }) =>
  new Date(iso).toLocaleDateString(undefined, opts);

function routes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /matches?limit=50&offset=0": { status: 200, body: matchesPersonal },
    "GET /matches/summary": { status: 200, body: summaryPersonal },
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...me.sync, jobs: [] } },
    [`GET /matches/${FACEIT.id}`]: { status: 200, body: reportPersonal },
    [`GET /matches/${SYNCED_MIRAGE.id}`]: { status: 200, body: reportNotInMatch },
    ...extra,
  };
}

const panel = () => screen.getByRole("region", { name: "Across your matches" });
const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);
const rows = (name: string) => within(within(panel()).getByRole("table", { name })).getAllByRole("row").slice(1);

describe("personal analytics: the signed-in player's own side each round", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("summary: your round win rate as CT / T, K/D, opening duels, maps and recent matches, above the all-player numbers", async () => {
    installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    const tiles = within(panel()).getByLabelText("Your stats");
    expect(tiles).toHaveTextContent("ROUNDS WON54%13 of 24 · 1 match: 1 W · 0 L");
    expect(tiles).toHaveTextContent("AS CT / AS T75% / 33%CT 9/12 · T 4/12 rounds won");
    expect(tiles).toHaveTextContent("K/D1.7129 K / 17 D · 1.21 per round · survived 29%");
    expect(tiles).toHaveTextContent("OPENING DUELS100%Won 4 of 4 · round won 75% after your opening kill, — after dying first");
    expect(panel()).toHaveTextContent("Your last 1 match: 1–0, 54% of rounds won, K/D 1.71.");
    expect(rows("Your maps").map(cells)).toEqual([["mirage", "1 (1–0)", "24", "54%", "75% (9/12)", "33% (4/12)", "1.71"]]);
    expect(rows("Your recent matches").map(cells)).toEqual([
      [`Added ${day(FACEIT.imported_at, { month: "short", day: "numeric" })}`, "mirage", "Won 13–11", "T", "13 of 24", "29 / 17"],
    ]);
    expect(panel()).toHaveTextContent("Not in your stats: 2 demos you’re not in (e.g. pro matches).");
    // The all-player numbers stay, labelled, below the personal ones.
    expect(panel()).toHaveTextContent("ALL PLAYERS IN THESE DEMOS · MAP SIDES");
    const all = within(panel()).getByLabelText("Previous matches summary");
    expect(all).toHaveTextContent("MATCHES342 rounds · 40 scored");  // FACEIT: 24 rounds, the knife round left out
    expect(all).toHaveTextContent("CT / T ROUNDS60% / 40%CT side won 25 of 42");
    const you = within(panel()).getByLabelText("Your stats");
    expect(you.compareDocumentPosition(all) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(panel()).toHaveTextContent("“You” uses the side your SteamID played each round (halftime swap included).");
    expect(panel()).not.toHaveTextContent("your own team isn’t tracked yet");
  });

  it("summary: a player in none of their demos gets a note, and the all-player numbers", async () => {
    installFakeApi(routes({ "GET /matches/summary": { status: 200, body: summaryNotInDemos } }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(within(panel()).queryByLabelText("Your stats")).not.toBeInTheDocument();
    expect(panel()).toHaveTextContent(
      "You (SteamID 76561198000000001) aren’t in any of these demos yet, so there are no personal stats. Left out: 2 demos you’re not in (e.g. pro matches). The numbers below cover all players.");
    expect(within(panel()).getByLabelText("Previous matches summary")).toHaveTextContent("MATCHES226 rounds");
  });

  it("summary from an older API (no 'you'): no personal section, the old note", async () => {
    installFakeApi(routes({ "GET /matches/summary": { status: 200, body: summaryFixture } }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(within(panel()).queryByLabelText("Your stats")).not.toBeInTheDocument();
    expect(panel()).not.toHaveTextContent("ALL PLAYERS IN THESE DEMOS");
    expect(panel()).toHaveTextContent("your own team isn’t tracked yet");
  });

  it("match list: played date when Valve gave one, else 'Added' + the import date", async () => {
    installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent(`Added ${day(FACEIT.imported_at)}`);
    expect(items[1]).toHaveTextContent(day(SYNCED_MIRAGE.played_at!));
    expect(items[1]).not.toHaveTextContent("Added");
    expect(within(items[1]).getByTitle("Played (from Valve)")).toBeInTheDocument();
  });

  it("report: your result, side and every round you played highlighted (won / lost, kills, opening duels)", async () => {
    installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /24 rounds/ }));
    await advance();
    const header = screen.getByLabelText("Match summary");
    expect(header).toHaveTextContent("YOUWon 13–11Started T, then CT · 29 K / 17 D (K/D 1.71) · won 13 of 24 rounds");
    expect(header).toHaveTextContent(`DATE${day(FACEIT.imported_at)}Added (no match date in the demo)`);
    const table = screen.getByRole("table", { name: "Rounds" });
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(
      ["Round", "You", "Opening kill", "Actual winner", "Model estimate"]);
    const body = within(table).getAllByRole("row").slice(1);
    expect(body).toHaveLength(24);  // the knife round before the match restart is not a round of the match
    expect(body.map((r) => cells(r)[1])).not.toContain("Not tracked");
    expect(cells(body[0])[0]).toBe("1");
    expect(cells(body[0])[1]).toBe("TLOST0 kills");
    expect(cells(body[0])[4]).toContain("your team 38%");
    expect(cells(body[13])[1]).toBe("CTWON2 kills · survived");
    expect(body.filter((r) => r.classList.contains("your-win"))).toHaveLength(13);
    expect(body.filter((r) => r.classList.contains("your-loss"))).toHaveLength(11);
    expect(within(table).getAllByText("YOUR KILL")).toHaveLength(4);
    expect(within(table).queryByText("YOU DIED FIRST")).not.toBeInTheDocument();
    expect(cells(body[6])[2]).toBe("T · AK-47 · 9.0sYOUR KILL");
  });

  it("report: a match you're not in says so and has no 'You' column; the date is when it was played", async () => {
    installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /10 rounds/ }));
    await advance();
    const header = screen.getByLabelText("Match summary");
    expect(header).toHaveTextContent("YOUNot in this demoYour SteamID isn’t in this match: the stats cover all players.");
    expect(header).toHaveTextContent(`DATE${day(SYNCED_MIRAGE.played_at!)}Played (from Valve)`);
    const table = screen.getByRole("table", { name: "Rounds" });
    expect(within(table).queryByRole("columnheader", { name: "You" })).not.toBeInTheDocument();
    expect(within(table).getAllByRole("row")).toHaveLength(11);
  });

  it("report from an older API (no 'you', no date fields): imported date, no You tile or column", async () => {
    const { you: _you, ...old } = reportPersonal;
    const oldMatch = { ...old.match, date: undefined, date_source: undefined, played_at: undefined };
    installFakeApi(routes({
      [`GET /matches/${FACEIT.id}`]: { status: 200, body: { ...old, match: oldMatch, rounds: old.rounds.map(({ you: _r, ...r }) => r) } },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /24 rounds/ }));
    await advance();
    const header = screen.getByLabelText("Match summary");
    expect(header).not.toHaveTextContent("YOU");
    expect(header).toHaveTextContent(`DATE${day(FACEIT.imported_at)}Added`);
    expect(within(screen.getByRole("table", { name: "Rounds" })).getAllByRole("columnheader")).toHaveLength(4);
  });
});
