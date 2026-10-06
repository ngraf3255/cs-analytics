import { kdText } from "./format";
import type { MatchPlayer, MatchReport } from "./types";

const short = (steamId: string) => `…${steamId.slice(-5)}`;

function seen(p: MatchPlayer): string {
  const h = p.history;
  if (!h || (!h.matches_with && !h.matches_against)) return "First time";
  return [h.matches_with ? `with ${h.matches_with}` : "", h.matches_against ? `vs ${h.matches_against}` : ""].filter(Boolean).join(" · ");
}

/** Teammates and opponents of one match from the demo's per-player rounds, plus how often
 * you've played with / against each of them in your other matches. Hidden on older APIs. */
export function Scoreboard({ report }: { report: MatchReport }) {
  const players = report.players ?? [];
  if (!players.length) return null;
  const withYou = players.some((p) => p.team === "you");
  const groups = withYou
    ? [
      { title: "YOUR TEAM", rows: players.filter((p) => p.team === "you" || p.team === "teammate") },
      { title: "OPPONENTS", rows: players.filter((p) => p.team === "opponent") },
    ]
    : [
      { title: "STARTED CT", rows: players.filter((p) => p.team === "ct_start") },
      { title: "STARTED T", rows: players.filter((p) => p.team === "t_start") },
    ];
  const regulars = withYou ? players.filter((p) => (p.history?.matches_with ?? 0) + (p.history?.matches_against ?? 0) >= 2) : [];
  return (
    <section className="scoreboard" aria-label="Players">
      <span className="section-kicker">PLAYERS · FROM THE DEMO</span>
      {regulars.length > 0 && (
        <p className="scoreboard-regulars">
          Seen before: {regulars.map((p) => `${short(p.steam_id)} (${seen(p)})`).join(", ")}.
        </p>
      )}
      {groups.map((group) => group.rows.length > 0 && (
        <table key={group.title} className="round-table scoreboard-table" aria-label={group.title === "YOUR TEAM" ? "Your team" : group.title === "OPPONENTS" ? "Opponents" : group.title}>
          <thead>
            <tr>
              <th>{group.title}</th><th>K</th><th>D</th><th>K/D</th><th>KPR</th><th>Opening</th><th>Survived</th>
              {withYou && <th>Played before</th>}
            </tr>
          </thead>
          <tbody>
            {group.rows.map((p) => (
              <tr key={p.steam_id} className={p.team === "you" ? "scoreboard-you" : ""}>
                <td>
                  <a href={`https://steamcommunity.com/profiles/${p.steam_id}`} target="_blank" rel="noreferrer" title={p.steam_id}>
                    {p.team === "you" ? "You" : short(p.steam_id)} ↗
                  </a>
                </td>
                <td>{p.kills}</td>
                <td>{p.deaths}</td>
                <td>{kdText(p.kd)}</td>
                <td>{p.kills_per_round == null ? "—" : p.kills_per_round.toFixed(2)}</td>
                <td>{p.opening_kills}–{p.opening_deaths}</td>
                <td>{p.survived}/{p.rounds}</td>
                {withYou && <td className="steam-muted">{p.team === "you" ? "" : seen(p)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      ))}
      <p className="steam-muted scoreboard-note">Steam names, ranks and damage aren’t in the stored data; open a profile to see who it is. “Played before” counts your other imported matches.</p>
    </section>
  );
}
