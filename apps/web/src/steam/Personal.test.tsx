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
import { MatchesSummaryPanel } from "./MatchesSummary";
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

  it("home: your four tiles + list chips; maps / all-players live on /stats", async () => {
    installFakeApi(routes());
    const { unmount } = render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    const tiles = screen.getByLabelText("Your stats");
    expect(tiles).toHaveTextContent("WON54%");
    expect(tiles).toHaveTextContent("CT / T75% / 33%");
    expect(tiles).toHaveTextContent("K/D1.71");
    expect(tiles).toHaveTextContent("OPENINGS100%");
    expect(screen.queryByRole("region", { name: "Across your matches" })).not.toBeInTheDocument();
    expect(screen.queryByText("ALL PLAYERS")).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Your maps" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Won · mirage · score 13–11 · K-D 29–17/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Stats" })).toHaveAttribute("href", "/stats");
    unmount();

    installFakeApi({ "GET /matches/summary": { status: 200, body: summaryPersonal } });
    render(<MatchesSummaryPanel variant="stats" refreshKey={1} />);
    await advance();
    // Bundle 4: home 4-tiles are not re-shown on /stats
    expect(within(panel()).queryByLabelText("Your stats")).not.toBeInTheDocument();
    expect(rows("Your maps").map(cells)).toEqual([["mirage", "1 (1–0)", "24", "54%", "75% (9/12)", "33% (4/12)", "1.71"]]);
    expect(panel()).toHaveTextContent("ALL PLAYERS");
    const all = within(panel()).getByLabelText("Previous matches summary");
    expect(all).toHaveTextContent("MATCHES3");
    expect(all).toHaveTextContent("CT / T60% / 40%");
    expect(within(panel()).queryByRole("table", { name: "Your recent matches" })).not.toBeInTheDocument();
  });

  it("home: player in none of their demos — no your tiles; /stats still shows all-players", async () => {
    installFakeApi(routes({ "GET /matches/summary": { status: 200, body: summaryNotInDemos } }));
    const { unmount } = render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.queryByLabelText("Your stats")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Across your matches" })).not.toBeInTheDocument();
    unmount();

    installFakeApi({ "GET /matches/summary": { status: 200, body: summaryNotInDemos } });
    render(<MatchesSummaryPanel variant="stats" refreshKey={1} />);
    await advance();
    expect(within(panel()).queryByLabelText("Your stats")).not.toBeInTheDocument();
    expect(panel()).not.toHaveTextContent("aren’t in any of these demos");
    expect(within(panel()).getByLabelText("Previous matches summary")).toHaveTextContent("MATCHES2");
  });

  it("older API (no 'you'): home has no your tiles; /stats has all-players without YOU kicker", async () => {
    installFakeApi(routes({ "GET /matches/summary": { status: 200, body: summaryFixture } }));
    const { unmount } = render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.queryByLabelText("Your stats")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Across your matches" })).not.toBeInTheDocument();
    unmount();

    installFakeApi({ "GET /matches/summary": { status: 200, body: summaryFixture } });
    render(<MatchesSummaryPanel variant="stats" refreshKey={1} />);
    await advance();
    expect(within(panel()).queryByLabelText("Your stats")).not.toBeInTheDocument();
    expect(panel()).not.toHaveTextContent("ALL PLAYERS");
    expect(panel()).not.toHaveTextContent("your own team isn’t tracked yet");
    expect(within(panel()).getByLabelText("Previous matches summary")).toBeInTheDocument();
  });

  it("match list: date groups when the list spans days; no Added essays on rows", async () => {
    installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    // 3 matches on different calendar days → Today/Yesterday/Oct N labels (not per-row essays).
    expect(screen.getByText("Yesterday")).toBeInTheDocument();  // FACEIT imported Oct 5 vs today Oct 6
    expect(screen.getByText(day(SYNCED_MIRAGE.played_at!, { month: "short", day: "numeric" }))).toBeInTheDocument();
    expect(screen.queryByText(/Added /)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Won · mirage · score 13–11 · K-D 29–17/i })).toBeInTheDocument();
  });

  it("report: your result, side and every round you played highlighted (won / lost, kills, opening duels)", async () => {
    installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /Won · mirage · score 13–11 · K-D 29–17/i }));
    await advance();
    expect(screen.getByRole("button", { name: /Share match/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Match summary")).not.toBeInTheDocument();
    expect(screen.queryByText("Added (no match date in the demo)")).not.toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Rounds" });
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(
      ["Round", "You", "Opening kill", "Actual winner", "Model estimate"]);
    const body = within(table).getAllByRole("row").slice(1);
    expect(body).toHaveLength(24);  // the knife round before the match restart is not a round of the match
    expect(body.map((r) => cells(r)[1])).not.toContain("Not tracked");
    expect(cells(body[0])[0]).toBe("1");
    expect(cells(body[0])[1]).toBe("T LOST0 kills");
    expect(cells(body[0])[4]).toContain("your team 38%");
    expect(cells(body[13])[1]).toBe("CT WON2 kills · survived");
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
    fireEvent.click(screen.getByRole("button", { name: /mirage · 2–8 · 10r/i }));
    await advance();
    expect(screen.getByRole("button", { name: /Share match/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Match summary")).not.toBeInTheDocument();
    expect(screen.queryByText(/Your SteamID/)).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: /Won · mirage · score 13–11 · K-D 29–17/i }));
    await advance();
    expect(screen.getByRole("button", { name: /Share match/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Match summary")).not.toBeInTheDocument();
    expect(screen.queryByText(/^YOU$/)).not.toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Rounds" })).getAllByRole("columnheader")).toHaveLength(4);
  });
});
