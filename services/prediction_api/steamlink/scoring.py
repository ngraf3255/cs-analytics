"""Retrospective round scoring with the trained model."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .storage.base import RoundRecord

MAX_OPENING_KILL_SECONDS = 120.0  # same bound as POST /predict


@dataclass(frozen=True)
class RoundScore:
    predicted_winner: str | None
    probability_ct: float | None
    probability_t: float | None
    unscored_reason: str | None


class RoundScorer:
    def __init__(self, model, map_options: list[str], weapon_options: list[str]):
        self.model = model
        self.maps = set(map_options)
        self.weapons = set(weapon_options)

    def unscored_reason(self, map_name: str | None, rnd: RoundRecord) -> str | None:
        if rnd.unscored_reason:
            return rnd.unscored_reason
        if map_name not in self.maps:
            return "map_not_in_model"
        if rnd.opening_kill_side not in ("ct", "t"):
            return "opening_kill_side_unknown"
        if rnd.opening_weapon not in self.weapons:
            return "weapon_not_in_model"
        if rnd.opening_kill_seconds is None or not 0 <= rnd.opening_kill_seconds <= MAX_OPENING_KILL_SECONDS:
            return "opening_kill_time_out_of_range"
        return None

    def score_rounds(self, map_name: str | None, rounds: list[RoundRecord]) -> list[RoundScore]:
        reasons = [self.unscored_reason(map_name, r) for r in rounds]
        scorable = [r for r, reason in zip(rounds, reasons) if reason is None]
        probabilities: list[tuple[float, float]] = []
        if scorable:
            frame = pd.DataFrame([
                {"map_name": map_name, "opening_kill_side": r.opening_kill_side,
                 "opening_kill_seconds": r.opening_kill_seconds, "opening_weapon": r.opening_weapon}
                for r in scorable
            ])
            classes = [int(c) for c in self.model.classes_]
            for row in self.model.predict_proba(frame):
                by_class = dict(zip(classes, row))
                probabilities.append((float(by_class[0]), float(by_class[1])))
        it = iter(probabilities)
        scores = []
        for reason in reasons:
            if reason:
                scores.append(RoundScore(None, None, None, reason))
            else:
                ct, t = next(it)
                scores.append(RoundScore("t" if t > ct else "ct", ct, t, None))
        return scores
