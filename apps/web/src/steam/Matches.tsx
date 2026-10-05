import { useCallback, useEffect, useState } from "react";
import { steamApi } from "./api";
import { ApiError, messageFor } from "./errors";
import type { MatchReport, MatchSummary, Me, SyncResult } from "./types";

const MATCH_STATUS: Record<string, string> = {
  demo_unavailable: "Demo is no longer available from Valve.",
  demo_too_large: "Demo exceeded the server’s size limit.",
  parser_error: "Demo could not be parsed.",
};

const sideName = (side: string | null | undefined) => (side === "ct" ? "CT" : side === "t" ? "T" : "—");
const mapLabel = (map: string | null) => (map ? map.replace(/^de_/, "").replaceAll("_", " ") : "Unknown map");

function syncMessage(result: SyncResult): { tone: "ok" | "error"; text: string } {
  const counts = `Imported ${result.imported} new match${result.imported === 1 ? "" : "es"}.`;
  if (result.status === "up_to_date") return { tone: "ok", text: `${counts} You’re up to date.` };
  if (result.status === "partial") return { tone: "ok", text: `${counts} More matches are waiting. Sync again to continue.` };
  if (result.status === "demo_not_ready")
    return { tone: "ok", text: `${counts} The next demo isn’t ready on Valve’s servers yet. Try again later.` };
  const code = result.error === "invalid_auth_code" ? "invalid_auth_code_status" : result.error;
  return { tone: "error", text: `${result.imported ? counts + " " : ""}${messageFor(code, "Sync failed.")}` };
}

export function Matches({ me, onMeChange }: { me: Me; onMeChange: () => Promise<unknown> }) {
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
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

  async function sync() {
    setSyncing(true);
    setNotice(null);
    try {
      const result = await steamApi.postSync();
      setNotice(syncMessage(result));
      setHasMore(result.has_more);
      await loadMatches();
    } catch (reason) {
      setNotice({ tone: "error", text: reason instanceof ApiError ? reason.message : "Sync failed." });
    } finally {
      setSyncing(false);
      void onMeChange();
    }
  }

  const linked = me.match_access.linked;
  const lastSync = me.sync.last_finished_at ? new Date(me.sync.last_finished_at).toLocaleString() : null;

  return (
    <div className="steam-card">
      <span className="section-kicker">STEP 3 · IMPORT MATCHES</span>
      <div className="sync-row">
        <button className="submit-button sync-button" type="button" onClick={sync} disabled={!linked || syncing || me.sync.status === "running"}>
          <span>{syncing ? "SYNCING…" : hasMore ? "SYNC MORE MATCHES" : "SYNC MATCHES"}</span><span className="button-arrow">↻</span>
        </button>
        <span className="steam-muted sync-meta">
          {!linked ? "Link your match history above to enable sync." : syncing ? "Fetching the next match from Valve, downloading and parsing the demo. This can take a minute." : lastSync ? `Last sync ${lastSync}` : "Not synced yet."}
        </span>
      </div>
      {notice && <div className={notice.tone === "ok" ? "steam-notice" : "steam-error"} role="status">{notice.text}</div>}
      {listError && <div className="steam-error" role="alert">{listError}</div>}

      {matches.length === 0 ? (
        <p className="steam-muted">No imported matches yet.</p>
      ) : (
        <ul className="match-list">
          {matches.map((match) => (
            <li key={match.id}>
              <button type="button" className={`match-item ${selected === match.id ? "selected" : ""}`} onClick={() => setSelected(selected === match.id ? null : match.id)}>
                <strong>{mapLabel(match.map_name)}</strong>
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
