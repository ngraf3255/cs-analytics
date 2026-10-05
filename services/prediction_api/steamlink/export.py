"""Tableau-ready CSV export of a user's matches and rounds.

Two tables, the same from the API (``GET /matches/export/rounds.csv`` and
``GET /matches/export/matches.csv``, signed-in user only) and offline from the
app database (``python -m steamlink.export --database-url ... --steam-id ...``,
see ``python/export_app_tableau.py``):

* ``rounds.csv``: one row per stored round of every imported match in the
  user's list (uploads, Steam sync and shared matches);
* ``matches.csv``: one row per match in the user's list (also matches whose
  demo could not be imported: ``status`` says which), with summary metrics.

Column names are stable (``ROUND_COLUMNS`` / ``MATCH_COLUMNS``; documented in
``tableau/README.md``). Conventions, chosen so Tableau infers the types:

* dates are ISO 8601 UTC to the second (``2026-10-05T04:12:00Z``); ``match_date``
  is when the match was played if known (Steam sync, from Valve) else when it
  was added (``date_source`` = ``played`` | ``imported``; demos carry no date),
  ``match_day`` the same as a plain ``YYYY-MM-DD`` (UTC) date;
* flags are ``1`` / ``0`` (so ``AVG([ct_won])`` is the CT round win rate);
  empty cells mean unknown / not applicable;
* sides are ``ct`` / ``t``; probabilities are 0..1;
* ``you_*`` columns are the user's own side / stats (their SteamID64's
  per-player rounds); empty when the user is not in the match (e.g. an
  uploaded pro demo), was not tracked that round, or the match was parsed
  before per-player rounds were recorded (``you_status`` says which), and
  always empty when no SteamID is given (offline export without ``--steam-id``).

Warmup / knife rounds are not stored by the parser, so they are not exported.
Matches parsed by an older parser are exported as stored and flagged
(``needs_reupload`` = 1, ``outdated_reason``); re-uploading the demo updates them.

The model columns are the same retrospective estimate as the match report: the
round-win model (trained on pro matches) given the map and the round's opening
kill (side, time, weapon); not a live pre-round prediction.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from datetime import datetime, timezone

from .analytics import SIDES, match_result
from .scoring import RoundScorer
from .storage.base import MatchRecord, PlayerRoundRecord, RoundRecord, Storage

EXPORT_VERSION = 1  # bump when a column changes meaning; new columns are only appended

MATCH_KEY_COLUMNS = (
    "match_id", "map_name", "match_date", "match_day", "date_source", "played_at", "imported_at", "source",
)
ROUND_COLUMNS = MATCH_KEY_COLUMNS + (
    "round_number",
    "winner_side",            # ct | t | empty (unknown)
    "ct_won",                 # 1 CT won the round, 0 T won, empty unknown
    "opening_kill_side",      # side that got the round's first kill
    "opening_kill_seconds",   # seconds after freeze time ended
    "opening_weapon",
    "opening_kill_side_won",  # 1 the side with the opening kill won the round
    "model_ct_win_probability",  # model P(CT wins the round), empty when not scored
    "model_predicted_winner",
    "model_correct",          # 1 the model's favourite won
    "unscored_reason",        # why the model did not score the round
    "match_score_ct",         # final score (team on CT / T at the end); same on every row
    "match_score_t",
    "needs_reupload",         # 1: parsed by an older parser (see outdated_reason)
    "you_side",
    "you_won",
    "you_kills",
    "you_deaths",
    "you_survived",
    "you_opening_kill",       # 1 you got the round's first kill
    "you_opening_death",      # 1 you died first
    "you_win_probability",    # model P(your side wins the round)
    "you_score_after",        # your team's score after this round (empty after a round you were not tracked)
    "opponent_score_after",
)
MATCH_COLUMNS = MATCH_KEY_COLUMNS + (
    "status",                 # imported | unavailable | parse_failed
    "status_reason",
    "needs_reupload",
    "outdated_reason",        # players_not_recorded | parser_updated | empty
    "rounds",                 # rounds stored (warmup / knife rounds are not)
    "match_score_ct",
    "match_score_t",
    "rounds_with_winner",
    "ct_rounds_won",
    "t_rounds_won",
    "ct_round_win_rate",
    "opening_kill_rounds",
    "opening_kill_converted",
    "opening_kill_conversion_rate",
    "model_scored_rounds",
    "model_correct",
    "model_hit_rate",
    "model_brier_score",
    "you_status",             # in_match | not_in_match | unknown (parsed before players were recorded) | empty
    "you_first_side",
    "you_last_side",
    "you_rounds",
    "you_rounds_won",
    "you_round_win_rate",
    "you_kills",
    "you_deaths",
    "you_kd",
    "you_opening_kills",
    "you_opening_deaths",
    "you_survived",
    "you_score",              # your team's final score
    "opponent_score",
    "you_result",             # won | lost | tied
)

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    """CSV text: '' for None, 1/0 for bools, rounded floats; text that a spreadsheet
    would run as a formula (map / weapon names come from uploaded demos) gets a leading '."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return repr(round(value, 6))
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _rate(part, whole):
    return round(part / whole, 4) if whole else None


