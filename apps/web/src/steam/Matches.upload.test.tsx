import { act as rtlAct, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { advance, FakeXHR, installFakeApi, job, POLL_MS } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";
import matchesFixture from "../test/fixtures/matches.json";
import reportFixture from "../test/fixtures/report.json";
import uploadDone from "../test/fixtures/upload_done.json";
import uploadQueued from "../test/fixtures/upload_queued.json";
import { Matches } from "./Matches";
import type { Me, UploadJob } from "./types";

const me = meFixture as Me;
const queued = uploadQueued.job as UploadJob;
const done = uploadDone.job as UploadJob;
const JOB = `/matches/upload/${queued.id}`;
const MATCH_ID = matchesFixture.matches[0].id;

function baseRoutes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /matches?limit=50&offset=0": [{ status: 200, body: { matches: [], limit: 50, offset: 0 } }, { status: 200, body: matchesFixture }],
    "GET /matches/upload?limit=5": { status: 200, body: { jobs: [] } },
    "GET /steam/sync": { status: 200, body: { ...me.sync, jobs: [] } },
    [`GET /matches/${MATCH_ID}`]: { status: 200, body: reportFixture },
    ...extra,
  };
}

const fileInput = () => document.querySelector("label.upload-button input[type=file]") as HTMLInputElement;
const pick = (name: string) => fireEvent.change(fileInput(), { target: { files: [new File(["demo"], name)] } });

