import { useCallback, useEffect, useRef, useState } from "react";
import { isJobActive, steamApi, type UploadProgress } from "./api";
import { ApiError, messageFor } from "./errors";
import type { MatchReport, MatchSummary, Me, RoundReport, SyncResult, UploadJob, YouInMatch } from "./types";
import { kdText, matchDate, outdatedText, resultText } from "./format";
import { MatchesSummaryPanel } from "./MatchesSummary";
import { weaponName } from "./weapons";

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
  const attached = result.attached ?? 0;
  const skipped = (attached ? ` Imported ${plural(attached, "new match")} (already analysed on the server, no download needed).` : "")
    + (result.skipped ? ` ${plural(result.skipped, "match")} you already had ${result.skipped === 1 ? "was" : "were"} skipped.` : "");
  if (result.status === "error") {
    return { tone: "error", text: `${messageFor(syncErrorCode(result.error), "Sync failed.")}${result.jobs.length ? " Matches found before the error are still being imported." : ""}` };
  }
  if (result.jobs.length) return null;  // the job progress tells the rest
  if (result.status === "queue_full") return { tone: "ok", text: `${messageFor("sync_queue_full")}${skipped}` };
  if (result.status === "partial") return { tone: "ok", text: `No new demos to download.${skipped} More matches are waiting. Sync again to continue.` };
  return { tone: "ok", text: attached ? `${skipped.trim()} You’re up to date.` : `You’re up to date.${skipped}` };
}

/** How the POST /steam/sync request itself ended; null when following jobs after a reload (unknown).
 * ``attached``: new matches it added to the list without a job (already analysed on the server). */
type SyncOutcome = { hasMore: boolean; error: string | null; attached?: number } | null;

const syncErrorCode = (error: string | null) => (error === "invalid_auth_code" ? "invalid_auth_code_status" : error);