def iso(value: datetime | None) -> str | None:
    """ISO 8601 UTC to the second, e.g. 2026-10-05T04:12:00Z."""

    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _match_keys(match: MatchRecord) -> dict:
    when = match.played_at or match.imported_at
    return {
        "match_id": match.id, "map_name": match.map_name,
        "match_date": iso(when),
        "match_day": when.astimezone(timezone.utc).strftime("%Y-%m-%d"),  # Tableau reads it as a date as is
        "date_source": "played" if match.played_at else "imported",
        "played_at": iso(match.played_at), "imported_at": iso(match.imported_at), "source": match.source,
    }


def _load(storage: Storage, user_id: str, steam_id: str | None):
    listed = storage.list_matches_with_rounds(user_id)  # newest added first
    listed.sort(key=lambda item: item[0].played_at or item[0].imported_at, reverse=True)
    mine = storage.list_player_rounds(user_id, steam_id) if steam_id else {}
    return listed, mine


def _round_rows(match: MatchRecord, rounds: list[RoundRecord], scorer: RoundScorer,
                mine: list[PlayerRoundRecord]) -> Iterator[dict]:
    keys = _match_keys(match)
    by_round = {r.round_number: r for r in mine}
    you = them = 0
    tracking = bool(mine)  # running score only while every round so far had the player's side
    for rnd, score in zip(rounds, scorer.score_rounds(match.map_name, rounds)):
        known = rnd.winner_side in SIDES
        scored = score.unscored_reason is None and score.probability_ct is not None
        record = by_round.get(rnd.round_number)
        row = {
            **keys,
            "round_number": rnd.round_number,
            "winner_side": rnd.winner_side if known else None,
            "ct_won": (rnd.winner_side == "ct") if known else None,
            "opening_kill_side": rnd.opening_kill_side,
            "opening_kill_seconds": rnd.opening_kill_seconds,
            "opening_weapon": rnd.opening_weapon,
            "opening_kill_side_won": (rnd.opening_kill_side == rnd.winner_side)
            if known and rnd.opening_kill_side in SIDES else None,
            "model_ct_win_probability": score.probability_ct if scored else None,
            "model_predicted_winner": score.predicted_winner if scored else None,
            "model_correct": (score.predicted_winner == rnd.winner_side) if scored and known else None,
            "unscored_reason": score.unscored_reason,
            "match_score_ct": match.score_ct, "match_score_t": match.score_t,
            "needs_reupload": match.outdated_reason is not None,
        }
        if record is not None and record.side in SIDES:
            if tracking and known:
                you += int(rnd.winner_side == record.side)
                them += int(rnd.winner_side != record.side)
            elif not known:
                tracking = False
            row.update({
                "you_side": record.side,
                "you_won": (rnd.winner_side == record.side) if known else None,
                "you_kills": record.kills, "you_deaths": record.deaths, "you_survived": record.survived,
                "you_opening_kill": record.opening_kill, "you_opening_death": record.opening_death,
                "you_win_probability": None if not scored else
                (score.probability_ct if record.side == "ct" else score.probability_t),
                "you_score_after": you if tracking else None,
                "opponent_score_after": them if tracking else None,
            })
        else:
            tracking = False
        yield row


def _match_row(match: MatchRecord, rounds: list[RoundRecord], scorer: RoundScorer,
               mine: list[PlayerRoundRecord], steam_id: str | None) -> dict:
    won = {"ct": 0, "t": 0}
    opening = converted = scored = correct = 0
    brier = 0.0
    winners = {}
    for rnd, score in zip(rounds, scorer.score_rounds(match.map_name, rounds)):
        if rnd.winner_side not in SIDES:
            continue
        winners[rnd.round_number] = rnd.winner_side
        won[rnd.winner_side] += 1
        if rnd.opening_kill_side in SIDES:
            opening += 1
            converted += int(rnd.opening_kill_side == rnd.winner_side)
        if score.unscored_reason is None and score.probability_ct is not None:
            scored += 1
            correct += int(score.predicted_winner == rnd.winner_side)
            brier += (score.probability_ct - (1.0 if rnd.winner_side == "ct" else 0.0)) ** 2
    with_winner = won["ct"] + won["t"]
    row = {
        **_match_keys(match),
        "status": match.status, "status_reason": match.status_reason,
        "needs_reupload": match.outdated_reason is not None, "outdated_reason": match.outdated_reason,
        "rounds": len(rounds), "match_score_ct": match.score_ct, "match_score_t": match.score_t,
        "rounds_with_winner": with_winner, "ct_rounds_won": won["ct"], "t_rounds_won": won["t"],
        "ct_round_win_rate": _rate(won["ct"], with_winner),
        "opening_kill_rounds": opening, "opening_kill_converted": converted,
        "opening_kill_conversion_rate": _rate(converted, opening),
        "model_scored_rounds": scored, "model_correct": correct, "model_hit_rate": _rate(correct, scored),
        "model_brier_score": round(brier / scored, 4) if scored else None,
    }
    if steam_id is None or match.status != "imported":
        return row
    if not mine:
        row["you_status"] = "not_in_match" if match.players_recorded else "unknown"
        return row
    decided = [r for r in mine if r.round_number in winners]
    you_won = sum(1 for r in decided if winners[r.round_number] == r.side)
    kills, deaths = sum(r.kills for r in mine), sum(r.deaths for r in mine)
    result = match_result(match, mine)
    row.update({
        "you_status": "in_match", "you_first_side": mine[0].side, "you_last_side": mine[-1].side,
        "you_rounds": len(mine), "you_rounds_won": you_won, "you_round_win_rate": _rate(you_won, len(decided)),
        "you_kills": kills, "you_deaths": deaths,
        "you_kd": round(kills / deaths, 2) if deaths else (float(kills) if kills else None),
        "you_opening_kills": sum(r.opening_kill for r in mine),
        "you_opening_deaths": sum(r.opening_death for r in mine),
        "you_survived": sum(r.survived for r in mine),
        "you_score": result["score"]["you"] if result["score"] else None,
        "opponent_score": result["score"]["them"] if result["score"] else None,
        "you_result": result["result"],
    })
    return row


