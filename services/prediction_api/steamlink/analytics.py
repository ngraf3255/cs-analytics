"""Cross-match analytics over a user's previous matches and rounds
(``GET /matches/summary``).

Everything is computed from stored rows on request: the rounds of every
imported match in the user's list (shared matches included, see
``match_owners``) are scored with the same retrospective model as the
per-match report (``GET /matches/{id}``), one model call per map.

Two scopes:

* ``you`` -- the signed-in player's own rounds, from the per-player rounds
  stored at parse time (``player_rounds``: the side their SteamID was on each
  round, so the halftime swap is handled, plus their kills / deaths / opening
  duels): round win rate overall, as CT and as T and per map, match results,
  K/D, opening duels and recent form. Matches the player is not in (e.g. an
  uploaded pro demo) and matches parsed before per-player rounds were recorded
  are counted separately and left out of these numbers;
* warmup / knife rounds are not in any number (the parser leaves them out);
  matches parsed by an older parser are counted in ``totals.outdated_matches``
  (included as stored: re-upload the demo to bring them up to date);
* everything else (``sides``, ``prediction``, ``opening_kills``, ``maps``,
  ``recent_form``) covers ALL players in every match: "CT win rate" there means
  "rounds won by the CT side". Rounds the model can't score (unknown weapon, no
  opening kill, ...) still count for the side / opening-kill statistics when
  their winner is known.

Matches are ordered by when they were played when known (``played_at``, from
the Game Coordinator for Steam sync), else by when they were added.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timezone

from .scoring import RoundScore, RoundScorer
from .storage.base import MatchRecord, PlayerRoundRecord, RoundRecord, Storage

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


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def match_date(match: MatchRecord) -> dict:
    """When the match was played if known, else when it was added to the user's list (labelled)."""

    return {"date": iso(match.played_at or match.imported_at),
            "date_source": "played" if match.played_at else "imported",
            "played_at": iso(match.played_at)}


def _sort_key(match: MatchRecord):
    return match.played_at or match.imported_at


