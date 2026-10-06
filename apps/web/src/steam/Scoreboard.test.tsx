import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import reportPersonal from "../test/fixtures/report_personal.json";
import { Scoreboard } from "./Scoreboard";
import type { MatchPlayer, MatchReport } from "./types";

const line = (steam_id: string, team: string, kills: number, history?: MatchPlayer["history"]): MatchPlayer => ({
  steam_id, team, rounds: 24, first_side: "t", kills, deaths: 15, kd: kills / 15, kills_per_round: kills / 24,
  opening_kills: 2, opening_deaths: 1, survived: 6, ...(history ? { history } : {}),
});
const report = (players: MatchPlayer[] | undefined) => ({ ...(reportPersonal as MatchReport), players });

describe("players scoreboard", () => {
  it("older API / not recorded: nothing", () => {
    const { container, rerender } = render(<Scoreboard report={report(undefined)} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<Scoreboard report={report([])} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("splits your team and opponents and shows who you've played with before", () => {
    render(<Scoreboard report={report([
      line("76561198157151718", "you", 29),
      line("76561198000000101", "teammate", 20, { matches_with: 3, matches_against: 0 }),
      line("76561198000000202", "opponent", 18, { matches_with: 1, matches_against: 1 }),
      line("76561198000000303", "opponent", 10, { matches_with: 0, matches_against: 0 }),
    ])} />);
    const mine = screen.getByRole("table", { name: "Your team" });
    expect(within(mine).getAllByRole("row")).toHaveLength(3);
    expect(within(mine).getByRole("link", { name: "You ↗" })).toHaveAttribute("href", "https://steamcommunity.com/profiles/76561198157151718");
    expect(within(mine).getByText("with 3")).toBeInTheDocument();
    const them = screen.getByRole("table", { name: "Opponents" });
    expect(within(them).getByText("with 1 · vs 1")).toBeInTheDocument();
    expect(within(them).getByText("First time")).toBeInTheDocument();
    expect(screen.getByText(/^Seen before: …00101 \(with 3\), …00202 \(with 1 · vs 1\)\.$/)).toBeInTheDocument();
  });

  it("demo you're not in: grouped by starting side, no history column", () => {
    render(<Scoreboard report={report([line("76561198000000101", "ct_start", 20), line("76561198000000202", "t_start", 18)])} />);
    expect(screen.getByRole("table", { name: "STARTED CT" })).toBeInTheDocument();
    expect(screen.queryByText("Played before")).toBeNull();
  });
});
