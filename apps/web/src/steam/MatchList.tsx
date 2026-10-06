import type { ReactNode } from "react";
import type { MatchSummary } from "./types";
import { matchDate, outdatedText, scoreLine } from "./format";

const MATCH_STATUS: Record<string, string> = {
  demo_unavailable: "Demo is no longer available from Valve.",
  demo_too_large: "Demo exceeded the server’s size limit.",
  parser_error: "Demo could not be parsed.",
  demo_has_no_rounds: "Demo has no completed rounds.",
};

const mapLabel = (map: string | null) => (map ? map.replace(/^de_/, "").replaceAll("_", " ") : "Unknown map");
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : word.endsWith("ch") ? "es" : "s"}`;

function statusText(match: MatchSummary): string {
  if (match.status === "imported") return plural(match.rounds_count, "round");
  return MATCH_STATUS[match.status_reason ?? ""] ?? "Not imported";
}

function MatchRow({
  match,
  selected,
  onToggle,
}: {
  match: MatchSummary;
  selected: boolean;
  onToggle: () => void;
}) {
  const date = matchDate(match);
  const score = scoreLine(match.score);
  const imported = match.status === "imported";
  const reason = !imported ? statusText(match) : null;

  return (
    <button
      type="button"
      className={`match-item ${selected ? "selected" : ""} ${imported ? "" : "match-item-stub"}`.trim()}
      aria-expanded={selected}
      onClick={onToggle}
    >
      <span className="match-map">
        <strong>{mapLabel(match.map_name)}</strong>
        {match.source === "upload" && <span className="source-tag">UPLOADED</span>}
        {match.outdated && (
          <span className="source-tag outdated-tag" title={outdatedText(match) ?? undefined}>
            RE-UPLOAD TO UPDATE
          </span>
        )}
        {!imported && <span className="source-tag stub-tag">NOT IMPORTED</span>}
      </span>
      <span className="match-score" title={score?.detail}>
        {imported && score ? (
          <>
            <strong>{score.primary}</strong>
            <small>{score.detail}</small>
          </>
        ) : imported ? (
          <span className="steam-muted">No score</span>
        ) : (
          <span className="match-status-short" title={reason ?? undefined}>{reason}</span>
        )}
      </span>
      <span className="match-meta steam-muted" title={date.label}>
        {imported && <span className="match-rounds">{plural(match.rounds_count, "round")}</span>}
        <span className="match-date">{date.played ? date.day : `Added ${date.day}`}</span>
        <span className="match-chevron" aria-hidden="true">{selected ? "▾" : "▸"}</span>
      </span>
    </button>
  );
}

function MatchStubNote({ match }: { match: MatchSummary }) {
  const reason = MATCH_STATUS[match.status_reason ?? ""] ?? "This match could not be imported.";
  return (
    <div className="match-stub-note" role="note">
      <p>{reason}</p>
      <p className="steam-muted">If you still have the .dem, upload it above to analyse the rounds.</p>
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
  /** Expanded report (or stub note) for the selected match. */
  children?: (match: MatchSummary) => ReactNode;
};

/** Scannable match rows: map · score · rounds/date. */
export function MatchList({ matches, selected, onSelect, children }: MatchListProps) {
  return (
    <div className="match-list-wrap">
      <div className="match-list-heading">
        <span className="section-kicker">{matches.length === 1 ? "1 MATCH" : `${matches.length} MATCHES`}</span>
        <span className="steam-muted match-list-hint">Tap a match for the round report</span>
      </div>
      <ul className="match-list">
        {matches.map((match) => (
          <li key={match.id}>
            <MatchRow
              match={match}
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
  );
}

/** List load failed: message + retry (keeps EmptyMatches from replacing a real error). */
export function MatchListError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="match-list-error" role="alert">
      <p className="steam-error" style={{ margin: 0 }}>{message}</p>
      <button type="button" className="ghost-button" onClick={onRetry}>TRY AGAIN</button>
    </div>
  );
}

