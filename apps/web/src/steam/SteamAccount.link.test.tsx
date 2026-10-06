import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, FakeXHR, installFakeApi } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";
import uploadQueued from "../test/fixtures/upload_queued.json";
import { Matches } from "./Matches";
import { AccountSettings } from "./SteamAccount";
import type { Me } from "./types";

const SHARE = "CSGO-9Dyih-7YcBA-VXraV-tRjNt-A3VTB";
const LINK = `steam://rungame/730/76561202255233023/+csgo_download_match%20${SHARE}`;
const unlinked = meFixture as Me;
const LINKED_ACCESS = { linked: true, auth_code_hint: "****-*****-FG56", linked_at: "2026-10-05T07:31:00Z", updated_at: "2026-10-05T07:31:00Z", needs_relink: null };
const linked: Me = { ...unlinked, match_access: LINKED_ACCESS };
const relinkShare: Me = { ...unlinked, match_access: { ...LINKED_ACCESS, needs_relink: { reason: "invalid_known_code", field: "share_code" } },
  sync: { ...unlinked.sync, status: "error", last_error: "invalid_known_code", last_finished_at: "2026-10-05T08:00:00Z" } };
const awaiting: Me = { ...unlinked, match_access: { ...LINKED_ACCESS, awaiting_share_code: true } };
const relinkAuth: Me = { ...relinkShare, match_access: { ...LINKED_ACCESS, needs_relink: { reason: "invalid_auth_code", field: "auth_code" } } };

const authBox = () => screen.getByLabelText(/^Auth code/) as HTMLInputElement;
const shareBox = () => screen.getByLabelText(/^Share code/) as HTMLInputElement;
const consent = () => screen.getByRole("checkbox");

function renderAccount(me: Me) {
  const onChange = vi.fn(async () => undefined);
  render(<AccountSettings me={me} onChange={onChange} onSignedOut={() => undefined} />);
  return onChange;
}

