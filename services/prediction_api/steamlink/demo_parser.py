"""Turn a parsed CS2 demo into the model's round features.

Feature definitions match the training data (``data/rounds.parquet`` /
``python/analysis.py``):
* opening kill = the first player death between the round's freeze end and
  its round_end (training data: opening_kill_tick is always >= freeze_end_tick
  and <= round_end_tick). Deaths after the previous round_end but before this
  round's freeze end (post-round "exit frags", warmup) are ignored; a real
  SourceTV demo has them, so counting them mislabels the opening kill;
* ``opening_kill_side`` = the killer's side; for ``world`` deaths (fall damage,
  etc.) the training data records the victim's side, so we do the same;
* ``opening_kill_seconds`` = (death tick - freeze-end tick) / 64;
* ``opening_weapon`` = the demo's weapon name without the ``weapon_`` prefix
  (e.g. ``ak47``, ``m4a1_silencer``, ``knife_karambit``);
* ``map_name`` = the demo header map name (e.g. ``de_mirage``).

Values are stored as observed. Values the model doesn't know (new maps, other
knife skins, ...) get an unscored reason when the report is built; they are
never coerced into a known category.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .storage.base import RoundRecord

CS2_TICKRATE = 64
TEAM_NUM_TO_SIDE = {2: "t", 3: "ct", "2": "t", "3": "ct", "T": "t", "CT": "ct", "t": "t", "ct": "ct"}


@dataclass(frozen=True)
class ParsedRound:
    number: int
    freeze_end_tick: int | None
    end_tick: int
    winner_side: str | None


@dataclass(frozen=True)
class ParsedDeath:
    tick: int
    attacker_side: str | None
    victim_side: str | None
    weapon: str | None


@dataclass(frozen=True)
class ParsedDemo:
    map_name: str | None
    rounds: list[ParsedRound] = field(default_factory=list)
    deaths: list[ParsedDeath] = field(default_factory=list)
    tickrate: int = CS2_TICKRATE


class DemoParseError(Exception):
    pass


class DemoParser(ABC):
    @abstractmethod
    def parse(self, demo_path: str) -> ParsedDemo: ...


def normalize_weapon(weapon: str | None) -> str | None:
    if not weapon:
        return None
    weapon = weapon.strip().lower()
    return weapon[len("weapon_"):] if weapon.startswith("weapon_") else weapon


def extract_rounds(demo: ParsedDemo) -> list[RoundRecord]:
    records: list[RoundRecord] = []
    deaths = sorted(demo.deaths, key=lambda d: d.tick)
    previous_end = -1
    for rnd in sorted(demo.rounds, key=lambda r: r.end_tick):
        window_start = previous_end
        previous_end = rnd.end_tick
        in_round = [d for d in deaths if window_start < d.tick <= rnd.end_tick]
        if rnd.freeze_end_tick is not None:
            in_round = [d for d in in_round if d.tick >= rnd.freeze_end_tick]
        first = in_round[0] if in_round else None
        reason = None
        side = seconds = weapon = None
        if first is None:
            reason = "no_opening_kill"
        else:
            weapon = normalize_weapon(first.weapon)
            side = first.attacker_side
            if side is None and weapon == "world":
                side = first.victim_side
            if rnd.freeze_end_tick is None:
                reason = "freeze_end_missing"
            else:
                seconds = round((first.tick - rnd.freeze_end_tick) / demo.tickrate, 6)
                if seconds < 0:
                    reason = "opening_kill_before_freeze_end"
            if reason is None and side not in ("ct", "t"):
                reason = "opening_kill_side_unknown"
            if reason is None and not weapon:
                reason = "opening_weapon_unknown"
        if reason is None and rnd.winner_side not in ("ct", "t"):
            reason = "winner_unknown"
        records.append(RoundRecord(
            round_number=rnd.number,
            winner_side=rnd.winner_side if rnd.winner_side in ("ct", "t") else None,
            opening_kill_side=side if side in ("ct", "t") else None,
            opening_kill_seconds=seconds,
            opening_weapon=weapon,
            unscored_reason=reason,
        ))
    return records


class Demoparser2Parser(DemoParser):
    """Adapter for the pinned ``demoparser2`` package.

    TODO(verify-live): event/column names below follow demoparser2 0.42 docs and
    must be checked against a current matchmaking demo before production use.
    """

    def parse(self, demo_path: str) -> ParsedDemo:
        try:
            from demoparser2 import DemoParser as _Parser

            parser = _Parser(demo_path)
            header = parser.parse_header() or {}
            round_end = parser.parse_event("round_end")
            freeze_end = parser.parse_event("round_freeze_end")
            deaths = parser.parse_event("player_death", player=["team_num"])
        except Exception as exc:  # parser raises a variety of native errors
            raise DemoParseError("demo could not be parsed") from exc

        freeze_ticks = sorted(int(t) for t in _column(freeze_end, "tick"))
        rounds: list[ParsedRound] = []
        previous_end = -1
        for index, row in enumerate(_rows(round_end), start=1):
            end_tick = int(row["tick"])
            freezes = [t for t in freeze_ticks if previous_end < t <= end_tick]
            rounds.append(ParsedRound(
                number=index,
                freeze_end_tick=freezes[-1] if freezes else None,
                end_tick=end_tick,
                winner_side=TEAM_NUM_TO_SIDE.get(row.get("winner")),
            ))
            previous_end = end_tick
        return ParsedDemo(
            map_name=header.get("map_name") or None,
            rounds=rounds,
            deaths=[
                ParsedDeath(
                    tick=int(row["tick"]),
                    attacker_side=TEAM_NUM_TO_SIDE.get(row.get("attacker_team_num")),
                    victim_side=TEAM_NUM_TO_SIDE.get(row.get("user_team_num")),
                    weapon=row.get("weapon"),
                )
                for row in _rows(deaths)
            ],
        )


def _rows(frame) -> list[dict]:
    if frame is None or len(frame) == 0:
        return []
    records = frame.to_dict("records")
    for record in records:  # pandas gives floats for nullable ints
        for key in ("winner", "attacker_team_num", "user_team_num"):
            value = record.get(key)
            if isinstance(value, float) and value == value:
                record[key] = int(value)
    return records


def _column(frame, name: str) -> list:
    if frame is None or len(frame) == 0 or name not in frame:
        return []
    return list(frame[name])