describe("demo upload", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeXHR.install();
  });

  it("walks the job through queued → unpacking % → parsing → saving → done and opens the report", async () => {
    const api = installFakeApi(baseRoutes({
      [`GET ${JOB}`]: [
        { status: 200, body: { job: job(queued, { status: "processing", stage: "decompressing", progress: 0.42, queue_position: null }) } },
        { status: 200, body: { job: job(queued, { status: "processing", stage: "parsing", progress: null, queue_position: null }) } },
        { status: 200, body: { job: job(queued, { status: "processing", stage: "storing", progress: null, queue_position: null }) } },
        { status: 200, body: uploadDone },
      ],
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByText("UPLOAD .DEM")).toBeInTheDocument();
    expect(screen.getByText("No imported matches yet.")).toBeInTheDocument();

    pick("match.dem");
    const xhr = FakeXHR.last();
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toMatch(/\/matches\/upload$/);
    expect(xhr.headers["X-Requested-With"]).toBe("csa");
    expect(xhr.headers["Content-Type"]).toBe("application/octet-stream");
    expect(xhr.withCredentials).toBe(true);

    await act(() => xhr.progress(30, 100));
    expect(screen.getByText("UPLOADING 30%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Demo upload progress" })).toHaveAttribute("aria-valuenow", "30");
    expect(fileInput()).toBeDisabled();

    await act(() => xhr.respond(202, { job: job(queued, { queue_position: 1 }) }));
    expect(screen.getByText("QUEUED…")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for 1 other demo to finish first/)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    await advance(POLL_MS);
    expect(screen.getByText("UNPACKING 42%")).toBeInTheDocument();
    expect(screen.getByText(/Unpacking the \.bz2 archive on the server/)).toBeInTheDocument();
    await advance(POLL_MS);
    expect(screen.getByText("PARSING…")).toBeInTheDocument();
    await advance(POLL_MS);
    expect(screen.getByText("SAVING…")).toBeInTheDocument();
    await advance(POLL_MS);

    expect(screen.getByRole("status")).toHaveTextContent("Demo imported: mirage, 10 rounds.");
    expect(screen.getByText("UPLOAD .DEM")).toBeInTheDocument();
    expect(fileInput()).not.toBeDisabled();
    expect(api.count(`GET ${JOB}`)).toBe(4);
    // the list is reloaded and the new match's report is opened
    expect(screen.getByText("UPLOADED")).toBeInTheDocument();
    expect(api.count(`GET /matches/${MATCH_ID}`)).toBe(1);
    expect(screen.getByText(/9 of 10 rounds could be scored/)).toBeInTheDocument();

    // no more polling once the job is done
    await advance(POLL_MS * 3);
    expect(api.count(`GET ${JOB}`)).toBe(4);
  });

  it("shows a failed job's error in plain language", async () => {
    installFakeApi(baseRoutes({
      [`GET ${JOB}`]: { status: 200, body: { job: job(queued, { status: "failed", error: "demo_parse_failed", queue_position: null }) } },
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    pick("broken.dem");
    await act(() => FakeXHR.last().respond(202, uploadQueued));
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent("The demo could not be parsed.");
    expect(screen.getByText("UPLOAD .DEM")).toBeInTheDocument();
  });

  it("explains a full queue (429 upload_queue_full) and lets the user retry", async () => {
    installFakeApi(baseRoutes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    pick("match.dem.bz2");
    await act(() => FakeXHR.last().respond(429, { detail: "upload_queue_full" }));
    expect(screen.getByRole("status")).toHaveTextContent("The server already has several demos waiting to be parsed. Try again in a few minutes.");
    expect(screen.getByText("UPLOAD .DEM")).toBeInTheDocument();
    expect(fileInput()).not.toBeDisabled();
  });

  it("maps a bare 413 and a dropped connection to plain messages", async () => {
    installFakeApi(baseRoutes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    pick("huge.dem");
    await act(() => FakeXHR.last().respond(413, null));
    expect(screen.getByRole("status")).toHaveTextContent("That demo is larger than the server allows.");
    pick("match.dem");
    await act(() => FakeXHR.last().fail());
    expect(screen.getByRole("status")).toHaveTextContent("The upload was interrupted. Check your connection and try again.");
  });

  it("rejects files that are not demos without uploading", async () => {
    installFakeApi(baseRoutes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    pick("screenshot.png");
    expect(FakeXHR.instances).toHaveLength(0);
    expect(screen.getByRole("status")).toHaveTextContent("Choose a CS2 demo file ending in .dem or .dem.bz2.");
  });

  it("opens the existing report when the server already had the demo (200, created=false)", async () => {
    installFakeApi(baseRoutes());
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    pick("match.dem");
    await act(() => FakeXHR.last().respond(200, { job: { ...done, created: false } }));
    await advance();
    expect(screen.getByRole("status")).toHaveTextContent("That demo was already imported. Opening its report.");
    expect(screen.getByText(/9 of 10 rounds could be scored/)).toBeInTheDocument();
  });

  it("resumes following a running parse after a page reload", async () => {
    const running = job(queued, { status: "processing", stage: "parsing", queue_position: null });
    const api = installFakeApi(baseRoutes({
      "GET /matches/upload?limit=5": { status: 200, body: { jobs: [running, { ...done, id: "older" }] } },
      [`GET ${JOB}`]: [{ status: 200, body: { job: running } }, { status: 200, body: uploadDone }],
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    expect(screen.getByText("PARSING…")).toBeInTheDocument();
    expect(fileInput()).toBeDisabled();
    await advance(POLL_MS);
    expect(screen.getByText("PARSING…")).toBeInTheDocument();
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent("Demo imported: mirage, 10 rounds.");
    expect(api.count(`GET ${JOB}`)).toBe(2);
    expect(FakeXHR.instances).toHaveLength(0);
  });

  it("keeps polling through server blips and reports a job the server lost (404)", async () => {
    installFakeApi(baseRoutes({
      [`GET ${JOB}`]: [{ status: 0 }, { status: 503, body: {} }, { status: 404, body: { detail: "upload_job_not_found" } }],
    }));
    render(<Matches me={me} onMeChange={async () => undefined} />);
    await advance();
    pick("match.dem");
    await act(() => FakeXHR.last().respond(202, uploadQueued));
    await advance(POLL_MS);
    await advance(POLL_MS);
    expect(screen.getByText("QUEUED…")).toBeInTheDocument();
    await advance(POLL_MS);
    expect(screen.getByRole("status")).toHaveTextContent("That upload is no longer tracked. Check your matches below.");
  });
});

/** Run a synchronous XHR callback inside act() and let the resulting promises settle. */
async function act(fn: () => void) {
  await rtlAct(async () => { fn(); await vi.advanceTimersByTimeAsync(0); });
}
