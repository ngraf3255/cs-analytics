import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import summaryPersonal from "../test/fixtures/summary_personal.json";
import { FormTrend, rolling } from "./FormTrend";
import type { MatchesAnalytics } from "./types";

const base = summaryPersonal as MatchesAnalytics;
const one = base.you!.recent_form.matches[0];
const series = [
  { ...one, id: "m3", kills: 30, deaths: 15, won: 13, rounds: 24, win_rate: 0.54, result: "won" as const },
  { ...one, id: "m2", kills: 10, deaths: 20, won: 6, rounds: 19, win_rate: 0.32, result: "lost" as const },
  { ...one, id: "m1", kills: 20, deaths: 20, won: 12, rounds: 24, win_rate: 0.5, result: "tied" as const },
];  // newest first, as the API sends them

describe("form over time", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("rolling average skips gaps", () => {
    expect(rolling([1, null, 3], 2)).toEqual([1, 1, 3]);
    expect(rolling([null], 5)).toEqual([null]);
  });

  it("fewer than 3 matches: hidden, no request", () => {
    const api = installFakeApi({});
    const { container } = render(<FormTrend refreshKey={1} matchCount={2} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/after 3 matches/)).toBeNull();
    expect(api.calls).toHaveLength(0);
  });

  it("asks for the last 50 matches and charts them oldest → newest", async () => {
    const body = { ...base, you: { ...base.you!, recent_form: { ...base.you!.recent_form, matches: series } } };
    const api = installFakeApi({ "GET /matches/summary?recent=50": { status: 200, body } });
    render(<FormTrend refreshKey={1} matchCount={3} />);
    await advance();
    expect(api.count("GET /matches/summary?recent=50")).toBe(1);
    const fig = screen.getByRole("figure", { name: "Your form over time" });
    expect(fig).toHaveTextContent("LAST 3 MATCHES");
    expect(within(fig).getByRole("img", { name: /^Rounds won per match, oldest to newest: 50%, 32%, 54%$/ })).toBeInTheDocument();
    expect(within(fig).getByRole("img", { name: /^K\/D per match, oldest to newest: 1\.00, 0\.50, 2\.00$/ })).toBeInTheDocument();
  });
});
