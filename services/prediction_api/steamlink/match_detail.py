"""Match detail for the per-match report (``GET /matches/{id}``): how each round ended,
each team's buy (economy) and the signed-in player's clutches and opening duels.

Everything comes from rows stored at parse time (PARSE_VERSION 3, migration 0010):
``rounds.end_reason`` and, per player and round, ``player_rounds.equip_value`` /
``clutch_vs``. Matches parsed before have NULLs there: their rounds get
``end``/``economy`` null and the match ``detail_recorded`` false (re-upload fills it in).

Buy type of a team in a round, from its players' average equipment value at freeze end
(what they carry into the round; checked on Valve MM, FACEIT and HLTV demos):

* ``pistol``: the first round of the match or of a half (most players switched sides
  since the round before) and an average under ``ECO_BELOW`` (overtime halves start
  with full money, so they are not pistol rounds);
* ``eco``: average under ``ECO_BELOW``;
* ``force``: under ``FULL_FROM`` (force / half buy);
* ``full``: ``FULL_FROM`` or more.
"""

from __future__ import annotations

from collections import defaultdict

from .demo_parser import round_outcome
from .storage.base import PlayerRoundRecord, RoundRecord

SIDES = ("ct", "t")
BUY_TYPES = ("pistol", "eco", "force", "full")
ECO_BELOW = 1500
FULL_FROM = 3500


def buy_type(average: float, *, first_of_half: bool) -> str:
    if average < ECO_BELOW:
        return "pistol" if first_of_half else "eco"
    return "full" if average >= FULL_FROM else "force"


def _first_of_half(previous: dict[str, str], current: dict[str, str]) -> bool:
    common = [sid for sid in current if sid in previous]
    return bool(common) and sum(previous[sid] != current[sid] for sid in common) * 2 > len(common)


def economy_by_round(rounds: list[RoundRecord], everyone: list[PlayerRoundRecord]) -> dict[int, dict | None]:
    """``{round number: {"ct": {"buy", "equip_value"}, "t": {...}} | None}`` (None: no
    equipment values recorded for the round). ``equip_value``: the team's average."""

    by_round: dict[int, list[PlayerRoundRecord]] = defaultdict(list)
    for record in everyone:
        by_round[record.round_number].append(record)
    result: dict[int, dict | None] = {}
    previous_sides: dict[str, str] = {}
    for index, rnd in enumerate(sorted(rounds, key=lambda r: r.round_number)):
        players = by_round.get(rnd.round_number, [])
        sides = {p.steam_id: p.side for p in players}
        first = index == 0 or _first_of_half(previous_sides, sides)
        previous_sides = sides or previous_sides
        values = {side: [p.equip_value for p in players if p.side == side and p.equip_value is not None]
                  for side in SIDES}
        if not all(values.values()):
            result[rnd.round_number] = None
            continue
        result[rnd.round_number] = {
            side: {"buy": buy_type(sum(v) / len(v), first_of_half=first), "equip_value": round(sum(v) / len(v))}
            for side, v in values.items()
        }
    return result


def round_end_view(rnd: RoundRecord) -> dict | None:
    """How the round ended: ``{"reason": raw name, "outcome": elimination | bomb | defuse |
    time | surrender | draw | null}``, or None when not recorded."""

    if rnd.end_reason is None:
        return None
    return {"reason": rnd.end_reason, "outcome": round_outcome(rnd.end_reason)}


def clutch_view(record: PlayerRoundRecord, winner: str | None) -> dict | None:
    if not record.clutch_vs:
        return None
    return {"vs": record.clutch_vs, "won": None if winner not in SIDES else winner == record.side}


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def player_detail(rounds: list[RoundRecord], mine: list[PlayerRoundRecord],
                  economy: dict[int, dict | None]) -> dict:
    """The signed-in player's opening duels, clutches and round wins by their team's buy."""

    winners = {r.round_number: r.winner_side for r in rounds}
    opening_kills = sum(r.opening_kill for r in mine)
    opening_deaths = sum(r.opening_death for r in mine)
    taken = opening_kills + opening_deaths
    recorded = [r for r in mine if r.clutch_vs is not None]
    clutches = [r for r in recorded if r.clutch_vs]
    won = [r for r in clutches if winners.get(r.round_number) == r.side]
    by_size: dict[int, dict] = {}
    for r in clutches:
        entry = by_size.setdefault(r.clutch_vs, {"vs": r.clutch_vs, "attempts": 0, "won": 0})
        entry["attempts"] += 1
        entry["won"] += int(winners.get(r.round_number) == r.side)
    buys = {buy: {"rounds": 0, "won": 0} for buy in BUY_TYPES}
    any_buy = False
    for r in mine:
        team = (economy.get(r.round_number) or {}).get(r.side)
        winner = winners.get(r.round_number)
        if team is None or winner not in SIDES:
            continue
        any_buy = True
        buys[team["buy"]]["rounds"] += 1
        buys[team["buy"]]["won"] += int(winner == r.side)
    return {
        "opening_duels": {"taken": taken, "won": opening_kills, "lost": opening_deaths,
                          "win_rate": _rate(opening_kills, taken)},
        # null: not recorded for this match (parsed before clutches were).
        "clutches": None if not recorded else {
            "attempts": len(clutches), "won": len(won), "win_rate": _rate(len(won), len(clutches)),
            "by_size": [by_size[n] for n in sorted(by_size)],
        },
        # Rounds (with a known winner) by your team's buy; null: no equipment values recorded.
        "buys": None if not any_buy else {
            buy: {**b, "win_rate": _rate(b["won"], b["rounds"])} for buy, b in buys.items()
        },
    }
