import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installFakeApi } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";
import { AccountPage } from "./AccountPage";
import { isAccountPath } from "./routes";
import type { Me } from "./types";

const LINKED: Me = { ...(meFixture as Me), match_access: { linked: true, auth_code_hint: "****-*****-FG56", linked_at: "2026-10-05T07:31:00Z", updated_at: "2026-10-05T07:31:00Z", needs_relink: null, awaiting_share_code: true } };

describe("/account", () => {
  afterEach(() => vi.restoreAllMocks());

  it("matches /account with or without a trailing slash only", () => {
    expect(isAccountPath("/account")).toBe(true);
    expect(isAccountPath("/account/")).toBe(true);
    expect(isAccountPath("/")).toBe(false);
    expect(isAccountPath("/accounts")).toBe(false);
  });

  it("while status / session load: shows the page heading and a placeholder, not a blank page", () => {
    installFakeApi({
      "GET /steam/status": () => new Promise(() => undefined),
      "GET /me": () => new Promise(() => undefined),
    });
    render(<AccountPage />);
    expect(screen.getByRole("heading", { name: "Account settings" })).toBeInTheDocument();
    expect(screen.getByLabelText("Loading your account")).toBeInTheDocument();
  });

  it("signed in: profile header, section nav and one section each for history, session and data", async () => {
    installFakeApi({
      "GET /steam/status": { status: 200, body: { steam: true, upload: true } },
      "GET /me": { status: 200, body: LINKED },
      "GET /steam/sync": { status: 200, body: { ...LINKED.sync, jobs: [] } },
    });
    render(<AccountPage />);
    const header = await screen.findByLabelText("Profile");
    expect(header).toHaveTextContent("MEMBER SINCE");
    expect(header).toHaveTextContent("Waiting for a share code");
    const nav = screen.getByRole("navigation", { name: "Account sections" });
    expect([...nav.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual(["#match-history", "#session", "#your-data"]);
    for (const name of ["Steam match history", "Signed in on this browser", "Delete account"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
  });

  it("signed out: offers Steam sign-in that comes back to /account", async () => {
    installFakeApi({
      "GET /steam/status": { status: 200, body: { steam: true, upload: true } },
      "GET /me": { status: 401, body: { detail: "not_signed_in" } },
    });
    render(<AccountPage />);
    const link = await screen.findByRole("link", { name: /Sign in through Steam/ });
    expect(decodeURIComponent(link.getAttribute("href") ?? "")).toMatch(/next=\/account$/);
  });

  it("failed Steam sign-in that started here comes back here and says so", async () => {
    window.history.replaceState(null, "", "/account?steam_login=failed&reason=bad_signature");
    installFakeApi({
      "GET /steam/status": { status: 200, body: { steam: true, upload: true } },
      "GET /me": { status: 401, body: { detail: "not_signed_in" } },
    });
    render(<AccountPage />);
    expect(await screen.findByText("Steam sign-in could not be verified. Please try again.")).toBeInTheDocument();
    expect(window.location.pathname + window.location.search).toBe("/account");
    window.history.replaceState(null, "", "/");
  });

  it("signed in: holds the codes form, disconnect, sign out and delete", async () => {
    installFakeApi({
      "GET /steam/status": { status: 200, body: { steam: true, upload: true } },
      "GET /me": { status: 200, body: LINKED },
      "POST /auth/logout": { status: 204 },
    });
    render(<AccountPage />);
    // wait for the signed-in panel itself (the heading also renders while signed out / loading)
    expect(await screen.findByText("Paste a share code from a recent match.")).toBeInTheDocument();
    expect(screen.getByText(/Auth saved/)).toBeInTheDocument();
    await vi.waitFor(() => expect(screen.getByLabelText(/^Share code/)).toHaveFocus());  // sync is blocked: land on the form
    expect(screen.getByRole("button", { name: "Disconnect match history" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete my data" })).toBeInTheDocument();
    const assign = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign } as Location);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/#matches"));
  });
});
