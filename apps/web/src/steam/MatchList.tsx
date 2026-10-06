import type { ReactNode } from "react";
import type { MatchSummary } from "./types";
import { matchDate, outdatedText, scoreLine } from "./format";

/** One-line stub note per status_reason (the row itself only says NOT IMPORTED). */
const STUB_NOTE: Record<string, string> = {
  demo_unavailable: "Demo gone from Valve — upload the .dem above if you have it.",
  demo_too_large: "Demo too large for the server — try uploading the .dem above.",
  parser_error: "Demo couldn’t be parsed — try uploading the .dem above.",
  demo_has_no_rounds: "Demo has no completed rounds — nothing to score.",
};
const STUB_DEFAULT = "Not imported — upload the .dem above if you have it.";

const mapLabel = (map: string | null) => (map ? map.replace(/^de_/, "").replaceAll("_", " ") : "Unknown map");
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : word.endsWith("ch") ? "es" : "s"}`;

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
            OUTDATED
          </span>
        )}
        {match.degraded && (
          <span className="source-tag outdated-tag" title={match.degraded.detail ?? "Parse incomplete (PacketEntities skips)"}>
            DEGRADED
          </span>
        )}
        {!imported && <span className="source-tag stub-tag">NOT IMPORTED</span>}
      </span>
      <span className="match-score" title={imported ? score?.detail : undefined}>
        {imported && score ? (
          <>
            <strong>{score.primary}</strong>
            <small>CT–T</small>
          </>
        ) : imported ? (
          <span className="steam-muted">No score</span>
        ) : (
          <span className="steam-muted" aria-label="No score">—</span>
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
  /** Expanded report (or stub note) for the selected match. */
  children?: (match: MatchSummary) => ReactNode;
};

/** Scannable match rows: map · score · rounds/date. */
export function MatchList({ matches, selected, onSelect, children }: MatchListProps) {
  return (
    <div className="match-list-wrap">
      <div className="match-list-heading">
        <span className="section-kicker">{matches.length === 1 ? "1 MATCH" : `${matches.length} MATCHES`}</span>
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
      <button type="button" className="ghost-button" onClick={onRetry}>Try again</button>
    </div>
  );
}

