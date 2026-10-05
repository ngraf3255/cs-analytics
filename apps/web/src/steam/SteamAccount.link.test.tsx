import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, FakeXHR, installFakeApi } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";
import uploadQueued from "../test/fixtures/upload_queued.json";
import { Matches } from "./Matches";
import { SteamAccount } from "./SteamAccount";
import type { Me } from "./types";

const SHARE = "CSGO-9Dyih-7YcBA-VXraV-tRjNt-A3VTB";
const LINK = `steam://rungame/730/76561202255233023/+csgo_download_match%20${SHARE}`;
const unlinked = meFixture as Me;
const LINKED_ACCESS = { linked: true, auth_code_hint: "****-*****-FG56", linked_at: "2026-10-05T07:31:00Z", updated_at: "2026-10-05T07:31:00Z", needs_relink: null };
const linked: Me = { ...unlinked, match_access: LINKED_ACCESS };
const relinkShare: Me = { ...unlinked, match_access: { ...LINKED_ACCESS, needs_relink: { reason: "invalid_known_code", field: "share_code" } },
  sync: { ...unlinked.sync, status: "error", last_error: "invalid_known_code", last_finished_at: "2026-10-05T08:00:00Z" } };
const relinkAuth: Me = { ...relinkShare, match_access: { ...LINKED_ACCESS, needs_relink: { reason: "invalid_auth_code", field: "auth_code" } } };

const authBox = () => screen.getByLabelText(/GAME AUTHENTICATION CODE/) as HTMLInputElement;
const shareBox = () => screen.getByLabelText(/MATCH SHARING CODE/) as HTMLInputElement;
const consent = () => screen.getByRole("checkbox");

function renderAccount(me: Me) {
  const onChange = vi.fn(async () => undefined);
  render(<SteamAccount me={me} onChange={onChange} onSignedOut={() => undefined} />);
  return onChange;
}

describe("link match history (Leetify-style onboarding)", () => {
  it("links Valve's exact pages and walks through both codes", () => {
    installFakeApi({});
    renderAccount(unlinked);
    const form = screen.getByRole("form", { name: "Link match history" });
    expect(within(form).getByRole("link", { name: /Access to Your Match History/ })).toHaveAttribute(
      "href", "https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128");
    expect(within(form).getByRole("link", { name: /Valve’s docs/ })).toHaveAttribute(
      "href", "https://developer.valvesoftware.com/wiki/Counter-Strike:_Global_Offensive_Access_Match_History");
    expect(form).toHaveTextContent("Your most recently completed match token");
    expect(form).toHaveTextContent("upload the demo file below");
    expect(form).toHaveTextContent("We import that match and every newer one");
    expect(screen.getByRole("button", { name: /LINK MATCH HISTORY/ })).toBeDisabled();
    expect(screen.getByText("Tick the box above to enable linking.")).toBeInTheDocument();
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
    await user.click(screen.getByRole("button", { name: /LINK MATCH HISTORY/ }));
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
    await user.click(screen.getByRole("button", { name: /LINK MATCH HISTORY/ }));
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
    await user.click(screen.getByRole("button", { name: /LINK MATCH HISTORY/ }));
    await waitFor(() => expect(shareBox()).toHaveAccessibleDescription(/last 30 days/));
    expect(shareBox()).toHaveFocus();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /LINK MATCH HISTORY/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Valve is rate-limiting requests");
  });

  it("expired share code: says why, opens the form, keeps the auth code and sends only a new share code", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({ "PUT /steam/match-access": { status: 200, body: LINKED_ACCESS } });
    const onChange = renderAccount(relinkShare);
    expect(screen.getByRole("alert")).toHaveTextContent(/Re-link needed to keep syncing.*last 30 days.*Your authentication code is kept/);
    expect(shareBox()).toHaveFocus();
    expect(screen.getByLabelText(/NEW MATCH SHARING CODE/)).toBe(shareBox());
    expect(authBox()).toHaveAttribute("placeholder", "Keep ****-*****-FG56");
    await user.paste(SHARE);
    await user.click(consent());
    await user.click(screen.getByRole("button", { name: /SAVE NEW CODES/ }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({ auth_code: "", share_code: SHARE, consent: true });
  });

  it("revoked auth code: the auth code box is required again", async () => {
    const user = userEvent.setup();
    const api = installFakeApi({});
    renderAccount(relinkAuth);
    expect(screen.getByRole("alert")).toHaveTextContent(/Valve rejected your saved Game Authentication Code/);
    expect(authBox()).toHaveFocus();
    expect(authBox()).toHaveAttribute("placeholder", "ABCD-EFGHI-JKLM");
    await user.type(shareBox(), SHARE);
    await user.click(consent());
    await user.click(screen.getByRole("button", { name: /SAVE NEW CODES/ }));
    expect(api.calls).toHaveLength(0);
    expect(authBox()).toHaveAccessibleDescription(/Paste your Game Authentication Code/);
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
    expect(empty).toHaveTextContent("Link your match history above (step 2)");
    expect(empty).toHaveTextContent("UPLOAD A DEMO");
    expect(empty).toHaveTextContent("game/csgo/replays");
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
    expect(screen.getByText(/Sync is paused: paste a recent share code above/)).toBeInTheDocument();
    expect(screen.getByLabelText("No matches yet")).toHaveTextContent("Sync is paused until you update your codes above.");
  });

  it("linked and fine: empty state points at the Sync button", async () => {
    installFakeApi(routes(linked));
    render(<Matches me={linked} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByRole("button", { name: /SYNC MATCHES/ })).toBeEnabled();
    expect(screen.getByLabelText("No matches yet")).toHaveTextContent("Press Sync matches above.");
  });
});
