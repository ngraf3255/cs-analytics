import type { MatchReport, RoundReport } from "./types";

/** One "what to fix" note from a single demo: what happened, the rounds it's based on, and a
 * concrete thing to try. Only computed from what the demo records (sides, kills, deaths, opening
 * duels, survival, round winners, the model's in-hindsight odds); thresholds are deliberately
 * conservative so a quiet match gives no tips rather than made-up ones. */
export type Tip = { id: string; tone: "fix" | "keep"; title: string; evidence: string; rounds: number[]; advice: string };

const pct = (part: number, whole: number) => Math.round((part / whole) * 100);
const list = (rounds: RoundReport[]) => rounds.map((r) => r.round_number);

export function coachTips(report: MatchReport, max = 3): Tip[] {
  if (report.you?.status !== "in_match") return [];
  const mine = report.rounds.filter((r) => r.you);
  if (mine.length < 8) return [];
  const tips: Tip[] = [];

  const diedFirst = mine.filter((r) => r.you!.opening_death);
  if (diedFirst.length >= 3 && diedFirst.length / mine.length >= 0.12) {
    const won = diedFirst.filter((r) => r.you!.won).length;
    tips.push({
      id: "opening-deaths", tone: "fix", title: "You died first too often",
      evidence: `First death of the round in ${diedFirst.length} of ${mine.length} rounds; your team won ${won} of those.`,
      rounds: list(diedFirst),
      advice: "Take first contact with a teammate ready to trade, or hold an angle instead of dry-peeking early.",
    });
  }

  const thrown = mine.filter((r) => r.you!.won === false && r.you!.win_probability != null && r.you!.win_probability >= 0.65);
  if (thrown.length >= 2) {
    tips.push({
      id: "lost-advantage", tone: "fix", title: "Advantages that slipped away",
      evidence: `${thrown.length} rounds lost after the opening duel left your team ≥65% favourite.`,
      rounds: list(thrown),
      advice: "After an early pick, slow down: group up, trade the next fight and play for the time / bomb instead of chasing kills.",
    });
  }

  const wonDuelLost = mine.filter((r) => r.you!.opening_kill && r.you!.won === false);
  if (wonDuelLost.length >= 2) {
    tips.push({
      id: "duel-not-converted", tone: "fix", title: "Opening kills not converted",
      evidence: `You got the first kill but your team still lost ${wonDuelLost.length} round${wonDuelLost.length === 1 ? "" : "s"}.`,
      rounds: list(wonDuelLost),
      advice: "After your opening kill, call it and reposition; don't re-peek the same angle alone.",
    });
  }

  const side = (s: "ct" | "t") => {
    const rounds = mine.filter((r) => r.you!.side === s && r.you!.won != null);
    return { rounds, won: rounds.filter((r) => r.you!.won).length };
  };
  const ct = side("ct"), t = side("t");
  if (ct.rounds.length >= 6 && t.rounds.length >= 6) {
    const gap = pct(ct.won, ct.rounds.length) - pct(t.won, t.rounds.length);
    if (Math.abs(gap) >= 25) {
      const weak = gap > 0 ? { name: "T", ...t } : { name: "CT", ...ct };
      const strong = gap > 0 ? { name: "CT", ...ct } : { name: "T", ...t };
      tips.push({
        id: "side-gap", tone: "fix", title: `Your ${weak.name} side lagged`,
        evidence: `${weak.name} ${weak.won}/${weak.rounds.length} rounds won vs ${strong.name} ${strong.won}/${strong.rounds.length}.`,
        rounds: list(weak.rounds.filter((r) => !r.you!.won)),
        advice: weak.name === "T"
          ? "Review your T executes on this map: utility before entry and a set trade order for the first contact."
          : "Review your CT setups on this map: crossfires and falling back for retakes instead of solo holds.",
      });
    }
  }

  const quiet = mine.filter((r) => r.you!.kills === 0 && r.you!.deaths > 0);
  if (quiet.length / mine.length >= 0.5 && quiet.length >= 6) {
    tips.push({
      id: "low-impact", tone: "fix", title: "Many rounds without a kill",
      evidence: `Died without a kill in ${quiet.length} of ${mine.length} rounds.`,
      rounds: list(quiet),
      advice: "Play closer to a teammate so your deaths trade, and use utility to win the first fight you take.",
    });
  }

  const fix = tips.slice(0, max);
  if (!fix.length) {
    const kills = mine.filter((r) => r.you!.opening_kill);
    if (kills.length >= 3) {
      fix.push({
        id: "strong-openings", tone: "keep", title: "Opening duels went your way",
        evidence: `First kill of the round in ${kills.length} rounds; your team won ${kills.filter((r) => r.you!.won).length} of them.`,
        rounds: list(kills), advice: "Nothing stood out to fix in this demo. Keep taking those duels with support.",
      });
    }
  }
  return fix;
}
