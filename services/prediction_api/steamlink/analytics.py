"""Cross-match analytics over a user's previous matches and rounds
(``GET /matches/summary``).

Everything is computed from stored rows on request: the rounds of every
imported match in the user's list (shared matches included, see
``match_owners``) are scored with the same retrospective model as the
per-match report (``GET /matches/{id}``), one model call per map.

Sides are the map sides (CT / T) of all players in the match: the user's own
team is not stored yet, so "CT win rate" means "rounds won by the CT side".
Rounds the model can't score (unknown weapon, no opening kill, ...) still
count for the side / opening-kill statistics when their winner is known.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timezone

from .scoring import RoundScore, RoundScorer
from .storage.base import MatchRecord, RoundRecord, Storage

SIDES = ("ct", "t")
# Bins of the model's confidence in its favourite (max(P(CT), P(T)) is always >= 0.5).
CALIBRATION_BINS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0))
COIN_FLIP_BRIER = 0.25  # Brier score of always saying 50 / 50
TOP_WEAPONS = 5
RECENT_DEFAULT = 10


def _rate(part: float, whole: float) -> float | None:
    return round(part / whole, 4) if whole else None


@dataclass
class _Tally:
    """Counters over a set of rounds (a match, a map, the recent window, everything)."""

    rounds: int = 0
    with_winner: int = 0
    won: dict = field(default_factory=lambda: {"ct": 0, "t": 0})
    scored: int = 0  # the model scored the round and its winner is known
    correct: int = 0
    brier_sum: float = 0.0

    def add(self, rnd: RoundRecord, score: RoundScore) -> None:
        self.rounds += 1
        if rnd.winner_side not in SIDES:
            return
        self.with_winner += 1
        self.won[rnd.winner_side] += 1
        if score.unscored_reason is None and score.probability_ct is not None:
            self.scored += 1
            self.correct += int(score.predicted_winner == rnd.winner_side)
            self.brier_sum += (score.probability_ct - (1.0 if rnd.winner_side == "ct" else 0.0)) ** 2

    def merge(self, other: "_Tally") -> None:
        self.rounds += other.rounds
        self.with_winner += other.with_winner
        for side in SIDES:
            self.won[side] += other.won[side]
        self.scored += other.scored
        self.correct += other.correct
        self.brier_sum += other.brier_sum

    def sides(self) -> dict:
        return {
            "rounds_with_winner": self.with_winner,
            "ct_won": self.won["ct"], "t_won": self.won["t"],
            "ct_win_rate": _rate(self.won["ct"], self.with_winner),
            "t_win_rate": _rate(self.won["t"], self.with_winner),
        }

    def model(self) -> dict:
        return {
            "scored_rounds": self.scored, "correct": self.correct,
            "hit_rate": _rate(self.correct, self.scored),
            "brier_score": round(self.brier_sum / self.scored, 4) if self.scored else None,
        }


def _score_by_map(scorer: RoundScorer, imported: list[tuple[MatchRecord, list[RoundRecord]]]) -> dict:
    """{(match id, round number): RoundScore}; one model call per map."""

    by_map: dict = defaultdict(list)
    for match, rounds in imported:
        for rnd in rounds:
            by_map[match.map_name].append((match.id, rnd))
    scores = {}
    for map_name, items in by_map.items():
        for (match_id, rnd), score in zip(items, scorer.score_rounds(map_name, [r for _, r in items])):
            scores[(match_id, rnd.round_number)] = score
    return scores


def build_user_summary(storage: Storage, scorer: RoundScorer, user_id: str, *, recent: int = RECENT_DEFAULT) -> dict:
    """The JSON of ``GET /matches/summary`` for ``user_id`` (see the module docstring)."""

    def iso(value):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None

    listed = storage.list_matches_with_rounds(user_id)  # newest first
    imported = [(m, rs) for m, rs in listed if m.status == "imported"]
    scores = _score_by_map(scorer, imported)

    total = _Tally()
    per_match: list[tuple[MatchRecord, _Tally]] = []
    per_map: dict = defaultdict(lambda: {"matches": 0, "tally": _Tally()})
    calibration = [{"rounds": 0, "correct": 0, "confidence_sum": 0.0} for _ in CALIBRATION_BINS]
    unscored: Counter = Counter()
    opening = {side: {"rounds": 0, "converted": 0} for side in SIDES}
    opening_seconds: list[float] = []
    weapons: dict = defaultdict(lambda: {"rounds": 0, "converted": 0})
    baseline_correct = 0  # scored rounds won by the side that got the opening kill

    for match, rounds in imported:
        tally = _Tally()
        for rnd in rounds:
            score = scores[(match.id, rnd.round_number)]
            tally.add(rnd, score)
            if score.unscored_reason is not None:
                unscored[score.unscored_reason] += 1
            known_winner = rnd.winner_side in SIDES
            if known_winner and rnd.opening_kill_side in SIDES:
                converted = int(rnd.opening_kill_side == rnd.winner_side)
                opening[rnd.opening_kill_side]["rounds"] += 1
                opening[rnd.opening_kill_side]["converted"] += converted
                if rnd.opening_weapon:
                    weapons[rnd.opening_weapon]["rounds"] += 1
                    weapons[rnd.opening_weapon]["converted"] += converted
            if rnd.opening_kill_seconds is not None and rnd.opening_kill_seconds >= 0:
                opening_seconds.append(rnd.opening_kill_seconds)
            if known_winner and score.unscored_reason is None and score.probability_ct is not None:
                baseline_correct += int(rnd.opening_kill_side == rnd.winner_side)
                confidence = max(score.probability_ct, score.probability_t)
                index = min(int(round((confidence - 0.5) * 10, 9)), len(CALIBRATION_BINS) - 1)
                bin_ = calibration[max(index, 0)]
                bin_["rounds"] += 1
                bin_["confidence_sum"] += confidence
                bin_["correct"] += int(score.predicted_winner == rnd.winner_side)
        total.merge(tally)
        per_match.append((match, tally))
        entry = per_map[match.map_name]
        entry["matches"] += 1
        entry["tally"].merge(tally)

    def window(items: list[tuple[MatchRecord, _Tally]]) -> dict:
        tally = _Tally()
        for _, t in items:
            tally.merge(t)
        return {"matches": len(items), **tally.model(), "ct_win_rate": tally.sides()["ct_win_rate"]}

    recent_items, earlier_items = per_match[:recent], per_match[recent:]
    recent_view, earlier_view = window(recent_items), window(earlier_items)
    change = None
    if recent_view["hit_rate"] is not None and earlier_view["hit_rate"] is not None:
        change = round(recent_view["hit_rate"] - earlier_view["hit_rate"], 4)

    opening_total = sum(o["rounds"] for o in opening.values())
    opening_converted = sum(o["converted"] for o in opening.values())
    model_view = total.model()
    return {
        "model": {"calibrated_for_matchmaking": False, "coin_flip_brier_score": COIN_FLIP_BRIER},
        "totals": {
            "matches": len(listed),
            "imported_matches": len(imported),
            "not_imported_matches": len(listed) - len(imported),
            "rounds": total.rounds,
            "rounds_with_winner": total.with_winner,
            "scored_rounds": total.scored,
            "unscored_rounds": total.rounds - total.scored,
            "first_imported_at": iso(min((m.imported_at for m, _ in imported), default=None)),
            "last_imported_at": iso(max((m.imported_at for m, _ in imported), default=None)),
        },
        # The round-win model over every previous scored round (actual winner known).
        "prediction": {
            **model_view,
            # Baseline: always back the side that got the opening kill.
            "opening_kill_baseline_hit_rate": _rate(baseline_correct, total.scored),
            "calibration": [
                {"min": lo, "max": hi, "rounds": b["rounds"],
                 "mean_confidence": _rate(b["confidence_sum"], b["rounds"]),
                 "hit_rate": _rate(b["correct"], b["rounds"])}
                for (lo, hi), b in zip(CALIBRATION_BINS, calibration)
            ],
        },
        "sides": total.sides(),
        "opening_kills": {
            "rounds": opening_total, "converted": opening_converted,
            "conversion_rate": _rate(opening_converted, opening_total),
            "average_seconds": round(sum(opening_seconds) / len(opening_seconds), 2) if opening_seconds else None,
            "by_side": {side: {**o, "conversion_rate": _rate(o["converted"], o["rounds"])}
                        for side, o in opening.items()},
            "top_weapons": [
                {"weapon": weapon, **w, "conversion_rate": _rate(w["converted"], w["rounds"])}
                for weapon, w in sorted(weapons.items(), key=lambda kv: (-kv[1]["rounds"], kv[0]))[:TOP_WEAPONS]
            ],
        },
        "maps": [
            {"map_name": map_name, "matches": entry["matches"], "rounds": entry["tally"].rounds,
             **entry["tally"].sides(), **entry["tally"].model()}
            for map_name, entry in sorted(
                per_map.items(), key=lambda kv: (-kv[1]["matches"], -kv[1]["tally"].rounds, kv[0] or "~"))
        ],
        "unscored_reasons": [{"reason": reason, "rounds": n}
                             for reason, n in sorted(unscored.items(), key=lambda kv: (-kv[1], kv[0]))],
        # Recent form: the last ``recent`` imported matches (newest first) vs the ones before.
        "recent_form": {
            "window": recent,
            "recent": recent_view,
            "earlier": earlier_view,
            "hit_rate_change": change,
            "matches": [
                {"id": m.id, "map_name": m.map_name, "imported_at": iso(m.imported_at), "rounds": t.rounds,
                 "score": None if m.score_ct is None or m.score_t is None else {"ct": m.score_ct, "t": m.score_t},
                 **t.model()}
                for m, t in recent_items
            ],
        },
    }