def round_rows(storage: Storage, scorer: RoundScorer, user_id: str, steam_id: str | None) -> Iterator[dict]:
    """``rounds.csv`` rows: imported matches only, newest match first, rounds in order."""

    listed, mine = _load(storage, user_id, steam_id)  # now, so a database error is raised before streaming
    return (row for match, rounds in listed if match.status == "imported"
            for row in _round_rows(match, rounds, scorer, mine.get(match.id, [])))


def match_rows(storage: Storage, scorer: RoundScorer, user_id: str, steam_id: str | None) -> Iterator[dict]:
    """``matches.csv`` rows: every match in the user's list, newest first."""

    listed, mine = _load(storage, user_id, steam_id)
    return (_match_row(match, rounds, scorer, mine.get(match.id, []), steam_id) for match, rounds in listed)


def csv_chunks(columns: tuple[str, ...], rows: Iterable[dict], *, batch: int = 500) -> Iterator[str]:
    """The CSV text (header first) in chunks of ``batch`` rows, for a streaming response."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    n = 0
    for row in rows:
        writer.writerow([_cell(row.get(name)) for name in columns])
        n += 1
        if n % batch == 0:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
    yield buffer.getvalue()


TABLES = {
    "rounds": (ROUND_COLUMNS, round_rows),
    "matches": (MATCH_COLUMNS, match_rows),
}


def write_tables(storage: Storage, scorer: RoundScorer, user_id: str, steam_id: str | None,
                 out_dir: Path, *, prefix: str = "app_") -> dict[str, tuple[Path, int]]:
    """Write ``<prefix>rounds.csv`` and ``<prefix>matches.csv`` to ``out_dir``: {table: (path, rows)}."""

    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, (columns, rows_fn) in TABLES.items():
        rows = list(rows_fn(storage, scorer, user_id, steam_id))
        path = out_dir / f"{prefix}{name}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            for chunk in csv_chunks(columns, rows):
                fh.write(chunk)
        written[name] = (path, len(rows))
    return written


def load_scorer(model_path: Path) -> RoundScorer:
    import joblib

    saved = joblib.load(model_path)
    return RoundScorer(saved["model"], [str(v) for v in saved["map_options"]],
                       [str(v) for v in saved["weapon_options"]])


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[3]  # repository root (model.pkl, tableau/)
    p = argparse.ArgumentParser(
        prog="python -m steamlink.export",
        description="Export one user's matches and rounds from the app database as Tableau-ready CSVs "
                    "(the same tables as GET /matches/export/*.csv). Read-only: nothing is migrated or written "
                    "to the database.")
    p.add_argument("--database-url", required=True,
                   help="the app database, e.g. postgresql://user:pass@host/db or sqlite:///path/local.db")
    p.add_argument("--steam-id", required=True,
                   help="SteamID64 of the user whose match list is exported; also fills the you_* columns")
    p.add_argument("--no-personal", action="store_true", help="leave the you_* columns empty")
    p.add_argument("--out-dir", default=str(root / "tableau" / "app"), help="output folder (default: tableau/app)")
    p.add_argument("--prefix", default="app_", help="file name prefix (default app_ -> app_rounds.csv, app_matches.csv)")
    p.add_argument("--model", default=str(root / "model.pkl"), help="model artifact (default: <repo>/model.pkl)")
    args = p.parse_args(argv)

    from .storage.sql import SqlStorage, make_engine

    storage = SqlStorage(make_engine(args.database_url))
    user = storage.find_user(args.steam_id)
    if user is None:
        print(f"No user with SteamID64 {args.steam_id} in this database (they sign in to the app first).",
              file=sys.stderr)
        return 2
    written = write_tables(storage, load_scorer(Path(args.model)), user.id,
                           None if args.no_personal else user.steam_id, Path(args.out_dir), prefix=args.prefix)
    for name, (path, n) in written.items():
        print(f"{name}: {n} rows -> {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
