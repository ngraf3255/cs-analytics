import type { MatchReport, RoundReport } from "./types";
import { weaponName } from "./weapons";

const pct = (part: number, whole: number) => (whole ? `${Math.round((part / whole) * 100)}%` : "—");
const sideName = (side: string | null | undefined) => (side === "ct" ? "CT" : side === "t" ? "T" : "—");

/** One cell per round: who won it (map side), whether your team did, who got the first kill,
 * and a divider where you switched sides (halftime / overtime). Only data the demo records. */
export function RoundTimeline({ rounds }: { rounds: RoundReport[] }) {
  const tracked = rounds.some((r) => r.you);
  let you = 0, them = 0;
  return (
    <div className="round-timeline-wrap">
      <span className="section-kicker">ROUND TIMELINE</span>
      <ol className="round-timeline" aria-label="Round timeline">
        {rounds.map((round, i) => {
          const mine = round.you;
          if (mine?.won === true) you += 1;
          else if (mine?.won === false) them += 1;
          const prev = rounds[i - 1]?.you;
          const swap = !!(mine && prev && prev.side !== mine.side);
          const firstKill = round.opening_kill?.side ?? null;
          const label = [
            `Round ${round.round_number}`,
            round.actual_winner ? `${sideName(round.actual_winner)} won` : "winner unknown",
            mine?.won != null ? (mine.won ? "your team won" : "your team lost") : null,
            mine ? `score ${you}–${them}` : null,
            firstKill ? `first kill ${sideName(firstKill)}${mine && firstKill === mine.side ? " (your team)" : ""}` : null,
            mine?.opening_kill ? "you got the first kill" : mine?.opening_death ? "you died first" : null,
          ].filter(Boolean).join(", ");
          return (
            <li key={round.round_number} className={["tl-round", round.actual_winner ?? "none",
              mine?.won === true ? "you-won" : mine?.won === false ? "you-lost" : "", swap ? "side-swap" : ""].filter(Boolean).join(" ")}
              title={label} aria-label={label}>
              <span className="tl-n" aria-hidden="true">{round.round_number}</span>
              <span className={`tl-ok ${firstKill ?? ""}`} aria-hidden="true" />
              {(mine?.opening_kill || mine?.opening_death) && <span className={`tl-you ${mine.opening_kill ? "kill" : "death"}`} aria-hidden="true" />}
            </li>
          );
        })}
      </ol>
      <div className="report-legend tl-legend">
        <span><i className="legend tl-ct" /> CT won</span>
        <span><i className="legend tl-t" /> T won</span>
        <span><i className="tl-dot" /> First kill (side colour)</span>
        {tracked && <span><i className="legend your-win" /> Underline: your team won</span>}
        {tracked && <span><i className="tl-dot you" /> You got / gave up the first kill</span>}
      </div>
    </div>
  );
}

type Conv = { rounds: number; converted: number };

/** Opening duels of this match: how often the side with the first kill won the round, by side,
 * the usual timing and weapons, and your own duels when you are in the demo. */
export function OpeningDuels({ report }: { report: MatchReport }) {
  const withKill = report.rounds.filter((r) => r.opening_kill?.side && r.actual_winner);
  if (!withKill.length) return null;
  const by: Record<"ct" | "t", Conv> = { ct: { rounds: 0, converted: 0 }, t: { rounds: 0, converted: 0 } };
  const team: Conv = { rounds: 0, converted: 0 };
  const opp: Conv = { rounds: 0, converted: 0 };
  const times: number[] = [];
  const weapons = new Map<string, number>();
  for (const r of withKill) {
    const side = r.opening_kill!.side as "ct" | "t";
    const won = r.actual_winner === side;
    by[side].rounds += 1; by[side].converted += Number(won);
    if (r.opening_kill!.seconds != null) times.push(r.opening_kill!.seconds);
    if (r.opening_kill!.weapon) weapons.set(r.opening_kill!.weapon, (weapons.get(r.opening_kill!.weapon) ?? 0) + 1);
    if (r.you) {
      const ours = side === r.you.side;
      const bucket = ours ? team : opp;
      bucket.rounds += 1;
      bucket.converted += Number(r.actual_winner === r.you.side);
    }
  }
  const sorted = [...times].sort((a, b) => a - b);
  const median = sorted.length ? sorted[Math.floor((sorted.length - 1) / 2)] : null;
  const top = [...weapons.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
  const mine = report.you?.status === "in_match" ? report.you : null;
  const myKills = report.rounds.filter((r) => r.you?.opening_kill);
  const myDeaths = report.rounds.filter((r) => r.you?.opening_death);
  return (
    <div className="opening-duels">
      <span className="section-kicker">OPENING DUELS · THIS MATCH</span>
      <dl className="report-header summary-tiles" aria-label="Opening duels">
        {mine && (
          <div>
            <dt>YOUR TEAM FIRST</dt><dd>{team.rounds} of {team.rounds + opp.rounds}</dd>
            <small>Won {pct(team.converted, team.rounds)} · {pct(opp.converted, opp.rounds)} when they struck first</small>
          </div>
        )}
        {mine && (
          <div>
            <dt>YOUR DUELS</dt><dd>{myKills.length} W · {myDeaths.length} L</dd>
            <small>
              {myKills.length + myDeaths.length
                ? `Won ${pct(myKills.filter((r) => r.you?.won).length, myKills.length)} after OK · ${pct(myDeaths.filter((r) => r.you?.won).length, myDeaths.length)} after OD`
                : "You didn’t take an opening duel"}
            </small>
          </div>
        )}
        <div>
          <dt>FIRST KILL → ROUND</dt><dd>{pct(by.ct.converted + by.t.converted, withKill.length)}</dd>
          <small>CT {by.ct.converted}/{by.ct.rounds} · T {by.t.converted}/{by.t.rounds} converted</small>
        </div>
        <div>
          <dt>TYPICAL TIMING</dt><dd>{median == null ? "—" : `${median.toFixed(0)}s`}</dd>
          <small>{top.length ? `Median first kill · ${top.map(([w, n]) => `${weaponName(w)} ×${n}`).join(", ")}` : "Median first kill"}</small>
        </div>
      </dl>
      <p className="steam-muted detail-note">Clutches and economy aren’t recorded yet.</p>
    </div>
  );
}
