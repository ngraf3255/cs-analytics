import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import reportPersonal from "../test/fixtures/report_personal.json";
import reportNotIn from "../test/fixtures/report_not_in_match.json";
import { CoachTips } from "./CoachTips";
import { coachTips } from "./coachTips";
import type { MatchReport, RoundReport } from "./types";

const personal = reportPersonal as MatchReport;

function withRounds(edit: (r: RoundReport, i: number) => Partial<NonNullable<RoundReport["you"]>>): MatchReport {
  return { ...personal, rounds: personal.rounds.map((r, i) => ({ ...r, you: { ...r.you!, ...edit(r, i) } })) };
}

describe("what to fix (rule-based, from this demo)", () => {
  it("not in the demo: no tips at all", () => {
    expect(coachTips(reportNotIn as MatchReport)).toEqual([]);
    const { container } = render(<CoachTips report={reportNotIn as MatchReport} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("real FACEIT demo: tips only cite rounds that match their rule", () => {
    for (const tip of coachTips(personal)) {
      const rounds = personal.rounds.filter((r) => tip.rounds.includes(r.round_number));
      expect(rounds).toHaveLength(tip.rounds.length);
      if (tip.id === "lost-advantage") for (const r of rounds) expect(r.you!.won === false && r.you!.win_probability! >= 0.65).toBe(true);
      if (tip.id === "duel-not-converted") for (const r of rounds) expect(r.you!.opening_kill && r.you!.won === false).toBe(true);
    }
  });

  it("frequent opening deaths: says how many and which rounds", () => {
    const report = withRounds((_r, i) => ({ opening_death: i < 5, opening_kill: false }));
    const tip = coachTips(report).find((t) => t.id === "opening-deaths");
    expect(tip?.rounds).toEqual([1, 2, 3, 4, 5]);
    expect(tip?.evidence).toMatch(/^First death of the round in 5 of 24 rounds/);
  });

  it("at most three tips, and a short demo gives none", () => {
    const busy = withRounds((_r, i) => ({ opening_death: i % 2 === 0, kills: 0, deaths: 1, won: false, win_probability: 0.7 }));
    expect(coachTips(busy).length).toBeLessThanOrEqual(3);
    expect(coachTips({ ...personal, rounds: personal.rounds.slice(0, 5) })).toEqual([]);
  });

  it("renders the tips with their evidence", () => {
    render(<CoachTips report={withRounds((_r, i) => ({ opening_death: i < 5, opening_kill: false }))} />);
    expect(screen.getByRole("region", { name: "What to fix" })).toHaveTextContent("You died first too often");
    expect(screen.getByText("Rounds 1, 2, 3, 4, 5")).toBeInTheDocument();
  });
});
