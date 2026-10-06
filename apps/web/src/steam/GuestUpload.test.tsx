import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, FakeXHR, installFakeApi } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";
import { SteamSection } from "./SteamSection";

/** GET /me of a guest (POST /auth/guest) on a server without Steam. */
const GUEST = {
  ...meFixture, steam_id: null, account: "guest",
  match_access: { linked: false, auth_code_hint: null, linked_at: null, updated_at: null, needs_relink: null },
  sync: {
    ...meFixture.sync, last_synced_at: null,
    auto_sync: { enabled: true, active: false, paused_reason: "server_disabled", interval_seconds: null, next_at: null,
                 last_run_at: null, last_error: null, failures: 0 },
  },
};
const UPLOAD_ONLY = { enabled: false, steam: false, upload: true, guest: true };
const EMPTY = { status: 200, body: { matches: [], limit: 50, offset: 0 } };

function routes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /steam/status": { status: 200, body: UPLOAD_ONLY },
    "GET /me": [{ status: 401, body: { detail: "not_authenticated" } }, { status: 200, body: GUEST }],
    "POST /auth/guest": { status: 200, body: GUEST },
    "GET /matches?limit=50&offset=0": EMPTY,
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /matches/summary": { status: 404, body: { detail: "Not Found" } },
    ...extra,
  };
}

describe("upload without Steam (guest session)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeXHR.install();
  });

  it("Steam off: Connect Steam is soft-disabled and a guest can open their uploads (no sync UI, no /steam/sync)", async () => {
    const api = installFakeApi(routes());
    render(<SteamSection />);
    await advance();
    expect(screen.queryByRole("link", { name: /Sign in through Steam/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Sign in through Steam · soon/ })).toBeDisabled();
    expect(screen.getByText("STEAM SYNC · COMING SOON")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open my uploads" }));
    await advance();
    const guest = api.calls.find((c) => c.method === "POST" && c.path === "/auth/guest");
    expect(guest?.headers.get("X-Requested-With")).toBe("csa");
    expect(screen.getByText("Uploading as a guest")).toBeInTheDocument();
    expect(screen.getByText("YOUR UPLOADS")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /SYNC MATCHES/ })).toBeNull();
    expect(screen.getByText("SYNC FROM STEAM · COMING SOON")).toBeInTheDocument();
    expect(screen.getByText("UPLOAD .DEM")).toBeInTheDocument();
    expect(api.count("GET /steam/sync")).toBe(0);
  });

  it("picking a demo before signing in starts a guest session and uploads it right away", async () => {
    installFakeApi(routes());
    render(<SteamSection />);
    await advance();
    const input = screen.getByLabelText("Choose a demo file to upload without Steam") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["demo"], "match.dem")] } });
    await advance();
    const xhr = FakeXHR.last();
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toMatch(/\/matches\/upload$/);
    expect(xhr.withCredentials).toBe(true);
    expect(FakeXHR.instances).toHaveLength(1);  // exactly once
  });

  it("Steam on: offers both Steam sign-in and the guest upload", async () => {
    installFakeApi(routes({
      "GET /steam/status": { status: 200, body: { enabled: true, steam: true, upload: true, guest: true } },
      "GET /me": { status: 401, body: { detail: "not_authenticated" } },
    }));
    render(<SteamSection />);
    await advance();
    expect(screen.getByRole("link", { name: /Sign in through Steam/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open my uploads" })).toBeInTheDocument();
  });

  it("older API (only `enabled`): Steam sign-in as before, no guest card", async () => {
    installFakeApi(routes({
      "GET /steam/status": { status: 200, body: { enabled: true } },
      "GET /me": { status: 401, body: { detail: "not_authenticated" } },
    }));
    render(<SteamSection />);
    await advance();
    expect(screen.getByRole("link", { name: /Sign in through Steam/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open my uploads" })).toBeNull();
  });

  it("API unreachable: says so instead of rendering a broken section", async () => {
    installFakeApi({ "GET /steam/status": { status: 0 } });
    render(<SteamSection />);
    await advance();
    expect(screen.getByText("Can’t reach the match service")).toBeInTheDocument();
  });
});
