import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";
import matchesFixture from "../test/fixtures/matches.json";
import { Matches } from "./Matches";
import type { Me } from "./types";

const base = meFixture as Me;
const unlinked: Me = { ...base, match_access: { ...base.match_access, linked: false, needs_relink: null, awaiting_share_code: false } };
const linked: Me = { ...base, match_access: { ...base.match_access, linked: true, needs_relink: null, awaiting_share_code: false } };

function routes(me: Me, matches: unknown = { matches: [], limit: 50, offset: 0 }) {
  return {
    "GET /matches?limit=50&offset=0": { status: 200, body: matches },
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...me.sync, jobs: [] } },
    "GET /matches/summary": { status: 404, body: { detail: { error: "match_not_found" } } },
  };
}

describe("first-run onboarding (signed in)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
  });

  it("no matches, not linked: checklist marks sign-in done and linking as next", async () => {
    installFakeApi(routes(unlinked));
    render(<Matches me={unlinked} onMeChange={async () => undefined} />);
    await advance();
    const steps = within(screen.getByRole("list", { name: "Get started" })).getAllByRole("listitem");
    expect(steps).toHaveLength(4);
    expect(steps[0]).toHaveTextContent("Signed in with Steam (done)");
    expect(steps[0].querySelector("p")).toBeNull();
    expect(steps[1]).toHaveTextContent("Link your match history · optional (next)");
    expect(steps[1].querySelector("p")).toBeNull();
    expect(steps[2]).toHaveTextContent("Import your first match");
    expect(steps[2].querySelector("p")).toBeNull();
  });

  it("no matches, linked: the next step is the first import", async () => {
    installFakeApi(routes(linked));
    render(<Matches me={linked} onMeChange={async () => undefined} />);
    await advance();
    const steps = within(screen.getByRole("list", { name: "Get started" })).getAllByRole("listitem");
    expect(steps[1]).toHaveTextContent("(done)");
    expect(steps[1].querySelector("p")).toBeNull();
    expect(steps[2]).toHaveTextContent("Import your first match (next)");
    expect(steps[2].querySelector("p")).toBeNull();
  });

  it("no checklist without Steam sync (uploads-only accounts keep the plain empty state)", async () => {
    installFakeApi(routes(unlinked));
    render(<Matches me={unlinked} onMeChange={async () => undefined} canSync={false} />);
    await advance();
    expect(screen.getByLabelText("No matches yet")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Get started" })).toBeNull();
  });

  it("matches but no linked history: a dismissible tip that stays dismissed", async () => {
    installFakeApi(routes(unlinked, matchesFixture));
    const { unmount } = render(<Matches me={unlinked} onMeChange={async () => undefined} />);
    await advance();
    const tip = screen.getByRole("complementary", { name: "Tip" });
    expect(tip).toHaveTextContent("Link match history");
    expect(within(tip).getByRole("link", { name: "Account settings" })).toHaveAttribute("href", "/account");
    fireEvent.click(within(tip).getByRole("button", { name: "Dismiss tip" }));
    expect(screen.queryByRole("complementary", { name: "Tip" })).toBeNull();
    unmount();
    render(<Matches me={unlinked} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.queryByRole("complementary", { name: "Tip" })).toBeNull();
  });

  it("linked with matches: no tip", async () => {
    installFakeApi(routes(linked, matchesFixture));
    render(<Matches me={linked} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.queryByRole("complementary", { name: "Tip" })).toBeNull();
  });
});
