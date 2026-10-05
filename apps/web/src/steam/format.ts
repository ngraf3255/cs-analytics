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
