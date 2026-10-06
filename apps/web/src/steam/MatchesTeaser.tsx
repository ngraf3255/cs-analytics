/** Signed out: a static, clearly-labelled sample of a match report, so the section shows what
 * you get (round-by-round model reads) instead of a bare sign-in card. Uses the same markup and
 * classes as the real report (Matches.tsx) and is hidden from assistive tech as decoration;
 * the caption says what it is. */

type SampleRound = { n: number; opening: string; actual: "ct" | "t"; favoured: "ct" | "t"; ct: number; you?: "win" | "loss" };

const SAMPLE_ROUNDS: SampleRound[] = [
  { n: 1, opening: "T · Glock-18 · 18.4s", actual: "t", favoured: "t", ct: 31, you: "win" },
  { n: 2, opening: "CT · USP-S · 24.1s", actual: "ct", favoured: "ct", ct: 66, you: "loss" },
  { n: 3, opening: "T · AK-47 · 41.7s", actual: "ct", favoured: "t", ct: 38, you: "loss" },
  { n: 4, opening: "CT · M4A1-S · 12.9s", actual: "ct", favoured: "ct", ct: 72, you: "loss" },
  { n: 5, opening: "T · AWP · 9.6s", actual: "t", favoured: "t", ct: 27, you: "win" },
];

const SIDE = { ct: "CT", t: "T" } as const;

export function MatchesTeaser() {
  return (
    <figure className="matches-teaser" aria-label="Sample match report">
      <figcaption className="teaser-caption">
        <span className="section-kicker">SAMPLE REPORT</span>
      </figcaption>
      <div className="match-report teaser-report" aria-hidden="true">
        <dl className="report-header">
          <div><dt>MAP</dt><dd>Mirage</dd><small>22 rounds</small></div>
          <div><dt>SCORE</dt><dd>13 – 9</dd><small>CT 9 · T 13 (sides at the end)</small></div>
          <div><dt>MODEL HIT RATE</dt><dd>16 / 21</dd><small>favourite won</small></div>
          <div><dt>YOUR OPENERS</dt><dd>5</dd><small>first kills</small></div>
        </dl>
        <div className="report-legend">
          <span><i className="legend actual" /> Actual winner</span>
          <span><i className="legend model" /> Model estimate</span>
          <span><i className="legend your-win" /> Your team won</span>
        </div>
        <table className="round-table">
          <thead>
            <tr><th>Round</th><th>Opening kill</th><th>Actual winner</th><th>Model estimate</th></tr>
          </thead>
          <tbody>
            {SAMPLE_ROUNDS.map((round) => (
              <tr key={round.n} className={round.you === "win" ? "your-win" : round.you === "loss" ? "your-loss" : ""}>
                <td>{round.n}</td>
                <td>{round.opening}</td>
                <td><span className={`actual-chip ${round.actual}`}>{SIDE[round.actual]}</span></td>
                <td><span className="model-estimate">{SIDE[round.favoured]} favoured · CT {round.ct}% / T {100 - round.ct}%</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
