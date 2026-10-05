import { useEffect, useState } from "react";
import { steamApi } from "./api";
import { ApiError, messageFor } from "./errors";
import { kdText, resultText } from "./format";
import type { MatchesAnalytics, YouAnalytics } from "./types";
import { weaponName } from "./weapons";

const pct = (rate: number | null | undefined) => (rate == null ? "—" : `${Math.round(rate * 100)}%`);
const mapLabel = (map: string | null) => (map ? map.replace(/^de_/, "").replaceAll("_", " ") : "Unknown map");
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : word.endsWith("ch") ? "es" : "s"}`;

function formLine(form: MatchesAnalytics["recent_form"]): string {
  const { recent, earlier, hit_rate_change: change } = form;
  if (!recent.matches) return "";
  const last = `Last ${plural(recent.matches, "match")}: the model’s favourite won ${pct(recent.hit_rate)} of scored rounds`;
  if (change == null) return `${last}. Import more than ${form.window} matches to see a trend.`;
  const points = Math.round(change * 100);
  const delta = points === 0 ? "no change" : `${points > 0 ? "+" : "−"}${Math.abs(points)} pts`;
  return `${last}, vs ${pct(earlier.hit_rate)} over the ${plural(earlier.matches, "match")} before (${delta}).`;
}

function youFormLine(form: YouAnalytics["recent_form"]): string {
  const { recent, earlier, win_rate_change: change } = form;
  if (!recent.matches) return "";
  const r = recent.results;
  const record = `${r.won}–${r.lost}${r.tied ? `–${r.tied}` : ""}`;
  const last = `Your last ${plural(recent.matches, "match")}: ${record}, ${pct(recent.win_rate)} of rounds won, K/D ${kdText(recent.kd)}`;
  if (change == null) return `${last}.`;
  const points = Math.round(change * 100);
  const delta = points === 0 ? "no change" : `${points > 0 ? "+" : "−"}${Math.abs(points)} pts`;
  return `${last}, vs ${pct(earlier.win_rate)} and K/D ${kdText(earlier.kd)} over the ${plural(earlier.matches, "match")} before (${delta}).`;
}

/** The signed-in player's own numbers (their side each round, from the demo). */
function YouSection({ you }: { you: YouAnalytics }) {
  const left = [
    you.matches_without_you ? `${plural(you.matches_without_you, "demo")} you’re not in (e.g. pro matches)` : "",
    you.matches_unknown ? `${plural(you.matches_unknown, "match")} imported before per-player stats (upload ${you.matches_unknown === 1 ? "it" : "them"} again to include)` : "",
  ].filter(Boolean);
  if (you.matches === 0) {
    return (
      <p className="steam-muted you-empty">
        You (SteamID {you.steam_id}) aren’t in any of these demos yet, so there are no personal stats.{" "}
        {left.length ? `Left out: ${left.join("; ")}. ` : ""}The numbers below cover all players.
      </p>
    );
  }
  const r = you.results;
  const duels = you.opening_duels;
  return (
    <div className="you-section">
      <span className="section-kicker">YOU · YOUR SIDE EACH ROUND</span>
      <dl className="report-header summary-tiles you-tiles" aria-label="Your stats">
        <div>
          <dt>ROUNDS WON</dt><dd>{pct(you.win_rate)}</dd>
          <small>{you.won} of {you.rounds_with_winner} · {plural(you.matches, "match")}: {r.won} W · {r.lost} L{r.tied ? ` · ${r.tied} T` : ""}</small>
        </div>
        <div>
          <dt>AS CT / AS T</dt><dd>{pct(you.sides.ct.win_rate)} / {pct(you.sides.t.win_rate)}</dd>
          <small>CT {you.sides.ct.won}/{you.sides.ct.rounds} · T {you.sides.t.won}/{you.sides.t.rounds} rounds won</small>
        </div>
        <div>
          <dt>K/D</dt><dd>{kdText(you.kd)}</dd>
          <small>{you.kills} K / {you.deaths} D · {you.kills_per_round ?? "—"} per round · survived {pct(you.survival_rate)}</small>
        </div>
        <div>
          <dt>OPENING DUELS</dt><dd>{pct(duels.win_rate)}</dd>
          <small>Won {duels.won} of {duels.taken} · round won {pct(duels.round_win_rate_after_opening_kill)} after your opening kill, {pct(duels.round_win_rate_after_opening_death)} after dying first</small>
        </div>
      </dl>
      {youFormLine(you.recent_form) && <p className="summary-form">{youFormLine(you.recent_form)}</p>}
      <table className="round-table summary-maps" aria-label="Your maps">
        <thead><tr><th>Map</th><th>Matches</th><th>Your rounds</th><th>Won</th><th>As CT</th><th>As T</th><th>K/D</th></tr></thead>
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
            </tr>
          ))}
        </tbody>
      </table>
      {you.recent_form.matches.length > 0 && (
        <table className="round-table" aria-label="Your recent matches">
          <thead><tr><th>Date</th><th>Map</th><th>Result</th><th>Started</th><th>Rounds won</th><th>K / D</th></tr></thead>
          <tbody>
            {you.recent_form.matches.map((m) => (
              <tr key={m.id}>
                <td title={m.date_source === "played" ? "Played (from Valve)" : "Added (no match date in the demo)"}>
                  {m.date_source === "played" ? "" : "Added "}{new Date(m.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                </td>
                <td>{mapLabel(m.map_name)}</td>
                <td className={m.result === "won" ? "you-won" : m.result === "lost" ? "you-lost" : ""}>{resultText(m) ?? "—"}</td>
                <td>{m.first_side === "ct" ? "CT" : "T"}</td>
                <td>{m.won} of {m.rounds}</td>
                <td>{m.kills} / {m.deaths}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {left.length > 0 && <p className="steam-muted summary-note">Not in your stats: {left.join("; ")}.</p>}
    </div>
  );
}

/** Compact analytics across all the user's previous matches (GET /matches/summary), shown above
 * the match list. ``refreshKey`` changes whenever the list is (re)loaded, e.g. after an import. */
export function MatchesSummaryPanel({ refreshKey }: { refreshKey: number }) {
  const [summary, setSummary] = useState<MatchesAnalytics | null>(null);
  const [error, setError] = useState("");
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    if (refreshKey < 1) return;  // wait for the match list: one request per list load
    let cancelled = false;
    steamApi.getMatchesSummary()
      .then((body) => { if (!cancelled) { setSummary(body); setError(""); } })
      .catch((reason) => {
        if (cancelled) return;
        // An older API has no summary route (it answers 404 match_not_found): show nothing.
        if (reason instanceof ApiError && reason.status === 404) setUnsupported(true);
        else setError(reason instanceof ApiError ? reason.message : "");
      });
    return () => { cancelled = true; };
  }, [refreshKey]);

  if (unsupported) return null;
  if (!summary) {
    return error ? <p className="steam-muted">Analytics across your matches couldn’t be loaded. {error}</p> : null;
  }
  const { totals, prediction, sides, opening_kills: opening, maps, recent_form: form } = summary;
  if (totals.matches === 0) return null;  // the list below says there is nothing yet
  if (totals.imported_matches === 0) {
    return <p className="steam-muted">None of your matches could be analysed yet. Upload their demos to see stats across matches.</p>;
  }
  const scored = prediction.scored_rounds > 0;
  const calibration = prediction.calibration.filter((bin) => bin.rounds > 0);
  return (
    <section className="summary-panel" aria-label="Across your matches">
      <span className="section-kicker">ACROSS YOUR MATCHES</span>
      {summary.you && <YouSection you={summary.you} />}
      {summary.you && <span className="section-kicker all-players-kicker">ALL PLAYERS IN THESE DEMOS · MAP SIDES</span>}
      <dl className="report-header summary-tiles" aria-label="Previous matches summary">
        <div>
          <dt>MATCHES</dt><dd>{totals.imported_matches}</dd>
          <small>{plural(totals.rounds, "round")} · {totals.scored_rounds} scored{totals.not_imported_matches ? ` · ${totals.not_imported_matches} not imported` : ""}</small>
        </div>
        <div>
          <dt>MODEL HIT RATE</dt><dd>{pct(prediction.hit_rate)}</dd>
          <small>{scored ? `${prediction.correct} of ${prediction.scored_rounds} rounds · opening-kill side ${pct(prediction.opening_kill_baseline_hit_rate)}` : "No scorable rounds yet"}</small>
        </div>
        <div>
          <dt>BRIER SCORE</dt><dd>{prediction.brier_score == null ? "—" : prediction.brier_score.toFixed(3)}</dd>
          <small>Lower is better · coin flip {summary.model.coin_flip_brier_score.toFixed(2)}</small>
        </div>
        <div>
          <dt>CT / T ROUNDS</dt><dd>{pct(sides.ct_win_rate)} / {pct(sides.t_win_rate)}</dd>
          <small>CT side won {sides.ct_won} of {sides.rounds_with_winner}</small>
        </div>
        <div>
          <dt>OPENING KILL WINS</dt><dd>{pct(opening.conversion_rate)}</dd>
          <small>Round won by the side with the first kill · CT {pct(opening.by_side.ct.conversion_rate)} · T {pct(opening.by_side.t.conversion_rate)}</small>
        </div>
      </dl>
      {formLine(form) && <p className="summary-form">{formLine(form)}</p>}
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
        {summary.unscored_reasons.length > 0 && (
          <p className="steam-muted">
            Unscored rounds: {summary.unscored_reasons.map((r) => `${r.rounds} × ${messageFor(r.reason, r.reason)}`).join(" ")}
          </p>
        )}
      </details>
      <p className="steam-muted summary-note">
        {summary.you
          ? "“You” uses the side your SteamID played each round (halftime swap included). The tiles and tables under “All players” count every round of every match by map side (CT / T), including demos you’re not in."
          : "CT / T are the map sides of everyone in the match; your own team isn’t tracked yet."}{" "}
        Model numbers are a retrospective estimate, not calibrated for matchmaking.
      </p>
    </section>
  );
}
