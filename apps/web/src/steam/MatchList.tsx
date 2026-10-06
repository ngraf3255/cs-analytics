import type { ReactNode } from "react";
import type { MatchResult, MatchSummary } from "./types";
import { mapLabel, matchDate, outdatedText, scoreLine } from "./format";

/** One-line stub note per status_reason (the row itself only says a glyph). */
const STUB_NOTE: Record<string, string> = {
  demo_unavailable: "Demo gone from Valve — upload the .dem above if you have it.",
  demo_too_large: "Demo too large for the server — try uploading the .dem above.",
  parser_error: "Demo couldn’t be parsed — try uploading the .dem above.",
  demo_has_no_rounds: "Demo has no completed rounds — nothing to score.",
};
const STUB_DEFAULT = "Not imported — upload the .dem above if you have it.";

/** Personal bits for a collapsed row (from summary you.recent_form).
 * Bold score uses your rounds won–lost (``won`` / ``rounds``), not remapped CT–T ``score``. */
export type YouMatchBits = MatchResult & { kills: number; deaths: number; rounds: number; won: number };

const DAY_MS = 86_400_000;

function calendarDay(iso: string): { key: string; label: string; start: number } {
  const d = new Date(iso);
  const start = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const key = `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
  const today = new Date();
  const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const diff = todayStart - start;
  const label =
    diff === 0 ? "Today"
    : diff === DAY_MS ? "Yesterday"
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: d.getFullYear() !== today.getFullYear() ? "numeric" : undefined });
  return { key, label, start };
}

function groupMatches(matches: MatchSummary[]): { key: string; label: string; matches: MatchSummary[] }[] {
  // Always day-group (even a 1-match list) so a header like Today / Oct 6 sits above the row.
  const groups: { key: string; label: string; matches: MatchSummary[]; start: number }[] = [];
  for (const match of matches) {
    const date = matchDate(match);
    const iso = (date.played ? match.date : undefined) ?? match.imported_at;
    const { key, label, start } = calendarDay(iso);
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.matches.push(match);
    else groups.push({ key, label, matches: [match], start });
  }
  return groups.map(({ key, label, matches: ms }) => ({ key, label, matches: ms }));
}

function MatchRow({
  match,
  you,
  selected,
  onToggle,
}: {
  match: MatchSummary;
  you?: YouMatchBits | null;
  selected: boolean;
  onToggle: () => void;
}) {
  const score = scoreLine(match.score);
  const imported = match.status === "imported";
  const result = you?.result ?? null;
  // Your-side rounds won–lost from recent_form tally — NOT remapped CT–T (``you.score``),
  // which for short/degraded demos can disagree with the round table (e.g. 0–2 vs 0–1).
  const yourSide =
    you && you.rounds > 0
      ? `${you.won}–${you.rounds - you.won}`
      : you?.score
        ? `${you.score.you}–${you.score.them}`
        : null;
  const primary = yourSide
    ?? (imported && !you && score ? score.primary : null);
  // One primary metric: K-D when personal bits exist, else rounds count.
  const metric = you
    ? `${you.kills}–${you.deaths}`
    : imported
      ? `${match.rounds_count}r`
      : null;
  const chip =
    result === "won" ? "W"
    : result === "lost" ? "L"
    : result === "tied" ? "T"
    : null;
  const map = mapLabel(match.map_name);
  const ariaMetric = you && metric ? `K-D ${metric}` : metric;
  const aria = [
    chip ? (result === "won" ? "Won" : result === "lost" ? "Lost" : "Tied") : null,
    map,
    primary ? (you ? `score ${primary}` : primary) : null,
    ariaMetric,
  ].filter(Boolean).join(" · ");

  return (
    <button
      type="button"
      className={`match-item ${selected ? "selected" : ""} ${imported ? "" : "match-item-stub"} ${chip ? `result-${result}` : ""}`.trim()}
      aria-expanded={selected}
      aria-label={aria}
      onClick={onToggle}
    >
      {chip ? (
        <span className={`wl-chip ${result}`} aria-hidden="true">{chip}</span>
      ) : (
        <span className="wl-chip empty" aria-hidden="true" />
      )}
      <span className="match-map">
        <strong>{map}</strong>
        {match.source === "upload" && (
          <span className="match-glyph" title="Uploaded" aria-label="Uploaded">↑</span>
        )}
        {match.outdated && (
          <span className="match-glyph warn" title={outdatedText(match) ?? "Outdated"} aria-label="Outdated">↻</span>
        )}
        {match.degraded && (
          <span
            className="match-glyph warn"
            title={match.degraded.detail ?? "PacketEntities skips — positions thin; Rush may omit round_end (recovered from officially-ended)."}
            aria-label="Degraded"
          >
            ⚠
          </span>
        )}
        {!imported && (
          <span className="match-glyph stub" title={STUB_NOTE[match.status_reason ?? ""] ?? STUB_DEFAULT} aria-label="Not imported">⊘</span>
        )}
      </span>
      <span className="match-score" title={imported && !you ? score?.detail : undefined}>
        {primary ? (
          <>
            <strong>{primary}</strong>
            {metric && <small>· {metric}</small>}
          </>
        ) : (
          <span className="steam-muted" aria-label="No score">—</span>
        )}
      </span>
      <span className="match-chevron" aria-hidden="true">{selected ? "▾" : "▸"}</span>
    </button>
  );
}

function MatchStubNote({ match }: { match: MatchSummary }) {
  return (
    <div className="match-stub-note" role="note">
      <p>{STUB_NOTE[match.status_reason ?? ""] ?? STUB_DEFAULT}</p>
    </div>
  );
}

/** Skeleton rows while GET /matches is in flight (avoids flashing the empty state). */
export function MatchListLoading() {
  return (
    <ul className="match-list match-list-loading" aria-busy="true" aria-label="Loading matches">
      {[0, 1, 2].map((i) => (
        <li key={i} className="match-skeleton" aria-hidden="true">
          <span /><span /><span />
        </li>
      ))}
    </ul>
  );
}

type MatchListProps = {
  matches: MatchSummary[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  /** Personal W/L · you–them · K-D keyed by match id (from summary you.recent_form). */
  youById?: Record<string, YouMatchBits>;
  /** Expanded report (or stub note) for the selected match. */
  children?: (match: MatchSummary) => ReactNode;
};

/** Scannable match rows: [W/L] MAP score · metric ▸ */
export function MatchList({ matches, selected, onSelect, youById, children }: MatchListProps) {
  const groups = groupMatches(matches);
  return (
    <div className="match-list-wrap">
      <div className="match-list-heading">
        <span className="section-kicker">{matches.length === 1 ? "1 MATCH" : `${matches.length} MATCHES`}</span>
      </div>
      {groups.map((group) => (
        <div key={group.key} className="match-day-group">
          {group.label ? <span className="match-day-label">{group.label}</span> : null}
          <ul className="match-list">
            {group.matches.map((match) => (
              <li key={match.id}>
                <MatchRow
                  match={match}
                  you={youById?.[match.id]}
                  selected={selected === match.id}
                  onToggle={() => onSelect(selected === match.id ? null : match.id)}
                />
                {selected === match.id && (
                  match.status === "imported"
                    ? children?.(match)
                    : <MatchStubNote match={match} />
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** List load failed: message + retry (keeps EmptyMatches from replacing a real error). */
export function MatchListError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="match-list-error" role="alert">
      <p className="steam-error" style={{ margin: 0 }}>{message}</p>
      <button type="button" className="ghost-button" onClick={onRetry}>Try again</button>
    </div>
  );
}
