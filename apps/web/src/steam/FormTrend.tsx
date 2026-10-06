import { useEffect, useState } from "react";
import { steamApi } from "./api";
import { kdText, mapLabel } from "./format";
import type { YouAnalytics } from "./types";

type TrendMatch = YouAnalytics["recent_form"]["matches"][number];

/** How many matches the trend asks for (the API's maximum window). */
export const TREND_MATCHES = 50;
/** Rolling average length for the trend lines. */
const ROLL = 5;


/** Your form over time: round win % and K/D per match (oldest → newest) with a 5-match rolling
 * average. Uses GET /matches/summary?recent=50, whose per-match list is the player's own rounds. */
export function FormTrend({ refreshKey, matchCount }: { refreshKey: number; matchCount: number }) {
  const [matches, setMatches] = useState<TrendMatch[] | null>(null);
  const [failed, setFailed] = useState(false);
  const enough = matchCount >= 3;

  useEffect(() => {
    if (!enough) return;
    let cancelled = false;
    steamApi.getMatchesSummary(TREND_MATCHES)
      .then((body) => { if (!cancelled) setMatches([...(body.you?.recent_form.matches ?? [])].reverse()); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [enough, refreshKey]);

  if (!enough) return null;  // hide until 3 matches — no empty-state essay
  if (failed) return null;
  if (!matches) return <div className="trend-loading" aria-label="Loading your form over time" />;
  const kds = matches.map((m) => (m.deaths ? m.kills / m.deaths : m.kills));
  const wins = matches.map((m) => (m.win_rate == null ? null : m.win_rate * 100));
  return (
    <figure className="form-trend" aria-label="Your form over time">
      <figcaption className="section-kicker">FORM OVER TIME · LAST {matches.length} MATCHES</figcaption>
      <TrendChart title="Rounds won" unit="%" values={wins} matches={matches} min={0} max={100} reference={50} format={(v) => `${Math.round(v)}%`} />
      <TrendChart title="K/D" unit="" values={kds} matches={matches} min={0} max={Math.max(2, Math.ceil(Math.max(...kds)))} reference={1} format={(v) => kdText(v)} />
    </figure>
  );
}

export function rolling(values: (number | null)[], n = ROLL): (number | null)[] {
  return values.map((_, i) => {
    const window = values.slice(Math.max(0, i - n + 1), i + 1).filter((v): v is number => v != null);
    return window.length ? window.reduce((a, b) => a + b, 0) / window.length : null;
  });
}

const VW = 600, VH = 120, PAD_L = 6, PAD_R = 6, PAD_T = 8, PAD_B = 8;

function TrendChart({ title, values, matches, min, max, reference, format }: {
  title: string; unit: string; values: (number | null)[]; matches: TrendMatch[]; min: number; max: number; reference: number;
  format: (v: number) => string;
}) {
  const n = values.length;
  const x = (i: number) => PAD_L + (n === 1 ? (VW - PAD_L - PAD_R) / 2 : (i * (VW - PAD_L - PAD_R)) / (n - 1));
  const y = (v: number) => PAD_T + (1 - (Math.min(Math.max(v, min), max) - min) / (max - min)) * (VH - PAD_T - PAD_B);
  const avg = rolling(values);
  const path = avg.map((v, i) => (v == null ? "" : `${i && avg[i - 1] != null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)).join("");
  const known = avg.filter((v): v is number => v != null);
  const latest = known.length ? known[known.length - 1] : undefined;
  const first = avg.find((v): v is number => v != null);
  return (
    <div className="trend-chart">
      <div className="trend-head">
        <strong>{title}</strong>
        <span className="steam-muted">
          {latest == null ? "—" : `${format(latest)} now`}
          {latest != null && first != null && n >= ROLL * 2 ? ` · ${format(first)} at the start` : ""}
        </span>
      </div>
      <div className="trend-plot">
      <span className="trend-axis top" aria-hidden="true">{format(max)}</span>
      <span className="trend-axis bottom" aria-hidden="true">{format(min)}</span>
      <svg viewBox={`0 0 ${VW} ${VH}`} preserveAspectRatio="none" role="img"
        aria-label={`${title} per match, oldest to newest: ${values.map((v) => (v == null ? "no data" : format(v))).join(", ")}`}>
        <line className="trend-ref" x1={PAD_L} x2={VW - PAD_R} y1={y(reference)} y2={y(reference)} />
        {values.map((v, i) => v == null ? null : (
          // zero-length line + round cap + non-scaling stroke: a round dot even though the chart stretches
          <path key={matches[i].id} className={`trend-dot ${matches[i].result ?? ""}`} d={`M${x(i).toFixed(1)},${y(v).toFixed(1)}h0`}>
            <title>{`${mapLabel(matches[i].map_name)} · ${new Date(matches[i].date).toLocaleDateString()} · ${format(v)}`}</title>
          </path>
        ))}
        <path className="trend-line" d={path} />
      </svg>
      </div>
    </div>
  );
}
