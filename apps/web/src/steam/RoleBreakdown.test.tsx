import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import summaryPersonal from "../test/fixtures/summary_personal.json";
import { MatchesSummaryPanel } from "./MatchesSummary";
import type { MatchesAnalytics, SideRole } from "./types";

const base = summaryPersonal as MatchesAnalytics;
const side = (extra: Partial<SideRole>): SideRole => ({ rounds: 120, role: "balanced", kd: 1.1, kills_per_round: 0.7, survival_rate: 0.35,
  opening_kills: 12, opening_deaths: 10, opening_attempt_rate: 0.18, opening_win_rate: 0.55, ...extra });

describe("map & role breakdown", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("older API: no role tiles and no opening-duels column", async () => {
    installFakeApi({ "GET /matches/summary": { status: 200, body: base } });
    render(<MatchesSummaryPanel refreshKey={1} />);
    await advance();
    expect(screen.queryByLabelText("Your role by side")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Opening duels" })).toBeNull();
  });

  it("roles per side and opening duels per map", async () => {
    const you = base.you!;
    const body = { ...base, you: { ...you,
      maps: you.maps.map((m) => ({ ...m, opening_kills: 4, opening_deaths: 1, opening_attempt_rate: 0.2083, opening_win_rate: 0.8 })),
      roles: { baseline_opening_attempt_rate: 0.2, min_rounds: 20, ct: side({ role: "support", opening_attempt_rate: 0.09 }), t: side({ role: null, rounds: 12 }) } } };
    installFakeApi({ "GET /matches/summary": { status: 200, body } });
    render(<MatchesSummaryPanel refreshKey={1} />);
    await advance();
    const tiles = screen.getByLabelText("Your role by side");
    expect(tiles).toHaveTextContent("AS CTSupport");
    expect(tiles).toHaveTextContent("In 9% of rounds (avg 20%)");
    expect(tiles).toHaveTextContent("Needs 20+ rounds on T (12 so far).");
    expect(screen.getByRole("columnheader", { name: "Opening duels" })).toBeInTheDocument();
    expect(screen.getByText("4–1 · in 21% of rounds")).toBeInTheDocument();
  });
});
