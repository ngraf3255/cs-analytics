import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi, job, POLL_MS } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";
import syncFinal from "../test/fixtures/sync_final.json";
import syncPost from "../test/fixtures/sync_post.json";
import { Matches } from "./Matches";
import type { Me, SyncResult, UploadJob } from "./types";

const linkedMe: Me = {
  ...(meFixture as Me),
  match_access: { linked: true, auth_code_hint: "****-*****-FG56", linked_at: "2026-10-05T07:30:52.928112Z", updated_at: "2026-10-05T07:30:52.928112Z" },
};
const posted = syncPost as SyncResult;
const [first, second] = posted.jobs;  // oldest first, as POST returns them
// GET /steam/sync lists jobs newest first
const [finalSecond, finalFirst] = syncFinal.jobs as UploadJob[];
const syncState = (jobs: UploadJob[], active = jobs.filter((j) => j.status === "queued" || j.status === "processing").length) =>
  ({ status: 200, body: { ...syncFinal, active_jobs: active, jobs } });

function routes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /matches?limit=50&offset=0": { status: 200, body: { matches: [], limit: 50, offset: 0 } },
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    ...extra,
  };
}

const syncButton = () => document.querySelector("button.sync-button") as HTMLButtonElement;

describe("Steam sync", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("follows the queued matches (match n/N, downloading %, unpacking %) and summarises when all finish", async () => {
    const processing = (j: UploadJob, stage: string, progress: number | null = null) =>
      job(j, { status: "processing", stage, progress, queue_position: null, started_at: "2026-10-05T07:30:53Z" });
    const api = installFakeApi(routes({
      "POST /steam/sync": { status: 202, body: posted },
      "GET /steam/sync": [
        syncState([]),  // on mount: nothing running
        syncState([second, processing(first, "locating")]),
        syncState([second, processing(first, "downloading", 0.37)]),
        syncState([second, processing(first, "parsing")]),
        syncState([processing(second, "decompressing", 0.5), finalFirst]),
        syncState([processing(second, "storing"), finalFirst]),
        syncState([finalSecond, finalFirst], 0),
      ],
    }));
    const onMeChange = vi.fn(async () => undefined);
    render(<Matches me={linkedMe} onMeChange={onMeChange} />);
    await advance();
    expect(syncButton()).toHaveTextContent("SYNC MATCHES");
    expect(syncButton()).toBeEnabled();

    fireEvent.click(syncButton());
    expect(syncButton()).toHaveTextContent("SYNCING…");
    expect(syncButton()).toBeDisabled();
    await advance();
    const call = api.calls.find((c) => c.method === "POST" && c.path === "/steam/sync")!;
    expect(call.headers.get("X-Requested-With")).toBe("csa");
    expect(syncButton()).toHaveTextContent("MATCH 1/2 · QUEUED…");

    const seen: string[] = [];
    for (let i = 0; i < 5; i++) {
      await advance(POLL_MS);
      seen.push(syncButton().textContent!.replace("↻", ""));
    }
    expect(seen).toEqual([
      "MATCH 1/2 · FINDING DEMO…",
      "MATCH 1/2 · DOWNLOADING 37%",
      "MATCH 1/2 · PARSING…",
      "MATCH 2/2 · UNPACKING 50%",
      "MATCH 2/2 · SAVING…",
    ]);
    expect(syncButton()).toBeDisabled();
    await advance(POLL_MS);

    expect(screen.getByRole("status")).toHaveTextContent("Imported 1 new match. 1 match was already in your list. You’re up to date.");
    expect(syncButton()).toHaveTextContent("SYNC MATCHES");
    expect(syncButton()).toBeEnabled();
    expect(onMeChange).toHaveBeenCalled();
    const polls = api.count("GET /steam/sync");
    await advance(POLL_MS * 3);
    expect(api.count("GET /steam/sync")).toBe(polls);
  });

  it("explains demo_retrieval_not_configured (no bot account) in plain language", async () => {
    installFakeApi(routes({
      "GET /steam/sync": syncState([]),
      "POST /steam/sync": { status: 200, body: { status: "error", queued: 0, skipped: 0, processed: 0, has_more: false, error: "demo_retrieval_not_configured", jobs: [] } },
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(syncButton());
    await advance();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Match sync is wired up, but demo download is not configured on the server yet (needs a Game Coordinator bot). Your share-code cursor was not advanced.");
    expect(syncButton()).toHaveTextContent("SYNC MATCHES");
  });

  it("explains a job that failed with demo_bot_auth_failed", async () => {
    const failed = job(first, { status: "failed", error: "demo_bot_auth_failed", queue_position: null, attempts: 1 });
    installFakeApi(routes({
      "POST /steam/sync": { status: 202, body: { ...posted, queued: 1, jobs: [first] } },
      "GET /steam/sync": [syncState([]), syncState([failed], 0)],
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(syncButton());
    await advance();
    await advance(POLL_MS);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("The server’s Steam demo bot could not sign in, so demos can’t be fetched right now. Your next sync retries the match.");
    expect(status).toHaveClass("steam-error");
  });

  it("shows request-level rejections (429 too_soon, 409 already_running / needs_share_code)", async () => {
    installFakeApi(routes({
      "GET /steam/sync": syncState([]),
      "POST /steam/sync": [
        { status: 429, body: { detail: "too_soon" } },
        { status: 409, body: { detail: "already_running" } },
        { status: 409, body: { detail: "needs_share_code" } },
      ],
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(syncButton());
    await advance();
    const tooSoon = screen.getByRole("status");
    expect(tooSoon).toHaveTextContent("Wait about 30 seconds between syncs.");
    expect(tooSoon).toHaveClass("steam-notice");  // expected gate, not a failure
    fireEvent.click(syncButton());
    await advance();
    const running = screen.getByRole("status");
    expect(running).toHaveTextContent("A sync is already running.");
    expect(running).toHaveClass("steam-notice");
    fireEvent.click(syncButton());
    await advance();
    const share = screen.getByRole("status");
    expect(share).toHaveTextContent("Add a share code in Account settings after your next match.");
    expect(share).toHaveClass("steam-error");
  });

  it("on 409 already_running attaches to active sync jobs instead of failing", async () => {
    const running = job(first, { status: "processing", stage: "downloading", progress: 0.4, queue_position: null });
    installFakeApi(routes({
      "GET /steam/sync": [
        syncState([]),  // mount
        syncState([running]),  // after 409: attach
        syncState([finalFirst], 0),
      ],
      "POST /steam/sync": { status: 409, body: { detail: "already_running" } },
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(syncButton());
    await advance();
    expect(screen.getByRole("status")).toHaveTextContent("A sync is already running.");
    expect(syncButton()).toHaveTextContent("MATCH 1/1 · DOWNLOADING 40%");
    expect(syncButton()).toBeDisabled();
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent(/Imported|up to date/i);
  });

  it("reports an up-to-date sync and a partial one (sync more)", async () => {
    installFakeApi(routes({
      "GET /steam/sync": syncState([]),
      "POST /steam/sync": [
        { status: 200, body: { status: "partial", queued: 0, skipped: 3, processed: 3, has_more: true, error: null, jobs: [] } },
        { status: 200, body: { status: "up_to_date", queued: 0, skipped: 1, processed: 1, has_more: false, error: null, jobs: [] } },
      ],
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(syncButton());
    await advance();
    expect(screen.getByRole("status")).toHaveTextContent("No new demos to download. 3 matches you already had were skipped. More matches are waiting. Sync again to continue.");
    expect(syncButton()).toHaveTextContent("SYNC MORE MATCHES");
    fireEvent.click(syncButton());
    await advance();
    expect(screen.getByRole("status")).toHaveTextContent("You’re up to date. 1 match you already had was skipped.");
    expect(syncButton()).toHaveTextContent("SYNC MATCHES");
  });

  it("resumes following running sync jobs after a page reload (oldest first)", async () => {
    const running = job(first, { status: "processing", stage: "downloading", progress: 0.8, queue_position: null });
    installFakeApi(routes({
      "GET /steam/sync": [syncState([second, running]), syncState([second, running]), syncState([finalSecond, finalFirst], 0)],
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    expect(syncButton()).toHaveTextContent("MATCH 1/2 · DOWNLOADING 80%");
    expect(syncButton()).toBeDisabled();
    expect(screen.getByText(/Downloading the match demo from Valve in the background/)).toBeInTheDocument();
    await advance(POLL_MS);
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent("Imported 1 new match. 1 match was already in your list.");
  });

  it("disables Sync until match history is linked", async () => {
    installFakeApi(routes({ "GET /steam/sync": syncState([]) }));
    render(<Matches me={meFixture as Me} onMeChange={async () => undefined} />);
    await advance();
    expect(syncButton()).toBeDisabled();
    expect(screen.getByText(/Link match history in/)).toBeInTheDocument();
  });

  it("does not claim 'up to date' when the history walk stopped with an error", async () => {
    installFakeApi(routes({
      "POST /steam/sync": { status: 202, body: { ...posted, status: "error", error: "valve_unavailable", queued: 1, jobs: [first] } },
      "GET /steam/sync": [syncState([]), syncState([finalFirst], 0)],
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(syncButton());
    await advance();
    expect(screen.getByRole("status")).toHaveTextContent("Matches found before the error are still being imported.");
    await advance(POLL_MS);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Imported 1 new match.");
    expect(status).toHaveTextContent("Valve’s match-history service did not respond.");
    expect(status).not.toHaveTextContent("up to date");
  });

  it("leads with the failure instead of 'Imported 0 new matches'", async () => {
    const failed = job(first, { status: "failed", error: "demo_not_ready", queue_position: null, attempts: 1 });
    installFakeApi(routes({
      "POST /steam/sync": { status: 202, body: { ...posted, queued: 1, jobs: [first] } },
      "GET /steam/sync": [syncState([]), syncState([failed], 0)],
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(syncButton());
    await advance();
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent(/^That match’s demo isn’t on Valve’s servers yet\. Sync again later to retry it\.$/);
  });

  it("after a reload it does not know whether more matches wait, so it does not claim 'up to date'", async () => {
    const running = job(first, { status: "processing", stage: "parsing", queue_position: null });
    installFakeApi(routes({ "GET /steam/sync": [syncState([running]), syncState([finalFirst], 0)] }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent(/^Imported 1 new match\.$/);
  });

  it("counts matches another player already imported (attached, no download) as new for this user", async () => {
    const done = job(finalFirst, { created: true });
    installFakeApi(routes({
      "POST /steam/sync": { status: 202, body: { ...posted, queued: 1, attached: 1, processed: 2, jobs: [first] } },
      "GET /steam/sync": [syncState([]), syncState([done], 0)],
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(syncButton());
    await advance();
    expect(syncButton()).toHaveTextContent("MATCH 1/1");
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent(/^Imported 2 new matches\. You’re up to date\.$/);
  });

  it("says attached matches were imported even when nothing needed a download", async () => {
    const api = installFakeApi(routes({
      "GET /steam/sync": syncState([]),
      "POST /steam/sync": { status: 200, body: { status: "up_to_date", queued: 0, skipped: 1, attached: 2, processed: 3, has_more: false, error: null, jobs: [] } },
    }));
    render(<Matches me={linkedMe} onMeChange={async () => undefined} />);
    await advance();
    const lists = api.count("GET /matches?limit=50&offset=0");
    fireEvent.click(syncButton());
    await advance();
    expect(screen.getByRole("status")).toHaveTextContent(
      /^Imported 2 new matches \(already analysed on the server, no download needed\)\. 1 match you already had was skipped\. You’re up to date\.$/);
    expect(api.count("GET /matches?limit=50&offset=0")).toBeGreaterThan(lists);
  });
});
