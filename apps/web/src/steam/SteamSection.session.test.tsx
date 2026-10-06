import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import matchesFixture from "../test/fixtures/matches.json";
import meFixture from "../test/fixtures/me.json";
import { SteamSection } from "./SteamSection";

const OTHER = { ...meFixture, steam_id: "76561198000000002" };
const OTHER_MATCHES = { matches: [{ ...matchesFixture.matches[0], id: "other", map_name: "de_nuke" }], limit: 50, offset: 0 };

function routes(me: unknown[], matches: unknown[] = [{ status: 200, body: matchesFixture }]) {
  return {
    "GET /steam/status": { status: 200, body: { enabled: true } },
    "GET /me": me as never,
    "GET /matches?limit=50&offset=0": matches as never,
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...meFixture.sync, jobs: [] } },
  };
}

describe("session changes and #matches", () => {
  let scrolled: string[];
  beforeEach(() => {
    vi.useFakeTimers();
    scrolled = [];
    Element.prototype.scrollIntoView = vi.fn(function (this: Element) { scrolled.push(this.id); });
  });
  afterEach(() => { window.location.hash = ""; });

  it("scrolls to #matches once the section has rendered (the browser's own jump came too early)", async () => {
    window.location.hash = "#matches";
    installFakeApi(routes([{ status: 200, body: meFixture }]));
    render(<SteamSection />);
    await advance();
    expect(document.getElementById("matches")).not.toBeNull();
    expect(scrolled).toContain("matches");
  });

  it("re-checks the session on hashchange and shows the newly signed-in user's matches", async () => {
    const api = installFakeApi(routes(
      [{ status: 200, body: meFixture }, { status: 200, body: OTHER }],
      [{ status: 200, body: matchesFixture }, { status: 200, body: OTHER_MATCHES }],
    ));
    render(<SteamSection />);
    await advance();
    expect(screen.getByRole("button", { name: /mirage/i })).toBeInTheDocument();
    scrolled = [];

    await act(async () => {
      window.location.hash = "#matches";  // e.g. /dev/login redirected to /#matches (jsdom fires hashchange)
    });
    await advance();
    await advance();
    expect(api.count("GET /me")).toBe(2);
    // SteamID card is off home (header avatar → /account); the match list shows the new user.
    expect(screen.queryByText(OTHER.steam_id, { exact: false })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /nuke/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /mirage/i })).not.toBeInTheDocument();
    expect(scrolled).toContain("matches");
  });

  it("re-checks the session when the tab regains focus (signed out elsewhere -> sign-in offered)", async () => {
    const api = installFakeApi(routes([{ status: 200, body: meFixture }, { status: 401, body: { detail: "not_authenticated" } }]));
    render(<SteamSection />);
    await advance();
    expect(screen.getByRole("button", { name: /mirage/i })).toBeInTheDocument();
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));  // usually fires with focus: one request
    });
    await advance();
    expect(api.count("GET /me")).toBe(2);
    expect(screen.getByRole("link", { name: /Sign in through Steam/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /mirage/i })).not.toBeInTheDocument();
  });
});
