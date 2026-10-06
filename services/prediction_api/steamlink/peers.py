"""GET /matches/peers: the signed-in player compared with the other players in their own
imported matches ("lobby peers"), overall and per map. Matchmaking puts players of similar
skill in one lobby, so the others in your matches are the closest available peer group: the
server stores no ranks, so this is not a same-rank comparison and says so (``basis``).

Numbers per group are per player-match averages of: kills per round, K/D, survival rate,
opening-duel involvement / win rate and round win rate (from each player's own side), plus
your percentile among every peer player-match line (kills per round, K/D)."""

from __future__ import annotations

from collections import defaultdict

from .analytics import SIDES, _rate
from .storage.base import PlayerRoundRecord, Storage

MIN_PEER_LINES = 5


def _line(rounds: list[PlayerRoundRecord], winners: dict[int, str | None]) -> dict:
    kills = sum(r.kills for r in rounds)
    deaths = sum(r.deaths for r in rounds)
    decided = [r for r in rounds if winners.get(r.round_number) in SIDES]
    taken = sum(int(r.opening_kill) + int(r.opening_death) for r in rounds)
    return {
        "rounds": len(rounds), "kills": kills, "deaths": deaths, "survived": sum(int(r.survived) for r in rounds),
        "opening_kills": sum(int(r.opening_kill) for r in rounds), "opening_taken": taken,
        "decided": len(decided), "won": sum(1 for r in decided if winners[r.round_number] == r.side),
    }


def _group(lines: list[dict]) -> dict | None:
    if not lines:
        return None
    t = defaultdict(int)
    for line in lines:
        for k, v in line.items():
            t[k] += v
    return {
        "lines": len(lines), "rounds": t["rounds"],
        "kills_per_round": round(t["kills"] / t["rounds"], 2) if t["rounds"] else None,
        "kd": round(t["kills"] / t["deaths"], 2) if t["deaths"] else None,
        "survival_rate": _rate(t["survived"], t["rounds"]),
        "opening_attempt_rate": _rate(t["opening_taken"], t["rounds"]),
        "opening_win_rate": _rate(t["opening_kills"], t["opening_taken"]),
        "round_win_rate": _rate(t["won"], t["decided"]),
    }


def _kpr(line: dict) -> float:
    return line["kills"] / line["rounds"] if line["rounds"] else 0.0


def _kd(line: dict) -> float:
    return line["kills"] / line["deaths"] if line["deaths"] else float(line["kills"])


def _percentile(mine: list[dict], peers: list[dict], metric) -> int | None:
    """Share of peer lines your average beats (0..100); None without enough peers."""

    if len(peers) < MIN_PEER_LINES or not mine:
        return None
    you = metric({k: sum(line[k] for line in mine) for k in mine[0]})
    below = sum(1 for p in peers if metric(p) < you) + 0.5 * sum(1 for p in peers if metric(p) == you)
    return round(100 * below / len(peers))


def build_peer_comparison(storage: Storage, user_id: str, steam_id: str) -> dict:
    listed = [(m, rs) for m, rs in storage.list_matches_with_rounds(user_id) if m.status == "imported"]
    everyone = storage.list_all_player_rounds(user_id)
    mine_all, peers_all = [], []
    per_map: dict = defaultdict(lambda: ([], []))
    for match, rounds in listed:
        players: dict[str, list[PlayerRoundRecord]] = defaultdict(list)
        for record in everyone.get(match.id, []):
            players[record.steam_id].append(record)
        if steam_id not in players:
            continue  # only lobbies you played in
        winners = {r.round_number: r.winner_side for r in rounds}
        for sid, recs in players.items():
            line = _line(recs, winners)
            bucket = 0 if sid == steam_id else 1
            (mine_all, peers_all)[bucket].append(line)
            per_map[match.map_name][bucket].append(line)

    def compare(mine: list[dict], peers: list[dict]) -> dict:
        return {"you": _group(mine), "peers": _group(peers), "matches": len(mine),
                "percentiles": {"kills_per_round": _percentile(mine, peers, _kpr), "kd": _percentile(mine, peers, _kd)}}

    return {
        "basis": "lobby",  # the other players in your own imported matches (no ranks are stored)
        "min_peer_lines": MIN_PEER_LINES,
        "overall": compare(mine_all, peers_all),
        "maps": [
            {"map_name": name, **compare(m, p)}
            for name, (m, p) in sorted(per_map.items(), key=lambda kv: (-len(kv[1][0]), kv[0] or "~"))
        ],
    }
