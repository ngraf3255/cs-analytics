import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi, job, POLL_MS } from "../test/fakeApi";
import meFixture from "../test/fixtures/me_auto_sync.json";
import summaryEmpty from "../test/fixtures/summary_empty.json";
import syncFinal from "../test/fixtures/sync_final.json";
import { AUTO_SYNC_WATCH_MS, Matches } from "./Matches";
import type { Me, UploadJob } from "./types";

const me = meFixture as Me;
const [doneSecond, doneFirst] = syncFinal.jobs as UploadJob[];
const state = (jobs: UploadJob[], patch: Record<string, unknown> = {}) =>
  ({ status: 200, body: { ...me.sync, ...patch, active_jobs: jobs.filter((j) => j.status === "queued" || j.status === "processing").length, jobs } });
const list = (matches: unknown[]) => ({ status: 200, body: { matches, limit: 50, offset: 0 } });
const syncButton = () => document.querySelector("button.sync-button") as HTMLButtonElement;

describe("matches found by automatic sync appear without pressing Sync", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("follows matches the scheduler queued, then reloads the list", async () => {
    const queued = job(doneFirst, { status: "queued", stage: null, match: null, created: null as unknown as boolean, queue_position: 0 });
    const api = installFakeApi({
      "GET /matches?limit=50&offset=0": [list([]), list([doneFirst.match])],
      "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
      "GET /matches/summary": { status: 200, body: summaryEmpty },
      "GET /steam/sync": [
        state([]),  // on mount
        state([queued]),  // a minute later: the scheduler queued a match
        state([job(queued, { status: "processing", stage: "downloading", progress: 0.5, queue_position: null })]),
        state([doneFirst]),
      ],
    });
    const onMeChange = vi.fn(async () => undefined);
    render(<Matches me={me} onMeChange={onMeChange} />);
    await advance();
    expect(document.querySelector(".sync-meta")).toHaveTextContent("· auto-sync on");
    expect(api.count("GET /steam/sync")).toBe(1);
    await advance(AUTO_SYNC_WATCH_MS);
    expect(syncButton()).toHaveTextContent("MATCH 1/1 · QUEUED…");
    await advance(POLL_MS);
    expect(syncButton()).toHaveTextContent("DOWNLOADING 50%");
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent("Imported 1 new match.");
    expect(api.count("GET /matches?limit=50&offset=0")).toBe(2);
    expect(onMeChange).toHaveBeenCalled();
    expect(syncButton()).toHaveTextContent("SYNC MATCHES");
  });

  it("reloads the list when a background sync attached matches without jobs; idle when auto-sync is off", async () => {
    const api = installFakeApi({
      "GET /matches?limit=50&offset=0": [list([]), list([doneSecond.match])],
      "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
      "GET /matches/summary": { status: 200, body: summaryEmpty },
      "GET /steam/sync": [state([]), state([]), state([], { last_synced_at: "2026-10-05T11:10:00Z" })],
    });
    const { unmount } = render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    await advance(AUTO_SYNC_WATCH_MS);  // nothing new
    expect(api.count("GET /matches?limit=50&offset=0")).toBe(1);
    await advance(AUTO_SYNC_WATCH_MS);  // last_synced_at moved: matches were attached
    expect(api.count("GET /matches?limit=50&offset=0")).toBe(2);
    unmount();

    const off: Me = { ...me, sync: { ...me.sync, auto_sync: { ...me.sync.auto_sync!, enabled: false, active: false, paused_reason: "turned_off" } } };
    const calls = api.count("GET /steam/sync");
    render(<Matches me={off} onMeChange={async () => undefined} />);
    await advance();
    await advance(AUTO_SYNC_WATCH_MS * 3);
    expect(api.count("GET /steam/sync")).toBe(calls + 1);  // only the on-mount check
    expect(document.querySelector(".sync-meta")).not.toHaveTextContent("auto-sync on");
  });
});
