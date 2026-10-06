import { coachTips } from "./coachTips";
import type { MatchReport } from "./types";
import "./coachTips.css";

/** "What to fix" from this demo only (rule-based, see coachTips). Hidden when you're not in it. */
export function CoachTips({ report }: { report: MatchReport }) {
  if (report.you?.status !== "in_match") return null;
  const tips = coachTips(report);
  return (
    <section className="coach-tips" aria-label="What to fix">
      <span className="section-kicker">WHAT TO FIX · FROM THIS DEMO</span>
      {tips.length === 0 ? (
        <p className="steam-muted">Nothing clear stood out.</p>
      ) : (
        <ol className="coach-list">
          {tips.map((tip) => (
            <li key={tip.id} className={`coach-tip ${tip.tone}`}>
              <strong>{tip.title}</strong>
              <p>{tip.evidence}</p>
              <p className="coach-advice">{tip.advice}</p>
              {tip.rounds.length > 0 && <small className="coach-rounds">Rounds {tip.rounds.join(", ")}</small>}
            </li>
          ))}
        </ol>
      )}
      <p className="steam-muted coach-note">Rule-based from this demo. Positions and utility aren’t recorded.</p>
    </section>
  );
}
