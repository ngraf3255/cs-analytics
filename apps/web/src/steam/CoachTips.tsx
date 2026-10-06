import { coachTips } from "./coachTips";
import type { MatchReport } from "./types";
import "./coachTips.css";

/** "What to fix" from this demo only (rule-based, see coachTips). Hidden when empty / not in it. */
export function CoachTips({ report }: { report: MatchReport }) {
  if (report.you?.status !== "in_match") return null;
  const tips = coachTips(report);
  if (tips.length === 0) return null;
  return (
    <section className="coach-tips" aria-label="What to fix">
      <span className="section-kicker">WHAT TO FIX · FROM THIS DEMO</span>
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
    </section>
  );
}
