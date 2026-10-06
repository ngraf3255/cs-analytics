import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import { PeerCompare } from "./PeerCompare";
import type { PeerComparison, PeerGroup } from "./types";

const g = (extra: Partial<PeerGroup>): PeerGroup => ({ lines: 10, rounds: 220, kills_per_round: 0.7, kd: 1.0, survival_rate: 0.3,
  opening_attempt_rate: 0.2, opening_win_rate: 0.5, round_win_rate: 0.5, ...extra });
const body: PeerComparison = {
  basis: "lobby", min_peer_lines: 5,
  overall: { matches: 10, you: g({ kills_per_round: 0.85, kd: 1.2, round_win_rate: 0.55 }), peers: g({ lines: 90 }), percentiles: { kills_per_round: 78, kd: 64 } },
  maps: [
    { map_name: "de_mirage", matches: 6, you: g({ kills_per_round: 0.9 }), peers: g({ lines: 54 }), percentiles: { kills_per_round: 82, kd: 60 } },
    { map_name: "de_nuke", matches: 4, you: g({ kills_per_round: 0.6 }), peers: g({ lines: 36 }), percentiles: { kills_per_round: 30, kd: 40 } },
  ],
};

describe("compare to peers (your lobbies)", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("older API / guest (404): nothing", async () => {
    installFakeApi({ "GET /matches/peers": { status: 404, body: { detail: "no_steam_id" } } });
    const { container } = render(<PeerCompare refreshKey={1} />);
    await advance();
    expect(container).toBeEmptyDOMElement();
  });

  it("unexpected body (e.g. an older API answering with something else): nothing, no crash", async () => {
    installFakeApi({ "GET /matches/peers": { status: 200, body: { match: { id: "peers" } } } });
    const { container } = render(<PeerCompare refreshKey={1} />);
    await advance();
    expect(container).toBeEmptyDOMElement();
  });

  it("overall percentile and gaps, then per map", async () => {
    installFakeApi({ "GET /matches/peers": { status: 200, body } });
    render(<PeerCompare refreshKey={1} />);
    await advance();
    const section = screen.getByRole("region", { name: "Compare to your lobbies" });
    expect(section).toHaveTextContent("Top 22% for kills per round · Top 36% for K/D among 90 player-matches.");
    const kpr = within(screen.getByRole("table")).getByText("Kills / round").closest("tr")!;
    expect(kpr).toHaveTextContent("0.850.70+0.15");
    const won = within(screen.getByRole("table")).getByText("Rounds won").closest("tr")!;
    expect(won).toHaveTextContent("+5 pts");
    fireEvent.change(screen.getByLabelText("Map to compare"), { target: { value: "de_nuke" } });
    expect(section).toHaveTextContent("Bottom 30% for kills per round");
    expect(section).not.toHaveTextContent("ranks aren’t stored");
  });
});
