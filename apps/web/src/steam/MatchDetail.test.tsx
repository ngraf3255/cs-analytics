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
    expect(screen.getByText("TIMELINE")).toBeInTheDocument();
    expect(screen.getByLabelText("Timeline legend")).toBeInTheDocument();
    expect(screen.queryByText("ROUND TIMELINE")).toBeNull();
    // Legend lines live inside the ⓘ details, not as 5 open rows
    const info = screen.getByLabelText("Timeline legend").closest("details");
    expect(info).not.toBeNull();
    expect(info!.textContent).toMatch(/CT won/);
    expect(info!.textContent).toMatch(/Underline: your team won/);
  });

  it("without per-player rounds: no your-team marks or legend", () => {
    render(<RoundTimeline rounds={(reportFixture as MatchReport).rounds} />);
    expect(screen.getByLabelText("Timeline legend")).toBeInTheDocument();
    const info = screen.getByLabelText("Timeline legend").closest("details")!;
    expect(info.textContent).not.toMatch(/your team won/);
    expect(info.textContent).not.toMatch(/You got/);
  });
});

describe("opening duels", () => {
  it("counts this match's first kills from the rounds (no clutch/economy footnote)", () => {
    render(<OpeningDuels report={personal} />);
    const tiles = screen.getByLabelText("Opening duels");
    const you = personal.you as { opening_kills: number; opening_deaths: number };
    expect(tiles).toHaveTextContent(`${you.opening_kills} W · ${you.opening_deaths} L`);
    expect(tiles).toHaveTextContent("FIRST KILL → ROUND");
    expect(screen.queryByText(/Clutches and economy/)).toBeNull();
  });
});
