import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installFakeApi } from "../test/fakeApi";
import autoOff from "../test/fixtures/auto_sync_off.json";
import autoOn from "../test/fixtures/auto_sync_on.json";
import meFixture from "../test/fixtures/me_auto_sync.json";
import { everyText, timeAgo, timeUntil } from "./format";
import { SteamAccount } from "./SteamAccount";
import type { AutoSync, Me } from "./types";

// Captured from the local API (fake Valve, AUTO_SYNC_INTERVAL_SECONDS=120): a user who linked and was
// synced by the scheduler 7 s later without pressing Sync (3 matches attached), then PUT /steam/auto-sync.
const me = meFixture as Me;
const SYNCED = Date.parse("2026-10-05T10:38:56.700661Z");
const withAuto = (patch: Partial<AutoSync>, base: Me = me): Me => ({
  ...base, sync: { ...base.sync, auto_sync: { ...base.sync.auto_sync!, ...patch } },
});

function renderAccount(account: Me) {
  const onChange = vi.fn(async () => undefined);
  render(<SteamAccount me={account} onChange={onChange} onSignedOut={() => undefined} />);
  return onChange;
}

const toggle = () => screen.getByRole("switch", { name: /Auto-sync new matches/ }) as HTMLInputElement;

beforeEach(() => {
  vi.spyOn(Date, "now").mockReturnValue(SYNCED + 5 * 60_000);  // five minutes after the automatic sync
});
afterEach(() => vi.restoreAllMocks());

describe("automatic sync in the linked-account area", () => {
  it("says when the last sync was and that auto-sync is on", () => {
    installFakeApi({});
    renderAccount(withAuto({ next_at: new Date(SYNCED + 30 * 60_000).toISOString() }));
    expect(screen.getByRole("status")).toHaveTextContent("Last synced 5 min ago, auto-sync on · next check in 25 min");
    expect(toggle()).toBeChecked();
    expect(screen.getByText(/checks Valve every 2 min/)).toBeInTheDocument();
  });

  it("turns it off and back on through PUT /steam/auto-sync", async () => {
    const api = installFakeApi({
      "PUT /steam/auto-sync": (call) => ({ status: 200, body: (call.body as { enabled: boolean }).enabled ? autoOn : autoOff }),
    });
    const onChange = renderAccount(me);
    fireEvent.click(toggle());
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Last synced 5 min ago, auto-sync off"));
    const put = api.calls.find((c) => c.method === "PUT");
    expect(put?.body).toEqual({ enabled: false });
    expect(put?.headers.get("X-Requested-With")).toBe("csa");
    expect(onChange).toHaveBeenCalled();
    expect(toggle()).not.toBeChecked();
    fireEvent.click(toggle());
    await waitFor(() => expect(toggle()).toBeChecked());
    expect(api.calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ enabled: false }, { enabled: true }]);
  });

  it("shows an error and keeps the toggle when the server refuses", async () => {
    installFakeApi({ "PUT /steam/auto-sync": { status: 0 } });
    renderAccount(me);
    fireEvent.click(toggle());
    expect(await screen.findByRole("alert")).toHaveTextContent("Can’t reach the cs-analytics server");
    expect(toggle()).toBeChecked();
  });

  it("explains a failing automatic sync and the backoff", () => {
    installFakeApi({});
    renderAccount(withAuto({ last_error: "rate_limited", failures: 3, next_at: new Date(SYNCED + 4 * 3600_000).toISOString() }));
    expect(screen.getByRole("status")).toHaveTextContent("auto-sync on · next check in 4 h");
    expect(screen.getByText(/Last automatic sync didn’t work: Valve is rate-limiting sync/)).toHaveTextContent(
      "(3 times in a row; retrying less often)");
  });

  it("says why it is paused (re-link, server without demo download) and hides the toggle when the server has it off", () => {
    installFakeApi({});
    const { unmount } = render(<SteamAccount me={withAuto({ active: false, paused_reason: "needs_relink", next_at: null })}
      onChange={async () => undefined} onSignedOut={() => undefined} />);
    expect(screen.getByRole("status")).toHaveTextContent("Last synced 5 min ago, auto-sync paused until you re-link");
    unmount();
    const r2 = render(<SteamAccount me={withAuto({ active: false, paused_reason: "demo_retrieval_not_configured", next_at: null })}
      onChange={async () => undefined} onSignedOut={() => undefined} />);
    expect(screen.getByRole("status")).toHaveTextContent("auto-sync starts once the server can download demos");
    expect(toggle()).toBeInTheDocument();
    r2.unmount();
    renderAccount(withAuto({ active: false, paused_reason: "server_disabled", interval_seconds: null, next_at: null }));
    expect(screen.getByRole("status")).toHaveTextContent("auto-sync isn’t available on this server");
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("never synced yet / older API without auto_sync", () => {
    installFakeApi({});
    const never: Me = withAuto({ next_at: new Date(SYNCED).toISOString() }, { ...me, sync: { ...me.sync, last_synced_at: null } });
    const { unmount } = render(<SteamAccount me={never} onChange={async () => undefined} onSignedOut={() => undefined} />);
    expect(screen.getByRole("status")).toHaveTextContent("Not synced yet, auto-sync on · next check any minute now");
    unmount();
    const { auto_sync: _drop, last_synced_at: _drop2, ...oldSync } = me.sync;
    renderAccount({ ...me, sync: oldSync });
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.getByText(/Match history linked/)).toBeInTheDocument();
  });
});

describe("relative times", () => {
  const now = Date.parse("2026-10-05T12:00:00Z");
  it("formats past, future and intervals", () => {
    expect(timeAgo("2026-10-05T11:59:30Z", now)).toBe("just now");
    expect(timeAgo("2026-10-05T11:30:00Z", now)).toBe("30 min ago");
    expect(timeAgo("2026-10-05T09:00:00Z", now)).toBe("3 h ago");
    expect(timeAgo("2026-10-02T12:00:00Z", now)).toBe("3 days ago");
    expect(timeUntil("2026-10-05T11:00:00Z", now)).toBe("any minute now");
    expect(timeUntil("2026-10-05T12:25:00Z", now)).toBe("in 25 min");
    expect(everyText(1800)).toBe("every 30 min");
    expect(everyText(7200)).toBe("every 2 h");
  });
});
