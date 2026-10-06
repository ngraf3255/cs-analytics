import { act as rtlAct, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, FakeXHR, installFakeApi, job, POLL_MS } from "../test/fakeApi";
import matchesOutdated from "../test/fixtures/matches_outdated.json";
import matchesPersonal from "../test/fixtures/matches_personal.json";
import meFixture from "../test/fixtures/me_personal.json";
import reportOutdated from "../test/fixtures/report_outdated.json";
import reportPersonal from "../test/fixtures/report_personal.json";
import summaryOutdated from "../test/fixtures/summary_outdated.json";
import summaryPersonal from "../test/fixtures/summary_personal.json";
import uploadQueued from "../test/fixtures/upload_queued.json";
import uploadUpdated from "../test/fixtures/upload_updated.json";
import { Matches } from "./Matches";
import { MatchesSummaryPanel } from "./MatchesSummary";
import type { Me, UploadJob } from "./types";

// Fixtures: real responses of the local API (SQLite, 2026-10-05) for SteamID 76561198157151718.
// *_outdated.json: the synced de_ancient match marked as parsed by parser version 1 in the local DB
// (outdated: parser_updated). upload_updated.json: the FACEIT de_mirage demo uploaded again after its
// stored match was set back to version 1 with the knife round counted (25 rounds): the job re-parsed
// it (created false, updated true) and the match has its 24 rounds.
const me = meFixture as Me;
const ANCIENT = matchesOutdated.matches.find((m) => m.map_name === "de_ancient")!;
const FACEIT = matchesPersonal.matches.find((m) => m.source === "upload")!;
const queued = uploadQueued.job as UploadJob;

function routes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /matches?limit=50&offset=0": { status: 200, body: matchesOutdated },
    "GET /matches/summary": { status: 200, body: summaryOutdated },
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...me.sync, jobs: [] } },
    [`GET /matches/${ANCIENT.id}`]: { status: 200, body: reportOutdated },
    [`GET /matches/${FACEIT.id}`]: { status: 200, body: reportPersonal },
    ...extra,
  };
}

async function act(fn: () => void) {
  await rtlAct(async () => { fn(); await vi.advanceTimersByTimeAsync(0); });
}

describe("matches parsed by an older version: re-upload to update", () => {
  beforeEach(() => { vi.useFakeTimers(); FakeXHR.install(); });

  it("tags only the outdated match in the list and explains it in its report", async () => {
    installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    const items = screen.getAllByRole("listitem");
    const tagged = items.filter((item) => within(item).queryByLabelText("Outdated"));
    expect(tagged).toHaveLength(1);
    expect(tagged[0]).toHaveTextContent("ancient");
    expect(within(tagged[0]).getByLabelText("Outdated")).toHaveAttribute(
      "title", "Parsed by an older version (warmup or knife rounds may still be counted). Upload this demo again to update it.");

    fireEvent.click(screen.getByRole("button", { name: /ancient/ }));
    await advance();
    expect(screen.getAllByRole("note").map((n) => n.textContent)).toContain(
      "Parsed by an older version (warmup or knife rounds may still be counted). Upload this demo again to update it.");
  });

  it("an up-to-date report has no such note", async () => {
    installFakeApi(routes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /Won · mirage · 13–11 · 29–17/i }));
    await advance();
    expect(screen.queryByText(/Upload this demo again to update it/)).not.toBeInTheDocument();
  });

  it("the summary counts outdated matches in one line", async () => {
    installFakeApi({ "GET /matches/summary": { status: 200, body: summaryOutdated } });
    render(<MatchesSummaryPanel variant="stats" refreshKey={1} />);
    await advance();
    const panel = screen.getByRole("region", { name: "Across your matches" });
    expect(within(panel).getByLabelText("Previous matches summary")).toHaveTextContent("MATCHES3");
    const note = within(panel).getByRole("note");
    expect(note).toHaveTextContent(/^1 match marked OUTDATED — re-upload that demo\.$/);  // the tag's tooltip explains why
    expect(note).not.toHaveTextContent(/warmup|knife|counted as stored/i);
  });

  it("no outdated matches: no count, no note", async () => {
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": { status: 200, body: matchesPersonal },
      "GET /matches/summary": { status: 200, body: summaryPersonal },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.queryByLabelText("Outdated")).not.toBeInTheDocument();
    expect(screen.queryByText(/to re-upload/)).not.toBeInTheDocument();
  });

  it("re-uploading the demo of an outdated match says it was updated, not 'already in your matches'", async () => {
    const JOB = `/matches/upload/${queued.id}`;
    installFakeApi(routes({
      "GET /matches?limit=50&offset=0": [{ status: 200, body: matchesOutdated }, { status: 200, body: matchesPersonal }],
      [`GET ${JOB}`]: { status: 200, body: { job: { ...uploadUpdated.job, id: queued.id } } },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    fireEvent.change(document.querySelector("label.upload-button input[type=file]") as HTMLInputElement,
      { target: { files: [new File(["demo"], "faceit.dem")] } });
    await act(() => FakeXHR.last().respond(202, { job: job(queued, { queue_position: 0 }) }));
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent("Match updated from the demo: mirage, 24 rounds.");
    expect(screen.getByRole("status")).not.toHaveTextContent("already in your matches");
  });
});