/** Summary once every job of a sync has finished. */
function syncSummary(jobs: UploadJob[], outcome: SyncOutcome): { tone: "ok" | "error"; text: string } {
  const imported = jobs.filter((j) => j.status === "done" && j.match?.status === "imported" && j.created).length + (outcome?.attached ?? 0);
  const known = jobs.filter((j) => j.status === "done" && j.match?.status === "imported" && !j.created).length;
  const missing = jobs.filter((j) => j.status === "done" && j.match && j.match.status !== "imported").length;
  const failed = jobs.filter((j) => j.status === "failed");
  const parts: string[] = [];
  // "Imported 0 new matches." only adds noise when something else explains the outcome.
  if (imported || !(known || missing || failed.length)) parts.push(`Imported ${plural(imported, "new match")}.`);
  if (known) parts.push(`${plural(known, "match")} ${known === 1 ? "was" : "were"} already in your list.`);
  if (missing) parts.push(`${plural(missing, "demo")} couldn’t be imported (see the list below; you can upload ${missing === 1 ? "it" : "them"} by hand).`);
  if (failed.length) parts.push(failed.length === 1 ? messageFor(failed[0].error, "One match failed.") : `${failed.length} matches failed: ${messageFor(failed[0].error, "try syncing again.")}`);
  // The history walk itself stopped early: say why rather than "up to date".
  if (outcome?.error) parts.push(messageFor(syncErrorCode(outcome.error), "The sync stopped early. Sync again to continue."));
  else if (outcome?.hasMore) { if (!failed.length) parts.push("More matches are waiting. Sync again to continue."); }
  else if (outcome && !missing && !failed.length) parts.push("You’re up to date.");
  return { tone: (failed.length || outcome?.error) && !imported ? "error" : "ok", text: parts.join(" ") };
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

/** How often the page checks for matches the server's automatic sync found. */
export const AUTO_SYNC_WATCH_MS = 60_000;

type MatchesProps = {
  me: Me;
  onMeChange: () => Promise<unknown>;
  /** Steam sync is usable for this account (server has Steam on and it isn't a guest). */
  canSync?: boolean;
  /** The server has Steam sign-in on (a guest could still sign in). */
  steamAvailable?: boolean;
  /** A demo picked before the session existed (guest start): uploaded once on mount. */
  initialFile?: File | null;
  onInitialFile?: () => void;
};

export function Matches({ me, onMeChange, canSync = true, steamAvailable = true, initialFile = null, onInitialFile }: MatchesProps) {
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncJobs, setSyncJobs] = useState<UploadJob[] | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [listError, setListError] = useState("");
  const [listVersion, setListVersion] = useState(0);  // bumped per list load: refreshes the summary panel

  const loadMatches = useCallback(async () => {
    try {
      setMatches((await steamApi.listMatches(50, 0)).matches);
      setListVersion((n) => n + 1);
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
        text: job.created
          ? `Demo imported: ${mapLabel(job.match.map_name)}, ${rounds}.`
          : job.updated
            ? `Match updated from the demo: ${mapLabel(job.match.map_name)}, ${rounds}.`
            : "That demo is already in your matches. Opening its report.",
      });
      await loadMatches();
      setSelected(job.match.id);
    } else if (job.status === "failed") {
      // An error code this build doesn't know (newer server): show it rather than a bare "failed".
      setNotice({ tone: "error", text: messageFor(job.error, job.error ? `The demo could not be imported (${job.error}).` : "The demo could not be imported.") });
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

  // Guest start: the user already picked a demo before the session existed.
  const uploadRef = useRef(uploadFile);
  uploadRef.current = uploadFile;
  useEffect(() => {
    if (!initialFile) return;
    onInitialFile?.();
    void uploadRef.current(initialFile);
  }, [initialFile, onInitialFile]);

  const uploadLabel = !upload
    ? "UPLOAD .DEM"
    : upload.phase === "uploading"
      ? `UPLOADING ${Math.round(upload.fraction * 100)}%`
      : jobLabel(upload.job);

  // Sync only finds new matches; each one downloads + parses as a background job. Follow them
  // (also after a page reload while they run) and summarise when all are finished.
  const onMeChangeRef = useRef(onMeChange);
  onMeChangeRef.current = onMeChange;
  const followSyncJobs = useCallback(async (jobs: UploadJob[], outcome: SyncOutcome, signal?: { cancelled: boolean }) => {
    setSyncJobs(jobs);
    try {
      const final = await steamApi.waitForSyncJobs(jobs, (next) => { if (!signal?.cancelled) setSyncJobs(next); }, signal);
      if (signal?.cancelled) return;
      setNotice(syncSummary(final, outcome));
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
    if (!canSync) return;
    const signal = { cancelled: false };
    steamApi.getSync()
      .then(({ jobs }) => {
        const active = (jobs ?? []).filter(isJobActive).reverse();  // oldest first
        if (active.length && !signal.cancelled) void followSyncJobs(active, null, signal);
      })
      .catch(() => undefined);  // older API, or not signed in yet
    return () => { signal.cancelled = true; };
  }, [followSyncJobs, canSync]);

  // Automatic sync runs on the server: while it is on, check every minute (tab visible, nothing
  // followed already) whether it queued matches (follow them like a Sync) or imported some.
  const following = useRef(false);
  following.current = syncing || syncJobs !== null;
  const lastSynced = useRef(me.sync.last_synced_at ?? null);
  lastSynced.current = me.sync.last_synced_at ?? null;
  const autoActive = canSync && !!me.sync.auto_sync?.active;
  useEffect(() => {
    if (!autoActive) return;
    const signal = { cancelled: false };
    const timer = setInterval(() => {
      if (following.current || document.visibilityState === "hidden") return;
      steamApi.getSync()
        .then(async (state) => {
          if (signal.cancelled || following.current) return;
          const active = (state.jobs ?? []).filter(isJobActive).reverse();  // oldest first
          if (active.length) void followSyncJobs(active, null, signal);
          else if ((state.last_synced_at ?? null) !== lastSynced.current) {
            lastSynced.current = state.last_synced_at ?? null;
            await loadMatches();
            void onMeChangeRef.current();
          }
        })
        .catch(() => undefined);  // e.g. a free instance waking up: try again next minute
    }, AUTO_SYNC_WATCH_MS);
    return () => { signal.cancelled = true; clearInterval(timer); };
  }, [autoActive, followSyncJobs, loadMatches]);

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
    if (result.skipped || result.attached) void loadMatches();
    const jobs = result.jobs ?? [];
    const outcome = { hasMore: result.has_more, error: result.status === "error" ? result.error : null, attached: result.attached ?? 0 };
    if (jobs.some(isJobActive)) {
      await followSyncJobs(jobs, outcome);
    } else {
      if (jobs.length) setNotice(syncSummary(jobs, outcome));
      await loadMatches();
      void onMeChange();
    }
  }

  const syncBusy = syncing || syncJobs !== null;
  const syncLabel = syncing ? "SYNCING…" : syncJobs ? syncJobsLabel(syncJobs) : hasMore ? "SYNC MORE MATCHES" : "SYNC MATCHES";

  const linked = me.match_access.linked;
  const relink = linked ? me.match_access.needs_relink ?? null : null;
  // Auth code saved, no share code yet: nothing to sync from until they add one after a match.
  const awaitingShare = linked && !relink && !!me.match_access.awaiting_share_code;
  const lastSync = me.sync.last_finished_at ? new Date(me.sync.last_finished_at).toLocaleString() : null;

  return (
    <div className="steam-card">
      <span className="section-kicker">{canSync ? "IMPORT MATCHES" : "YOUR UPLOADS"}</span>
      <div className="sync-row">
        {canSync && (
          <button className={`submit-button sync-button ${syncJobs ? "busy" : ""}`} type="button" onClick={sync} disabled={!linked || !!relink || awaitingShare || syncBusy || me.sync.status === "running"}>
            <span>{syncLabel}</span><span className="button-arrow">↻</span>
          </button>
        )}
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
              : !canSync ? `Upload a CS2 .dem / .dem.bz2 to get a round-by-round report.${steamAvailable ? " Sign in through Steam to sync matches automatically." : " Steam sync is coming soon."}`
              : !linked ? "Link your match history above to sync (optional), or upload a CS2 .dem / .dem.bz2 you already have."
                : awaitingShare ? "Sync starts once you add a match share code in Account settings. Uploads work now."
                : relink ? (relink.field === "auth_code" ? "Sync is paused: update your Game Authentication Code in Account settings." : "Sync is paused: add a recent share code in Account settings.")
                : syncing ? "Checking Valve’s match history for new matches…" : lastSync ? `Last sync ${lastSync}${me.sync.auto_sync?.active ? " · auto-sync on" : ""}` : "Not synced yet. You can also upload a CS2 .dem / .dem.bz2."}
        </span>
      </div>
      {upload?.phase === "uploading" && (
        <div className="upload-progress" role="progressbar" aria-label="Demo upload progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(upload.fraction * 100)}>
          <span style={{ width: `${upload.fraction * 100}%` }} />
        </div>
      )}
      {notice && <div className={notice.tone === "ok" ? "steam-notice" : "steam-error"} role="status">{notice.text}</div>}
      {listError && <div className="steam-error" role="alert">{listError}</div>}
      <MatchesSummaryPanel refreshKey={listVersion} />

      {matches.length === 0 ? (
        <EmptyMatches linked={linked} relink={!!relink} awaitingShare={awaitingShare} uploading={uploading} canSync={canSync} steamAvailable={steamAvailable}
          onFile={(file) => void uploadFile(file)} />
      ) : (
        <ul className="match-list">
          {matches.map((match) => (
            <li key={match.id}>
              <button type="button" className={`match-item ${selected === match.id ? "selected" : ""}`} onClick={() => setSelected(selected === match.id ? null : match.id)}>
                <strong>
                  {mapLabel(match.map_name)}{match.source === "upload" && <span className="source-tag">UPLOADED</span>}
                  {match.outdated && <span className="source-tag outdated-tag" title={outdatedText(match) ?? undefined}>RE-UPLOAD TO UPDATE</span>}
                </strong>
                <span>{match.status === "imported" ? `${match.rounds_count} rounds` : MATCH_STATUS[match.status_reason ?? ""] ?? "Not imported"}</span>
                <span className="steam-muted" title={matchDate(match).label}>
                  {matchDate(match).played ? matchDate(match).day : `Added ${matchDate(match).day}`}
                </span>
              </button>
              {selected === match.id && match.status === "imported" && <MatchReportView matchId={match.id} />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** No matches yet: both ways in, side by side (Leetify-style onboarding: sync forward, upload the rest). */
type EmptyMatchesProps = {
  linked: boolean; relink: boolean; awaitingShare: boolean; uploading: boolean; canSync: boolean; steamAvailable: boolean;
  onFile: (file: File | undefined) => void;
};

function EmptyMatches({ linked, relink, awaitingShare, uploading, canSync, steamAvailable, onFile }: EmptyMatchesProps) {
  return (
    <div className="empty-matches" aria-label="No matches yet">
      <p className="steam-muted">No imported matches yet.</p>
      <div className="empty-options">
        <div className={`empty-option ${canSync ? "" : "soft-disabled"}`}>
          <span className="section-kicker">{canSync || steamAvailable ? "SYNC FROM STEAM" : "SYNC FROM STEAM · COMING SOON"}</span>
          <p>
            {!canSync
              ? steamAvailable
                ? "Sign in through Steam above to import your Competitive, Premier and Wingman matches automatically."
                : "Automatic import from your Steam match history is coming soon. Uploading demos works now."
              : !linked
              ? "Optional: link your match history above, then press Sync matches."
              : relink
                ? "Sync is paused until you update your code in Account settings."
                : awaitingShare
                  ? "Your authentication code is saved. After your next match, add its share code in Account settings."
                : "Press Sync matches above. We import the match you linked with and your newer Competitive, Premier and Wingman matches; each one downloads and parses in the background."}
          </p>
        </div>
        <div className="empty-option">
          <span className="section-kicker">UPLOAD A DEMO</span>
          <p>
            Any CS2 <code>.dem</code> or <code>.dem.bz2</code>: matches older than 30 days, FACEIT or pro demos. Replays you
            download in CS2 (<em>Watch → Your Matches</em>) are saved in the game folder under <code>game/csgo/replays</code>.
          </p>
          <label className={`ghost-button empty-upload ${uploading ? "busy" : ""}`} aria-disabled={uploading}>
            <span>CHOOSE A DEMO FILE</span>
            <input type="file" accept=".dem,.bz2,application/x-bzip2" disabled={uploading} aria-label="Choose a demo file to upload"
              onChange={(event) => { onFile(event.target.files?.[0]); event.target.value = ""; }} />
          </label>
        </div>
      </div>
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

  const { summary, match } = report;
  const score = match.score ?? null;
  const date = matchDate(match);
  const you = report.you;
  const inMatch = you?.status === "in_match";
  return (
    <div className="match-report">
      <dl className={`report-header ${you ? "with-you" : ""}`} aria-label="Match summary">
        <div><dt>MAP</dt><dd>{mapLabel(match.map_name)}</dd><small>{plural(match.rounds_count, "round")}</small></div>
        <div>
          <dt>SCORE</dt>
          <dd>{score ? `${Math.max(score.ct, score.t)} – ${Math.min(score.ct, score.t)}` : "—"}</dd>
          <small>{score ? `CT ${score.ct} · T ${score.t} (sides at the end)` : "Not recorded in this demo"}</small>
        </div>
        <div><dt>DATE</dt><dd>{date.day}</dd><small>{date.label}</small></div>
        <div><dt>SOURCE</dt><dd>{match.source === "upload" ? "Upload" : "Steam sync"}</dd><small>{match.source === "upload" ? "You uploaded the demo" : "From your match history"}</small></div>
        {you && <YouTile you={you} />}
      </dl>
      {outdatedText(match) && <div className="outdated-note" role="note">{outdatedText(match)}</div>}
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
        {inMatch && <span><i className="legend your-win" /> Your team won the round</span>}
      </div>
      <table className="round-table" aria-label="Rounds">
        <thead>
          <tr><th>Round</th>{inMatch && <th>You</th>}<th>Opening kill</th><th>Actual winner</th><th>Model estimate</th></tr>
        </thead>
        <tbody>
          {report.rounds.map((round) => (
            <tr key={round.round_number} className={[round.prediction ? "" : "unscored-row", yourRowClass(round)].filter(Boolean).join(" ")}>
              <td>{round.round_number}</td>
              {inMatch && <td className="you-cell">{yourRound(round)}</td>}
              <td>
                {round.opening_kill
                  ? `${sideName(round.opening_kill.side)} · ${weaponName(round.opening_kill.weapon)} · ${round.opening_kill.seconds != null ? `${round.opening_kill.seconds.toFixed(1)}s` : "?"}`
                  : "—"}
                {round.you?.opening_kill && <span className="you-tag">YOUR KILL</span>}
                {round.you?.opening_death && <span className="you-tag lost">YOU DIED FIRST</span>}
              </td>
              <td><span className={`actual-chip ${round.actual_winner ?? ""}`}>{sideName(round.actual_winner)}</span></td>
              <td>
                {round.prediction ? (
                  <span className="model-estimate">
                    {sideName(round.prediction.predicted_winner)} favoured · CT {(round.prediction.probabilities.ct * 100).toFixed(0)}% / T {(round.prediction.probabilities.t * 100).toFixed(0)}%
                    {round.you?.win_probability != null && ` · your team ${(round.you.win_probability * 100).toFixed(0)}%`}
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

function yourRowClass(round: RoundReport): string {
  if (!round.you || round.you.won == null) return "";
  return round.you.won ? "your-win" : "your-loss";
}

function yourRound(round: RoundReport) {
  const you = round.you;
  if (!you) return <span className="steam-muted">Not tracked</span>;
  const stats = `${plural(you.kills, "kill")}${you.survived ? " · survived" : ""}`;
  return (
    <>
      <span className={`side-chip ${you.side}`}>{sideName(you.side)}</span>
      {you.won != null && <strong className={you.won ? "you-won" : "you-lost"}>{you.won ? "WON" : "LOST"}</strong>}
      <small>{stats}</small>
    </>
  );
}

/** The signed-in player's line in the report header. */
function YouTile({ you }: { you: YouInMatch }) {
  if (you.status !== "in_match") {
    return (
      <div className="you-tile muted">
        <dt>YOU</dt>
        <dd>{you.status === "not_in_match" ? "Not in this demo" : "Not tracked"}</dd>
        <small>{you.status === "not_in_match"
          ? "Your SteamID isn’t in this match: the stats cover all players."
          : "Imported before per-player stats. Upload the demo again to add yours."}</small>
      </div>
    );
  }
  const side = you.first_side === you.last_side ? `${sideName(you.first_side)} all match` : `Started ${sideName(you.first_side)}, then ${sideName(you.last_side)}`;
  return (
    <div className="you-tile">
      <dt>YOU</dt>
      <dd>{resultText(you) ?? `${you.won} of ${you.rounds} rounds won`}</dd>
      <small>{side} · {you.kills} K / {you.deaths} D (K/D {kdText(you.kd)}) · won {you.won} of {plural(you.rounds, "round")}</small>
    </div>
  );
}
