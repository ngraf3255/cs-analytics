import { useEffect, useState } from "react";
import { steamApi } from "./api";
import { ApiError } from "./errors";
import { kdText } from "./format";
import { FormTrend } from "./FormTrend";
import { ShareButton } from "./ShareButton";
import { profileCard } from "./shareCard";
import { PeerCompare } from "./PeerCompare";
import { RoleBreakdown } from "./RoleBreakdown";
import { TableauExport } from "./TableauExport";
import type { MatchesAnalytics, YouAnalytics } from "./types";
import { weaponName } from "./weapons";

const pct = (rate: number | null | undefined) => (rate == null ? "—" : `${Math.round(rate * 100)}%`);
const mapLabel = (map: string | null) => (map ? map.replace(/^de_/, "").replaceAll("_", " ") : "Unknown map");
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : word.endsWith("ch") ? "es" : "s"}`;



/** The signed-in player's own numbers (their side each round, from the demo). */
function YouSection({ you, refreshKey }: { you: YouAnalytics; refreshKey: number }) {
  if (you.matches === 0) return null;
  const duels = you.opening_duels;
  const hasDuels = you.maps.some((m) => m.opening_attempt_rate !== undefined);
  return (
    <div className="you-section">
      <span className="section-kicker">YOU · YOUR SIDE EACH ROUND</span>
      <dl className="report-header summary-tiles you-tiles" aria-label="Your stats">
        <div>
          <dt>ROUNDS WON</dt><dd>{pct(you.win_rate)}</dd>
        </div>
        <div>
          <dt>AS CT / AS T</dt><dd>{pct(you.sides.ct.win_rate)} / {pct(you.sides.t.win_rate)}</dd>
        </div>
        <div>
          <dt>K/D</dt><dd>{kdText(you.kd)}</dd>
        </div>
        <div>
          <dt>OPENING DUELS</dt><dd>{pct(duels.win_rate)}</dd>
        </div>
      </dl>
      <div className="report-actions"><ShareButton card={() => profileCard(you)} label="Share my numbers" /></div>
      <FormTrend refreshKey={refreshKey} matchCount={you.matches} />
      {you.matches > 0 && <PeerCompare refreshKey={refreshKey} />}
      {you.roles && <RoleBreakdown roles={you.roles} />}
      <table className="round-table summary-maps" aria-label="Your maps">
        <thead><tr><th>Map</th><th>Matches</th><th>Your rounds</th><th>Won</th><th>As CT</th><th>As T</th><th>K/D</th>{hasDuels && <th>Opening duels</th>}</tr></thead>
        <tbody>
          {you.maps.map((map) => (
            <tr key={map.map_name ?? "unknown"}>
              <td>{mapLabel(map.map_name)}</td>
              <td>{map.matches} ({map.results.won}–{map.results.lost}{map.results.tied ? `–${map.results.tied}` : ""})</td>
              <td>{map.rounds}</td>
              <td>{pct(map.win_rate)}</td>
              <td>{map.sides.ct.rounds ? `${pct(map.sides.ct.win_rate)} (${map.sides.ct.won}/${map.sides.ct.rounds})` : "—"}</td>
              <td>{map.sides.t.rounds ? `${pct(map.sides.t.win_rate)} (${map.sides.t.won}/${map.sides.t.rounds})` : "—"}</td>
              <td>{kdText(map.kd)}</td>
              {hasDuels && (
                <td>{(map.opening_kills ?? 0) + (map.opening_deaths ?? 0)
                  ? `${map.opening_kills}–${map.opening_deaths} · in ${pct(map.opening_attempt_rate)} of rounds`
                  : "—"}</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Compact analytics across all the user's previous matches (GET /matches/summary), shown above
 * the match list. ``refreshKey`` changes whenever the list is (re)loaded, e.g. after an import. */
/** Cover the match list window so collapsed rows can join W/L · you–them · K-D. */
const LIST_RECENT = 50;

type MatchesSummaryPanelProps = {
  refreshKey: number;
  /** Personal recent_form rows for MatchList collapsed chips (id → bits). */
  onPersonalMatches?: (matches: YouAnalytics["recent_form"]["matches"]) => void;
};

export function MatchesSummaryPanel({ refreshKey, onPersonalMatches }: MatchesSummaryPanelProps) {
  const [summary, setSummary] = useState<MatchesAnalytics | null>(null);
  const [error, setError] = useState("");
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    if (refreshKey < 1) return;  // wait for the match list: one request per list load
    let cancelled = false;
    steamApi.getMatchesSummary(LIST_RECENT)
      .then((body) => {
        if (cancelled) return;
        setSummary(body);
        setError("");
        onPersonalMatches?.(body.you?.recent_form.matches ?? []);
      })
      .catch((reason) => {
        if (cancelled) return;
        onPersonalMatches?.([]);
        // An older API has no summary route (it answers 404 match_not_found): show nothing.
        if (reason instanceof ApiError && reason.status === 404) setUnsupported(true);
        else setError(reason instanceof ApiError ? reason.message : "");
      });
    return () => { cancelled = true; };
  }, [refreshKey, onPersonalMatches]);

  if (unsupported) return null;
  if (!summary) {
    return error ? <p className="steam-muted">Analytics across your matches couldn’t be loaded. {error}</p> : null;
  }
  const { totals, prediction, sides, opening_kills: opening, maps } = summary;
  if (totals.matches === 0) return null;  // the list below says there is nothing yet
  if (totals.imported_matches === 0) {
    return <p className="steam-muted">None of your matches could be analysed yet. Upload their demos to see stats across matches.</p>;
  }
  const calibration = prediction.calibration.filter((bin) => bin.rounds > 0);
  return (
    <section className="summary-panel" aria-label="Across your matches">
      <span className="section-kicker">ACROSS YOUR MATCHES</span>
      {summary.you && <YouSection you={summary.you} refreshKey={refreshKey} />}
      {summary.you && <span className="section-kicker all-players-kicker">ALL PLAYERS IN THESE DEMOS · MAP SIDES</span>}
      <dl className="report-header summary-tiles" aria-label="Previous matches summary">
        <div>
          <dt>MATCHES</dt><dd>{totals.imported_matches}</dd>
        </div>
        <div>
          <dt>MODEL HIT RATE</dt><dd>{pct(prediction.hit_rate)}</dd>
        </div>
        <div>
          <dt>BRIER SCORE</dt><dd>{prediction.brier_score == null ? "—" : prediction.brier_score.toFixed(3)}</dd>
        </div>
        <div>
          <dt>CT / T ROUNDS</dt><dd>{pct(sides.ct_win_rate)} / {pct(sides.t_win_rate)}</dd>
        </div>
        <div>
          <dt>OPENING KILL WINS</dt><dd>{pct(opening.conversion_rate)}</dd>
        </div>
      </dl>
      {totals.outdated_matches ? (
        <p className="steam-muted outdated-summary" role="note">
          {plural(totals.outdated_matches, "match")} marked OUTDATED — re-upload {totals.outdated_matches === 1 ? "that demo" : "those demos"}.
        </p>
      ) : null}
      <table className="round-table summary-maps" aria-label="By map">
        <thead><tr><th>Map</th><th>Matches</th><th>Rounds</th><th>CT side won</th><th>Model hit rate</th><th>Brier</th></tr></thead>
        <tbody>
          {maps.map((map) => (
            <tr key={map.map_name ?? "unknown"}>
              <td>{mapLabel(map.map_name)}</td>
              <td>{map.matches}</td>
              <td>{map.rounds}</td>
              <td>{pct(map.ct_win_rate)}</td>
              <td>{map.scored_rounds ? `${pct(map.hit_rate)} (${map.correct}/${map.scored_rounds})` : "Not scored"}</td>
              <td>{map.brier_score == null ? "—" : map.brier_score.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details className="summary-more">
        <summary>Model calibration and opening weapons</summary>
        {calibration.length > 0 && (
          <table className="round-table" aria-label="Model calibration">
            <thead><tr><th>Model confidence</th><th>Rounds</th><th>Average confidence</th><th>Favourite won</th></tr></thead>
            <tbody>
              {calibration.map((bin) => (
                <tr key={bin.min}>
                  <td>{Math.round(bin.min * 100)}–{Math.round(bin.max * 100)}%</td>
                  <td>{bin.rounds}</td>
                  <td>{pct(bin.mean_confidence)}</td>
                  <td>{pct(bin.hit_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {opening.top_weapons.length > 0 && (
          <table className="round-table" aria-label="Opening weapons">
            <thead><tr><th>Opening kill weapon</th><th>Rounds</th><th>Side with the kill won</th></tr></thead>
            <tbody>
              {opening.top_weapons.map((w) => (
                <tr key={w.weapon}><td>{weaponName(w.weapon)}</td><td>{w.rounds}</td><td>{pct(w.conversion_rate)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
      <TableauExport matches={totals.imported_matches} rounds={totals.rounds} personalMatches={summary.you?.matches} />
    </section>
  );
}
