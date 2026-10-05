import { useCallback, useEffect, useRef, useState } from "react";
import { isJobActive, steamApi, type UploadProgress } from "./api";
import { ApiError, messageFor } from "./errors";
import type { MatchReport, MatchSummary, Me, SyncResult, UploadJob } from "./types";

const MATCH_STATUS: Record<string, string> = {
  demo_unavailable: "Demo is no longer available from Valve.",
  demo_too_large: "Demo exceeded the server’s size limit.",
  parser_error: "Demo could not be parsed.",
  demo_has_no_rounds: "Demo has no completed rounds.",
};

const sideName = (side: string | null | undefined) => (side === "ct" ? "CT" : side === "t" ? "T" : "—");
const mapLabel = (map: string | null) => (map ? map.replace(/^de_/, "").replaceAll("_", " ") : "Unknown map");

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : word.endsWith("ch") ? "es" : "s"}`;

/** Message right after POST /steam/sync (before the queued matches are imported). */
function syncMessage(result: SyncResult): { tone: "ok" | "error"; text: string } | null {
  const skipped = result.skipped ? ` ${plural(result.skipped, "match")} you already had ${result.skipped === 1 ? "was" : "were"} skipped.` : "";
  if (result.status === "error") {
    const code = result.error === "invalid_auth_code" ? "invalid_auth_code_status" : result.error;
    return { tone: "error", text: `${messageFor(code, "Sync failed.")}${result.jobs.length ? " Matches found before the error are still being imported." : ""}` };
  }
  if (result.jobs.length) return null;  // the job progress tells the rest
  if (result.status === "queue_full") return { tone: "ok", text: `${messageFor("sync_queue_full")}${skipped}` };
  if (result.status === "partial") return { tone: "ok", text: `No new demos to download.${skipped} More matches are waiting. Sync again to continue.` };
  return { tone: "ok", text: `You’re up to date.${skipped}` };
}

/** Summary once every job of a sync has finished. */
function syncSummary(jobs: UploadJob[], hasMore: boolean): { tone: "ok" | "error"; text: string } {
  const imported = jobs.filter((j) => j.status === "done" && j.match?.status === "imported" && j.created).length;
  const known = jobs.filter((j) => j.status === "done" && j.match?.status === "imported" && !j.created).length;
  const missing = jobs.filter((j) => j.status === "done" && j.match && j.match.status !== "imported").length;
  const failed = jobs.filter((j) => j.status === "failed");
  const parts = [`Imported ${plural(imported, "new match")}.`];
  if (known) parts.push(`${plural(known, "match")} ${known === 1 ? "was" : "were"} already imported.`);
  if (missing) parts.push(`${plural(missing, "demo")} couldn’t be imported (see the list below; you can upload ${missing === 1 ? "it" : "them"} by hand).`);
  if (failed.length) parts.push(failed.length === 1 ? messageFor(failed[0].error, "One match failed.") : `${failed.length} matches failed: ${messageFor(failed[0].error, "try syncing again.")}`);
  else if (hasMore) parts.push("More matches are waiting. Sync again to continue.");
  else if (!missing) parts.push("You’re up to date.");
  return { tone: failed.length && !imported ? "error" : "ok", text: parts.join(" ") };
}

function jobLabel(job: UploadJob | null): string {
  if (!job || job.status === "queued") return "QUEUED…";
  if (job.stage === "locating") return "FINDING DEMO…";
  if (job.stage === "downloading") return job.progress != null ? `DOWNLOADING ${Math.round(job.progress * 100)}%` : "DOWNLOADING…";
  if (job.stage === "decompressing") return job.progress != null ? `UNPACKING ${Math.round(job.progress * 100)}%` : "UNPACKING…";
  if (job.stage === "storing") return "SAVING…";
  if (job.stage === "hashing") return "CHECKING…";
  return "PARSING…";
}

function jobHint(job: UploadJob | null): string {
  if (job?.status === "queued" && job.queue_position)
    return `Upload complete. Waiting for ${job.queue_position} other demo${job.queue_position === 1 ? "" : "s"} to finish first.`;
  if (job?.stage === "decompressing")
    return "Unpacking the .bz2 archive on the server. This is the slow part: it can take several minutes. Plain .dem files skip it.";
  return "Upload complete. Parsing the demo and scoring each round in the background. A full match can take a minute or two; you can leave and come back.";
}

/** Sync button label / hint for the queued matches of a sync (one job per match, worked on in order). */
function syncJobsLabel(jobs: UploadJob[]): string {
  const finished = jobs.filter((j) => !isJobActive(j)).length;
  const current = jobs.find((j) => j.status === "processing") ?? jobs.find(isJobActive) ?? null;
  return `MATCH ${Math.min(finished + 1, jobs.length)}/${jobs.length} · ${jobLabel(current)}`;
}

function syncJobsHint(jobs: UploadJob[]): string {
  const current = jobs.find((j) => j.status === "processing");
  const waiting = jobs.find(isJobActive);
  if (!current && waiting?.queue_position)
    return `Matches found. Waiting for ${waiting.queue_position} other demo${waiting.queue_position === 1 ? "" : "s"} on the server to finish first.`;
  if (current?.stage === "downloading" || current?.stage === "locating")
    return "Downloading the match demo from Valve in the background. You can leave and come back.";
  if (current?.stage === "decompressing")
    return "Unpacking Valve’s .bz2 demo on the server. This is the slow part: it can take several minutes per match.";
  return "Parsing each demo and scoring its rounds in the background. A full match can take a few minutes; you can leave and come back.";
}

export function Matches({ me, onMeChange }: { me: Me; onMeChange: () => Promise<unknown> }) {
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncJobs, setSyncJobs] = useState<UploadJob[] | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [listError, setListError] = useState("");

  const loadMatches = useCallback(async () => {
    try {
      setMatches((await steamApi.listMatches(50, 0)).matches);
      setListError("");
    } catch (reason) {
      setListError(reason instanceof ApiError ? reason.message : "Could not load your matches.");
    }
  }, []);

  useEffect(() => {
    void loadMatches();
  }, [loadMatches]);

  const [upload, setUpload] = useState<UploadProgress | null>(null);
  const uploading = upload !== null;

  const finishJob = useCallback(async (job: UploadJob) => {
    if (job.status === "done" && job.match) {
      const rounds = `${job.match.rounds_count} round${job.match.rounds_count === 1 ? "" : "s"}`;
      setNotice({
        tone: "ok",
        text: job.created ? `Demo imported: ${mapLabel(job.match.map_name)}, ${rounds}.` : "That demo was already imported. Opening its report.",
      });
      await loadMatches();
      setSelected(job.match.id);
    } else if (job.status === "failed") {
      setNotice({ tone: "error", text: messageFor(job.error, "The demo could not be imported.") });
    }
  }, [loadMatches]);

  // Parsing runs in the background on the server: after the upload (or after a page reload
  // while a parse is still running) poll the job until it finishes.
  const followJob = useCallback(async (job: UploadJob, signal?: { cancelled: boolean }) => {
    setUpload({ phase: "processing", job });
    try {
      const final = await steamApi.waitForUploadJob(job, (next) => { if (!signal?.cancelled) setUpload({ phase: "processing", job: next }); }, signal);
      if (!signal?.cancelled) await finishJob(final);
    } catch (reason) {
      if (!signal?.cancelled) setNotice({ tone: "error", text: reason instanceof ApiError ? reason.message : "Lost track of the upload. Check your matches below." });
    } finally {
      if (!signal?.cancelled) setUpload(null);
    }
  }, [finishJob]);

  useEffect(() => {
    const signal = { cancelled: false };
    steamApi.listUploadJobs(5)
      .then(({ jobs }) => {
        const active = jobs.find(isJobActive);
        if (active && !signal.cancelled) void followJob(active, signal);
      })
      .catch(() => undefined);  // older API without upload jobs, or not signed in yet
    return () => { signal.cancelled = true; };
  }, [followJob]);

  async function uploadFile(file: File | undefined) {
    if (!file) return;
    setNotice(null);
    if (!/\.dem(\.bz2)?$/i.test(file.name)) {
      setNotice({ tone: "error", text: messageFor("not_a_demo_file") });
      return;
    }
    setUpload({ phase: "uploading", fraction: 0 });
    let job: UploadJob;
    try {
      job = (await steamApi.uploadDemo(file, setUpload)).job;
    } catch (reason) {
      setNotice({ tone: "error", text: reason instanceof ApiError ? reason.message : "Upload failed." });
      setUpload(null);
      return;
    }
    await followJob(job);
  }

  const uploadLabel = !upload
    ? "UPLOAD .DEM"
    : upload.phase === "uploading"
      ? `UPLOADING ${Math.round(upload.fraction * 100)}%`
      : jobLabel(upload.job);

  // Sync only finds new matches; each one downloads + parses as a background job. Follow them
  // (also after a page reload while they run) and summarise when all are finished.
  const onMeChangeRef = useRef(onMeChange);
  onMeChangeRef.current = onMeChange;
  const followSyncJobs = useCallback(async (jobs: UploadJob[], hasMoreAfter: boolean, signal?: { cancelled: boolean }) => {
    setSyncJobs(jobs);
    try {
      const final = await steamApi.waitForSyncJobs(jobs, (next) => { if (!signal?.cancelled) setSyncJobs(next); }, signal);
      if (signal?.cancelled) return;
      setNotice(syncSummary(final, hasMoreAfter));
      await loadMatches();
    } catch (reason) {
      if (!signal?.cancelled) setNotice({ tone: "error", text: reason instanceof ApiError ? reason.message : "Lost track of the sync. Check your matches below." });
    } finally {
      if (!signal?.cancelled) {
        setSyncJobs(null);
        void onMeChangeRef.current();
      }
    }
  }, [loadMatches]);

  useEffect(() => {
    const signal = { cancelled: false };
    steamApi.getSync()
      .then(({ jobs }) => {
        const active = (jobs ?? []).filter(isJobActive).reverse();  // oldest first
        if (active.length && !signal.cancelled) void followSyncJobs(active, false, signal);
      })
      .catch(() => undefined);  // older API, or not signed in yet
    return () => { signal.cancelled = true; };
  }, [followSyncJobs]);

  async function sync() {
    setSyncing(true);
    setNotice(null);
    let result: SyncResult;
    try {
      result = await steamApi.postSync();
    } catch (reason) {
      setNotice({ tone: "error", text: reason instanceof ApiError ? reason.message : "Sync failed." });
      setSyncing(false);
      void onMeChange();
      return;
    }
    setSyncing(false);
    setHasMore(result.has_more);
    setNotice(syncMessage(result));
    if (result.skipped) void loadMatches();
    const jobs = result.jobs ?? [];
    if (jobs.some(isJobActive)) {
      await followSyncJobs(jobs, result.has_more);
    } else {
      if (jobs.length) setNotice(syncSummary(jobs, result.has_more));
      await loadMatches();
      void onMeChange();
    }
  }

  const syncBusy = syncing || syncJobs !== null;
  const syncLabel = syncing ? "SYNCING…" : syncJobs ? syncJobsLabel(syncJobs) : hasMore ? "SYNC MORE MATCHES" : "SYNC MATCHES";

  const linked = me.match_access.linked;
  const lastSync = me.sync.last_finished_at ? new Date(me.sync.last_finished_at).toLocaleString() : null;

  return (
    <div className="steam-card">
      <span className="section-kicker">STEP 3 · IMPORT MATCHES</span>
      <div className="sync-row">
        <button className={`submit-button sync-button ${syncJobs ? "busy" : ""}`} type="button" onClick={sync} disabled={!linked || syncBusy || me.sync.status === "running"}>
          <span>{syncLabel}</span><span className="button-arrow">↻</span>
        </button>
        <label className={`ghost-button upload-button ${uploading ? "busy" : ""}`} aria-disabled={uploading}>
          <span>{uploadLabel}</span>
          <input type="file" accept=".dem,.bz2,application/x-bzip2" disabled={uploading}
            onChange={(event) => { void uploadFile(event.target.files?.[0]); event.target.value = ""; }} />
        </label>
        <span className="steam-muted sync-meta">
          {uploading
            ? upload?.phase === "processing"
              ? jobHint(upload.job)
              : "Uploading your demo…"
            : syncJobs
              ? syncJobsHint(syncJobs)
              : !linked ? "Link your match history above to sync, or upload a CS2 .dem / .dem.bz2 you already have." : syncing ? "Checking Valve’s match history for new matches…" : lastSync ? `Last sync ${lastSync}` : "Not synced yet. You can also upload a CS2 .dem / .dem.bz2."}
        </span>
      </div>
      {upload?.phase === "uploading" && (
        <div className="upload-progress" role="progressbar" aria-label="Demo upload progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(upload.fraction * 100)}>
          <span style={{ width: `${upload.fraction * 100}%` }} />
        </div>
      )}
      {notice && <div className={notice.tone === "ok" ? "steam-notice" : "steam-error"} role="status">{notice.text}</div>}
      {listError && <div className="steam-error" role="alert">{listError}</div>}

      {matches.length === 0 ? (
        <p className="steam-muted">No imported matches yet.</p>
      ) : (
        <ul className="match-list">
          {matches.map((match) => (
            <li key={match.id}>
              <button type="button" className={`match-item ${selected === match.id ? "selected" : ""}`} onClick={() => setSelected(selected === match.id ? null : match.id)}>
                <strong>{mapLabel(match.map_name)}{match.source === "upload" && <span className="source-tag">UPLOADED</span>}</strong>
                <span>{match.status === "imported" ? `${match.rounds_count} rounds` : MATCH_STATUS[match.status_reason ?? ""] ?? "Not imported"}</span>
                <span className="steam-muted">{new Date(match.imported_at).toLocaleDateString()}</span>
              </button>
              {selected === match.id && match.status === "imported" && <MatchReportView matchId={match.id} />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MatchReportView({ matchId }: { matchId: string }) {
  const [report, setReport] = useState<MatchReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    steamApi.getMatch(matchId)
      .then((body) => { if (!cancelled) setReport(body); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof ApiError ? reason.message : "Could not load the report."); });
    return () => { cancelled = true; };
  }, [matchId]);

  if (error) return <div className="steam-error" role="alert">{error}</div>;
  if (!report) return <p className="steam-muted">Loading round report…</p>;

  const { summary } = report;
  return (
    <div className="match-report">
      <div className="calibration-note" role="note">
        <strong>Retrospective estimate, not calibrated for your games.</strong> {report.model.note}
      </div>
      <p className="steam-muted">
        {summary.scored} of {summary.rounds} rounds could be scored. The model’s favourite won {summary.correct_predictions} of {summary.scored}.
      </p>
      <div className="report-legend">
        <span><i className="legend actual" /> Actual winner (from the demo)</span>
        <span><i className="legend model" /> Model estimate (in hindsight)</span>
        <span><i className="legend unscored" /> Unscored</span>
      </div>
      <table className="round-table">
        <thead>
          <tr><th>Round</th><th>Opening kill</th><th>Actual winner</th><th>Model estimate</th></tr>
        </thead>
        <tbody>
          {report.rounds.map((round) => (
            <tr key={round.round_number} className={round.prediction ? "" : "unscored-row"}>
              <td>{round.round_number}</td>
              <td>
                {round.opening_kill
                  ? `${sideName(round.opening_kill.side)} · ${round.opening_kill.weapon ?? "?"} · ${round.opening_kill.seconds != null ? `${round.opening_kill.seconds.toFixed(1)}s` : "?"}`
                  : "—"}
              </td>
              <td><span className={`actual-chip ${round.actual_winner ?? ""}`}>{sideName(round.actual_winner)}</span></td>
              <td>
                {round.prediction ? (
                  <span className="model-estimate">
                    {sideName(round.prediction.predicted_winner)} favoured · CT {(round.prediction.probabilities.ct * 100).toFixed(0)}% / T {(round.prediction.probabilities.t * 100).toFixed(0)}%
                  </span>
                ) : (
                  <span className="unscored-reason">Unscored: {messageFor(round.unscored_reason, "Not scorable.")}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
