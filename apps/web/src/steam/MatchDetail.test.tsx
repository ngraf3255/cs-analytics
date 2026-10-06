import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import reportPersonal from "../test/fixtures/report_personal.json";
import reportFixture from "../test/fixtures/report.json";
import { OpeningDuels, RoundTimeline } from "./MatchDetail";
import type { MatchReport } from "./types";

const personal = reportPersonal as MatchReport;

describe("round timeline", () => {
  it("one cell per round with winner, your team's running score and the side swap", () => {
    render(<RoundTimeline rounds={personal.rounds} />);
    const cells = within(screen.getByRole("list", { name: "Round timeline" })).getAllByRole("listitem");
    expect(cells).toHaveLength(personal.rounds.length);
    expect(cells[0]).toHaveAccessibleName(/^Round 1, CT won, your team lost, score 0–1, first kill CT/);
    // FACEIT MR12: you started T and switched to CT for round 13
    expect(cells[12]).toHaveClass("side-swap");
    expect(cells.filter((c) => c.classList.contains("side-swap"))).toHaveLength(1);
    const last = cells[cells.length - 1].getAttribute("aria-label") ?? "";
    expect(last).toContain(`score ${personal.you!.status === "in_match" ? `${(personal.you as { won: number }).won}–` : ""}`);
  });

  it("without per-player rounds: no your-team marks or legend", () => {
    render(<RoundTimeline rounds={(reportFixture as MatchReport).rounds} />);
    expect(screen.queryByText(/your team won/)).toBeNull();
  });
});

describe("opening duels", () => {
  it("counts this match's first kills from the rounds and says clutch / economy aren't recorded", () => {
    render(<OpeningDuels report={personal} />);
    const tiles = screen.getByLabelText("Opening duels");
    const you = personal.you as { opening_kills: number; opening_deaths: number };
    expect(tiles).toHaveTextContent(`${you.opening_kills} W · ${you.opening_deaths} L`);
    expect(tiles).toHaveTextContent("FIRST KILL → ROUND");
    expect(screen.getByText(/Clutches and economy .* aren’t recorded/)).toBeInTheDocument();
  });
});