@dataclass
class _Mine:
    """The player's own rounds over a set of matches."""

    matches: int = 0
    rounds: int = 0
    with_winner: int = 0
    won: int = 0
    side_rounds: dict = field(default_factory=lambda: {"ct": 0, "t": 0})
    side_won: dict = field(default_factory=lambda: {"ct": 0, "t": 0})
    kills: int = 0
    deaths: int = 0
    survived: int = 0
    opening_kills: int = 0
    opening_deaths: int = 0
    won_after_opening_kill: int = 0
    won_after_opening_death: int = 0
    results: dict = field(default_factory=lambda: {"won": 0, "lost": 0, "tied": 0, "unknown": 0})

    def add_round(self, mine: PlayerRoundRecord, winner: str | None) -> None:
        self.rounds += 1
        self.side_rounds[mine.side] = self.side_rounds.get(mine.side, 0) + 1
        self.kills += mine.kills
        self.deaths += mine.deaths
        self.survived += int(mine.survived)
        self.opening_kills += int(mine.opening_kill)
        self.opening_deaths += int(mine.opening_death)
        if winner in SIDES:
            won = int(winner == mine.side)
            self.with_winner += 1
            self.won += won
            self.side_won[mine.side] = self.side_won.get(mine.side, 0) + won
            self.won_after_opening_kill += won if mine.opening_kill else 0
            self.won_after_opening_death += won if mine.opening_death else 0

    def merge(self, other: "_Mine") -> None:
        for name in ("matches", "rounds", "with_winner", "won", "kills", "deaths", "survived", "opening_kills",
                     "opening_deaths", "won_after_opening_kill", "won_after_opening_death"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for side in SIDES:
            self.side_rounds[side] += other.side_rounds[side]
            self.side_won[side] += other.side_won[side]
        for key in self.results:
            self.results[key] += other.results[key]

    def kd(self) -> float | None:
        return round(self.kills / self.deaths, 2) if self.deaths else (float(self.kills) if self.kills else None)

    def rounds_view(self) -> dict:
        return {"rounds": self.rounds, "rounds_with_winner": self.with_winner, "won": self.won,
                "win_rate": _rate(self.won, self.with_winner)}

    def view(self) -> dict:
        return {
            "matches": self.matches, **self.rounds_view(), "results": dict(self.results),
            "kills": self.kills, "deaths": self.deaths, "kd": self.kd(),
            "kills_per_round": round(self.kills / self.rounds, 2) if self.rounds else None,
            "survived": self.survived, "survival_rate": _rate(self.survived, self.rounds),
            **self.duels_view(),
        }

    def duels_view(self) -> dict:
        taken = self.opening_kills + self.opening_deaths
        return {
            "opening_kills": self.opening_kills, "opening_deaths": self.opening_deaths,
            # Each round has one opening kill and one opening death among 10 players, so the
            # lobby average is exactly 0.2 duels per player-round (ROLE_BASELINE).
            "opening_attempt_rate": _rate(taken, self.rounds),
            "opening_win_rate": _rate(self.opening_kills, taken),
        }


# Opening duels per player-round in a 5v5 (2 of 10 players take part in each round's first duel).
ROLE_BASELINE = 0.2
# Minimum rounds on a side before a role is suggested.
ROLE_MIN_ROUNDS = 20


def role_view(tally: "_Mine") -> dict:
    """Per-side role from opening-duel involvement vs the 5v5 average (data-backed, no positions):
    ``entry`` takes the first duel well above average, ``support`` well below, else ``balanced``;
    ``null`` with fewer than ROLE_MIN_ROUNDS rounds on the side."""

    duels = tally.duels_view()
    rate = duels["opening_attempt_rate"]
    role = None
    if tally.rounds >= ROLE_MIN_ROUNDS and rate is not None:
        role = "entry" if rate >= ROLE_BASELINE * 1.4 else "support" if rate <= ROLE_BASELINE * 0.6 else "balanced"
    return {"rounds": tally.rounds, "role": role, "kd": tally.kd(),
            "kills_per_round": round(tally.kills / tally.rounds, 2) if tally.rounds else None,
            "survival_rate": _rate(tally.survived, tally.rounds), **duels}


def _player_match(match: MatchRecord, rounds: list[RoundRecord], mine: list[PlayerRoundRecord]):
    """(_Mine for one match, per-side rounds with a known winner, match result view)."""

    winners = {r.round_number: r.winner_side for r in rounds}
    tally = _Mine(matches=1)
    with_winner = {"ct": 0, "t": 0}
    for record in mine:
        winner = winners.get(record.round_number)
        tally.add_round(record, winner)
        if winner in SIDES and record.side in SIDES:
            with_winner[record.side] += 1
    result = match_result(match, mine)
    tally.results[result["result"] or "unknown"] += 1
    return tally, with_winner, result


def match_result(match: MatchRecord, mine: list[PlayerRoundRecord]) -> dict:
    """The player's final score and result, from the final score (sides at the end) and the
    side they ended the match on."""

    if not mine or match.score_ct is None or match.score_t is None:
        return {"score": None, "result": None}
    last_side = mine[-1].side
    you, them = (match.score_ct, match.score_t) if last_side == "ct" else (match.score_t, match.score_ct)
    return {"score": {"you": you, "them": them}, "result": "won" if you > them else "lost" if you < them else "tied"}


def build_player_summary(imported: list[tuple[MatchRecord, list[RoundRecord]]],
                         player_rounds: dict[str, list[PlayerRoundRecord]], steam_id: str, *, recent: int) -> dict:
    """The ``you`` section of ``GET /matches/summary``: ``imported`` newest first."""

    total, with_winner_total = _Mine(), {"ct": 0, "t": 0}
    by_side = {side: _Mine() for side in SIDES}
    per_map: dict = defaultdict(lambda: [_Mine(), {"ct": 0, "t": 0}])
    per_match: list = []
    without_you = unknown = 0
    for match, rounds in imported:
        mine = player_rounds.get(match.id, [])
        if not mine:
            if match.players_recorded:
                without_you += 1
            else:
                unknown += 1
            continue
        tally, with_winner, result = _player_match(match, rounds, mine)
        winners = {r.round_number: r.winner_side for r in rounds}
        for record in mine:
            if record.side in SIDES:
                by_side[record.side].add_round(record, winners.get(record.round_number))
        total.merge(tally)
        entry = per_map[match.map_name]
        entry[0].merge(tally)
        for side in SIDES:
            with_winner_total[side] += with_winner[side]
            entry[1][side] += with_winner[side]
        per_match.append((match, tally, result, mine))

    def sides(tally: _Mine, with_winner: dict) -> dict:
        return {side: {"rounds": with_winner[side], "won": tally.side_won[side],
                       "win_rate": _rate(tally.side_won[side], with_winner[side])} for side in SIDES}

    def window(items) -> dict:
        tally = _Mine()
        for _, t, _, _ in items:
            tally.merge(t)
        return {"matches": tally.matches, "rounds": tally.rounds, "won": tally.won,
                "win_rate": _rate(tally.won, tally.with_winner), "kd": tally.kd(), "results": dict(tally.results)}

    recent_items, earlier_items = per_match[:recent], per_match[recent:]
    recent_view, earlier_view = window(recent_items), window(earlier_items)
    win_change = kd_change = None
    if recent_view["win_rate"] is not None and earlier_view["win_rate"] is not None:
        win_change = round(recent_view["win_rate"] - earlier_view["win_rate"], 4)
    if recent_view["kd"] is not None and earlier_view["kd"] is not None:
        kd_change = round(recent_view["kd"] - earlier_view["kd"], 2)
    taken = total.opening_kills + total.opening_deaths
    return {
        "steam_id": steam_id,
        **total.view(),
        "matches_without_you": without_you,  # demos you are not in (e.g. an uploaded pro match)
        "matches_unknown": unknown,  # parsed before per-player rounds were recorded: re-upload to include
        "sides": sides(total, with_winner_total),
        "opening_duels": {
            "taken": taken, "won": total.opening_kills, "lost": total.opening_deaths,
            "win_rate": _rate(total.opening_kills, taken),
            "round_win_rate_after_opening_kill": _rate(total.won_after_opening_kill, total.opening_kills),
            "round_win_rate_after_opening_death": _rate(total.won_after_opening_death, total.opening_deaths),
        },
        "roles": {"baseline_opening_attempt_rate": ROLE_BASELINE, "min_rounds": ROLE_MIN_ROUNDS,
                  **{side: role_view(by_side[side]) for side in SIDES}},
        "maps": [
            {"map_name": map_name, **tally.view(), "sides": sides(tally, with_winner)}
            for map_name, (tally, with_winner) in sorted(
                per_map.items(), key=lambda kv: (-kv[1][0].matches, -kv[1][0].rounds, kv[0] or "~"))
        ],
        "recent_form": {
            "window": recent, "recent": recent_view, "earlier": earlier_view,
            "win_rate_change": win_change, "kd_change": kd_change,
            "matches": [
                {"id": m.id, "map_name": m.map_name, **match_date(m), "first_side": mine[0].side,
                 "rounds": t.rounds, "won": t.won, "win_rate": _rate(t.won, t.with_winner),
                 "kills": t.kills, "deaths": t.deaths, **result}
                for m, t, result, mine in recent_items
            ],
        },
    }


def build_user_summary(storage: Storage, scorer: RoundScorer, user_id: str, *, steam_id: str | None = None,
                       recent: int = RECENT_DEFAULT) -> dict:
    """The JSON of ``GET /matches/summary`` for ``user_id`` (see the module docstring);
    ``steam_id``: the user's SteamID64 for the ``you`` section (omitted when None)."""

    listed = storage.list_matches_with_rounds(user_id)
    listed.sort(key=lambda item: _sort_key(item[0]), reverse=True)  # newest played (else added) first
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
    summary = {
        "model": {"calibrated_for_matchmaking": False, "coin_flip_brier_score": COIN_FLIP_BRIER},
        "totals": {
            "matches": len(listed),
            "imported_matches": len(imported),
            "not_imported_matches": len(listed) - len(imported),
            # Parsed by an older parser (e.g. warmup / knife rounds may still be counted, or no
            # per-player rounds): included as stored; re-uploading the demo brings each up to date.
            "outdated_matches": sum(1 for m, _ in imported if m.outdated_reason is not None),
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
                {"id": m.id, "map_name": m.map_name, "imported_at": iso(m.imported_at), **match_date(m),
                 "rounds": t.rounds,
                 "score": None if m.score_ct is None or m.score_t is None else {"ct": m.score_ct, "t": m.score_t},
                 **t.model()}
                for m, t in recent_items
            ],
        },
        # Which numbers above cover everyone in the match (not just the signed-in player).
        "scope": {"sides": "all_players", "prediction": "all_players", "opening_kills": "all_players",
                  "maps": "all_players", "recent_form": "all_players", "you": "signed_in_player"},
    }
    if steam_id is not None:
        summary["you"] = build_player_summary(imported, storage.list_player_rounds(user_id, steam_id), steam_id,
                                              recent=recent)
    return summary
