import type { MatchSummary, MatchResult } from "./types";

const DAY: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric" };

/** When the match was played if the API knows (Steam sync), else when it was added, labelled. Older
 * APIs only send imported_at. */
export function matchDate(match: Pick<MatchSummary, "imported_at" | "date" | "date_source">): {
  day: string; played: boolean; label: string;
} {
  const played = match.date_source === "played";
  const day = new Date((played ? match.date : undefined) ?? match.imported_at).toLocaleDateString(undefined, DAY);
  return { day, played, label: played ? "Played (from Valve)" : "Added (no match date in the demo)" };
}

/** "Won 13–11" / "Lost 1–2" / "Tied 12–12"; null when the final score is unknown. */
export function resultText(result: MatchResult): string | null {
  if (!result.score || !result.result) return null;
  const word = result.result === "won" ? "Won" : result.result === "lost" ? "Lost" : "Tied";
  return `${word} ${result.score.you}–${result.score.them}`;
}

export const kdText = (kd: number | null | undefined) => (kd == null ? "—" : kd.toFixed(2));

/** Why a match should be uploaded again (it was parsed by an older version), or null. */
export function outdatedText(match: Pick<MatchSummary, "outdated">): string | null {
  if (!match.outdated) return null;
  const why = match.outdated.reason === "players_not_recorded"
    ? "Imported before per-player stats were recorded."
    : "Parsed by an older version (warmup or knife rounds may still be counted).";
  return `${why} Upload this demo again to update it.`;
}

const MINUTE = 60_000;

function span(ms: number): string {
  const minutes = Math.round(ms / MINUTE);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

/** "just now" / "5 min ago" / "3 h ago" / "2 days ago". */
export function timeAgo(iso: string, now = Date.now()): string {
  const ms = now - new Date(iso).getTime();
  return ms < MINUTE ? "just now" : `${span(ms)} ago`;
}

/** "in 25 min" / "in 2 h"; "any minute now" when due or overdue. */
export function timeUntil(iso: string, now = Date.now()): string {
  const ms = new Date(iso).getTime() - now;
  return ms < MINUTE ? "any minute now" : `in ${span(ms)}`;
}

/** "every 30 min" / "every 2 h". */
export function everyText(seconds: number): string {
  return `every ${span(seconds * 1000)}`;
}
