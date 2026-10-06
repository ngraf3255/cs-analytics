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

  it("signed out: offers Steam sign-in that comes back to /account", async () => {
    installFakeApi({
      "GET /steam/status": { status: 200, body: { steam: true, upload: true } },
      "GET /me": { status: 401, body: { detail: "not_signed_in" } },
    });
    render(<AccountPage />);
    const link = await screen.findByRole("link", { name: /Sign in through Steam/ });
    expect(decodeURIComponent(link.getAttribute("href") ?? "")).toMatch(/next=\/account$/);
  });

  it("signed in: holds the codes form, disconnect, sign out and delete", async () => {
    installFakeApi({
      "GET /steam/status": { status: 200, body: { steam: true, upload: true } },
      "GET /me": { status: 200, body: LINKED },
      "POST /auth/logout": { status: 204 },
    });
    render(<AccountPage />);
    expect(await screen.findByRole("heading", { name: "Account settings" })).toBeInTheDocument();
    expect(screen.getByText("ADD A MATCH SHARING CODE")).toBeInTheDocument();
    expect(screen.getByLabelText(/MATCH SHARING CODE/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect match history" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete my data" })).toBeInTheDocument();
    const assign = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign } as Location);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/#matches"));
  });
});