describe("link match history (Leetify-style onboarding)", () => {
  it("first link: two short hints, short labels and consent, no kicker (no wall of instructions)", () => {
    installFakeApi({});
    renderAccount(unlinked);
    const form = screen.getByRole("form", { name: "Link match history" });
    expect(within(form).getByRole("link", { name: /Valve’s match-history page/ })).toHaveAttribute(
      "href", "https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128");
    expect(form).toHaveTextContent("Codes from Valve’s match-history page ↗.");
    expect(form).toHaveTextContent("Imports that match and newer. Expire ~30 days. Older/FACEIT: upload a demo.");
    expect(shareBox()).toHaveAccessibleDescription("Optional · latest match token there, or CS2 Watch → Your Matches.");
    expect(form).not.toHaveTextContent("most recent completed match token");
    expect(form.querySelector(".section-kicker")).toBeNull();
    expect(form).not.toHaveTextContent(/OPTIONAL/);
    expect(within(form).getByRole("checkbox", { name: "Store encrypted; auto-sync on by default. Disconnect anytime." })).not.toBeChecked();
    expect(screen.getByRole("button", { name: /^Link/ })).toBeDisabled();
    expect(form).not.toHaveTextContent("Tick the box");
  });

  it("accepts codes as pasted (lowercase auth code without dashes, CS2 steam:// share link) and sends them cleaned up", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({ "PUT /steam/match-access": { status: 200, body: LINKED_ACCESS } });
    const onChange = renderAccount(unlinked);
    await user.type(authBox(), "ab12cde34fg56");
    await user.click(shareBox());
    await user.paste(LINK);
    await user.tab();
    expect(screen.getAllByText("Looks right.")).toHaveLength(1);
    expect(screen.getByText(`Found ${SHARE} in the link.`)).toBeInTheDocument();
    await user.click(consent());
    await user.click(screen.getByRole("button", { name: /^Link/ }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const put = api.calls.find((c) => c.method === "PUT");
    expect(put?.body).toEqual({ auth_code: "AB12-CDE34-FG56", share_code: SHARE, consent: true });
    expect(put?.headers.get("X-Requested-With")).toBe("csa");
    expect(authBox().value).toBe("");
  });

  it("catches codes pasted into the wrong box before asking Valve", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({});
    renderAccount(unlinked);
    await user.click(authBox());
    await user.paste(SHARE);
    await user.click(shareBox());
    await user.paste("AB12-CDE34-FG56");
    await user.tab();
    expect(authBox()).toHaveAttribute("aria-invalid", "true");
    expect(authBox()).toHaveAccessibleDescription(/That’s a match sharing code/);
    expect(shareBox()).toHaveAccessibleDescription(/That’s your Game Authentication Code/);
    await user.click(consent());
    await user.click(screen.getByRole("button", { name: /^Link/ }));
    expect(api.calls).toHaveLength(0);
    expect(authBox()).toHaveFocus();
  });

  it("shows Valve's rejection under the box it belongs to; other errors as an alert", async () => {
    const user = userEvent.setup();
    installFakeApi({ "PUT /steam/match-access": [
      { status: 422, body: { detail: "invalid_share_code" } },
      { status: 429, body: { detail: "valve_rate_limited" } },
    ] });
    renderAccount(unlinked);
    await user.type(authBox(), "AB12-CDE34-FG56");
    await user.type(shareBox(), SHARE);
    await user.click(consent());
    await user.click(screen.getByRole("button", { name: /^Link/ }));
    await waitFor(() => expect(shareBox()).toHaveAccessibleDescription(/last 30 days/));
    expect(shareBox()).toHaveFocus();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Link/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Valve is rate-limiting requests");
  });

  it("expired share code: says why, opens the form, keeps the auth code and sends only a new share code", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({ "PUT /steam/match-access": { status: 200, body: LINKED_ACCESS } });
    const onChange = renderAccount(relinkShare);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Re-link needed to keep syncing. Paste a share code from a match in the last 30 days.");
    expect(alert).toHaveFocus();  // announced first
    expect(shareBox()).toHaveAccessibleDescription(alert.textContent!);  // share-code text, not auth-code text
    expect(screen.queryByText("Paste a share code from a recent match.")).toBeNull();
    expect(screen.queryByText(/Optional · latest match token/)).toBeNull();
    await user.click(shareBox());
    expect(screen.queryByLabelText(/^Auth code/)).toBeNull();  // the stored auth code is kept
    expect(screen.queryByRole("checkbox")).toBeNull();  // consent was given at link
    await user.paste(SHARE);
    await user.click(screen.getByRole("button", { name: /Save share code/ }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({ auth_code: "", share_code: SHARE, consent: true });
  });

  it("revoked auth code: the auth code box is required again", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({});
    renderAccount(relinkAuth);
    expect(screen.getByRole("alert")).toHaveTextContent("Re-link needed to keep syncing. Paste your current Game Authentication Code.");
    expect(screen.getByRole("alert")).toHaveFocus();
    expect(authBox()).toHaveAccessibleDescription(/Paste your current Game Authentication Code/);
    expect(authBox()).toHaveAttribute("placeholder", "ABCD-EFGHI-JKLM");
    expect(screen.queryByLabelText(/^Share code/)).toBeNull();
    await user.click(consent());
    await user.click(screen.getByRole("button", { name: /^Save/ }));
    expect(api.calls).toHaveLength(0);
    expect(authBox()).toHaveAccessibleDescription(/Paste your Game Authentication Code/);
  });

  it("links with just the Game Authentication Code: no match sharing code needed to get started", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({ "PUT /steam/match-access": { status: 200, body: { ...LINKED_ACCESS, awaiting_share_code: true } } });
    const onChange = renderAccount(unlinked);
    await user.type(authBox(), "AB12-CDE34-FG56");
    await user.click(consent());
    await user.click(screen.getByRole("button", { name: /^Link/ }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({ auth_code: "AB12-CDE34-FG56", share_code: "", consent: true });
  });

  it("still needs the auth code to link", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({});
    renderAccount(unlinked);
    await user.type(shareBox(), SHARE);
    await user.click(consent());
    await user.click(screen.getByRole("button", { name: /^Link/ }));
    expect(api.calls).toHaveLength(0);
    expect(authBox()).toHaveAccessibleDescription(/Paste your Game Authentication Code/);
  });

  it("auth code saved, no share code yet: asks for one after the first match and sends only the share code", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({ "PUT /steam/match-access": { status: 200, body: LINKED_ACCESS } });
    const onChange = renderAccount(awaiting);
    expect(screen.getByText(/Auth saved/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Paste a share code from a recent match.")).toBeInTheDocument();
    expect(screen.queryByText("ADD A MATCH SHARING CODE")).toBeNull();
    expect(screen.queryByLabelText(/^Auth code/)).toBeNull();  // only the share code is asked for
    expect(shareBox()).toHaveFocus();  // sync is blocked on this box
    expect(shareBox()).toHaveAccessibleDescription("Paste a share code from a recent match.");
    await user.click(screen.getByRole("button", { name: /Save share code/ }));
    expect(api.calls).toHaveLength(0);  // nothing to save yet
    expect(shareBox()).toHaveAccessibleDescription(/Paste a match sharing code/);
    await user.type(shareBox(), SHARE);
    await user.click(screen.getByRole("button", { name: /Save share code/ }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({ auth_code: "", share_code: SHARE, consent: true });
  });

  it("linked and healthy: 'Update codes' replaces either code (empty keeps it); consent only for a new auth code", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({ "PUT /steam/match-access": { status: 200, body: LINKED_ACCESS } });
    const onChange = renderAccount(linked);
    const details = screen.getByText("Update codes").closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    const form = within(details).getByRole("form", { name: "Update codes" });
    expect(form.querySelector(".section-kicker")).toBeNull();
    expect(form).not.toHaveTextContent(/OPTIONAL|Replace codes if/);
    expect(authBox()).toHaveAttribute("placeholder", "Keep current");
    expect(shareBox()).toHaveAttribute("placeholder", "Keep current");
    expect(screen.queryByRole("checkbox")).toBeNull();
    await user.click(screen.getByRole("button", { name: /^Save/ }));
    expect(api.calls).toHaveLength(0);  // nothing to save
    await user.type(authBox(), "AB12-CDE34-FG56");
    expect(screen.getByRole("button", { name: /^Save/ })).toBeDisabled();
    await user.click(consent());
    await user.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({ auth_code: "AB12-CDE34-FG56", share_code: "", consent: true });
  });

  it("session & data: no Valve-revoke paragraph (it lives in the disconnect confirm)", () => {
    installFakeApi({});
    renderAccount(linked);
    expect(screen.queryByText(/To revoke access on Valve’s side/)).toBeNull();
  });

  it("linked: codes are tucked away under 'Update codes'; disconnect confirms and offers to link again", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const api = installFakeApi({ "DELETE /steam/match-access": { status: 204 } });
    const onChange = renderAccount(linked);
    expect(screen.getByText(/Update codes/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Disconnect match history" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Match history disconnected. Your imported matches stay.");
    expect(api.count("DELETE /steam/match-access")).toBe(1);
    expect(onChange).toHaveBeenCalled();
  });
});

describe("match list empty state and paused sync", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeXHR.install();
  });

  const routes = (me: Me) => ({
    "GET /matches?limit=50&offset=0": { status: 200, body: { matches: [], limit: 50, offset: 0 } },
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...me.sync, jobs: [] } },
    "GET /matches/summary": { status: 200, body: { totals: { matches: 0 } } },
  });

  it("offers both Sync and upload, and the empty-state upload works", async () => {
    installFakeApi(routes(unlinked));
    render(<Matches me={unlinked} onMeChange={async () => undefined} />);
    await advance();
    const empty = screen.getByLabelText("No matches yet");
    expect(empty).toHaveTextContent("SYNC FROM STEAM");
    expect(within(empty).getByRole("link", { name: "Account settings" })).toHaveAttribute("href", "/account");
    expect(empty).toHaveTextContent("UPLOAD A DEMO");
    expect(empty.querySelector(".empty-option p")).toBeNull();
    expect(empty).not.toHaveTextContent(/game\/csgo\/replays|Watch → Your Matches/);
    fireEvent.change(screen.getByLabelText("Choose a demo file to upload"), { target: { files: [new File(["demo"], "match.dem")] } });
    const xhr = FakeXHR.last();
    expect(xhr.url).toMatch(/\/matches\/upload$/);
    await advance();
    xhr.respond(202, uploadQueued);
    await advance();
    expect(screen.getByText("QUEUED…")).toBeInTheDocument();
  });

  it("pauses Sync while the codes need fixing and says which one", async () => {
    installFakeApi(routes(relinkShare));
    render(<Matches me={relinkShare} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByRole("button", { name: /SYNC MATCHES/ })).toBeDisabled();
    expect(document.querySelector(".sync-meta")).toBeNull();
    expect(screen.getByLabelText("No matches yet").querySelector(".empty-option p")).toBeNull();
  });

  it("auth code saved without a share code: Sync waits for one, uploads still work", async () => {
    installFakeApi(routes(awaiting));
    render(<Matches me={awaiting} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByRole("button", { name: /SYNC MATCHES/ })).toBeDisabled();
    expect(document.querySelector(".sync-meta")).toBeNull();
    expect(screen.getByLabelText("No matches yet").querySelector(".empty-option p")).toBeNull();
    expect(screen.queryByText(/Uploads work now|Your authentication code is saved/)).toBeNull();
    expect(screen.getByLabelText("Choose a demo file to upload")).toBeEnabled();
  });

  it("linked and fine: empty state points at the Sync button", async () => {
    installFakeApi(routes(linked));
    render(<Matches me={linked} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByRole("button", { name: /SYNC MATCHES/ })).toBeEnabled();
    const empty = screen.getByLabelText("No matches yet");
    expect(empty.querySelector(".empty-option p")).toBeNull();
    expect(empty).not.toHaveTextContent(/We import the match/);
    expect(document.querySelector(".sync-meta")).toBeNull();
  });
});

describe("main matches page once signed in", () => {
  it("sync blocked by a re-link: says so on the home page and links to the form in Account settings", async () => {
    const { SteamAccount } = await import("./SteamAccount");
    render(<SteamAccount me={relinkAuth} onChange={async () => undefined} onSignedOut={() => undefined} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Sync paused. Paste your current Game Authentication Code.");
    expect(within(alert).getByRole("link", { name: /Re-link in Account settings/ })).toHaveAttribute("href", "/account");
  });

  it("shows only who is signed in and a link to Account settings: no codes form, no account buttons", async () => {
    const { SteamAccount } = await import("./SteamAccount");
    for (const me of [unlinked, awaiting, relinkShare, linked]) {
      const { unmount } = render(<SteamAccount me={me} onChange={async () => undefined} onSignedOut={() => undefined} />);
      expect(screen.getByRole("link", { name: "Account settings" })).toHaveAttribute("href", "/account");
      expect(screen.queryByRole("form")).toBeNull();
      expect(screen.queryByRole("button", { name: /Delete my data|Sign out|Disconnect/ })).toBeNull();
      if (me !== relinkShare) expect(screen.queryByRole("alert")).toBeNull();
      unmount();
    }
  });
});
