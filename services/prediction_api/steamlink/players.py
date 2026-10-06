"""Everyone in one match (GET /matches/{id} ``players``): each player's line from the stored
per-player rounds (migration 0007), which team they were on relative to the signed-in player,
and how often the signed-in player has had them as a teammate / opponent in other matches of
their list. Only what the demo records: SteamIDs, sides, kills, deaths, opening duels and
survival (no names, ranks, damage or utility)."""

from __future__ import annotations

from .storage.base import PlayerRoundRecord, Storage

SIDES = ("ct", "t")


def _same_team(a: list[PlayerRoundRecord], b: list[PlayerRoundRecord]) -> bool | None:
    """Were two players on the same team? Compared on the first round both played."""

    sides = {r.round_number: r.side for r in b}
    for r in a:
        if r.round_number in sides:
            return r.side == sides[r.round_number]
    return None


def _line(rounds: list[PlayerRoundRecord]) -> dict:
    kills, deaths = sum(r.kills for r in rounds), sum(r.deaths for r in rounds)
    return {
        "rounds": len(rounds), "first_side": rounds[0].side if rounds else None,
        "kills": kills, "deaths": deaths,
        "kd": round(kills / deaths, 2) if deaths else (float(kills) if kills else None),
        "kills_per_round": round(kills / len(rounds), 2) if rounds else None,
        "opening_kills": sum(int(r.opening_kill) for r in rounds),
        "opening_deaths": sum(int(r.opening_death) for r in rounds),
        "survived": sum(int(r.survived) for r in rounds),
    }


def match_players(storage: Storage, user_id: str, match_id: str, steam_id: str | None) -> list[dict]:
    """Players of ``match_id`` (ownership already checked), most kills first. ``team``:
    ``you`` / ``teammate`` / ``opponent`` when the signed-in player is in the demo, else
    ``ct_start`` / ``t_start`` (the side each started on). ``history`` (signed-in player in
    the demo only): other matches of the user's list with this player on their team / the other."""

    players = storage.get_match_players(match_id)
    if not players:
        return []
    mine = players.get(steam_id) if steam_id else None
    history: dict[str, dict] = {}
    if mine:
        others = [sid for sid in players if sid != steam_id]
        seen = storage.list_players_in_matches(user_id, [steam_id, *others])
        for other_match, by_player in seen.items():
            me_there = by_player.get(steam_id)
            if other_match == match_id or not me_there:
                continue
            for sid in others:
                if sid not in by_player:
                    continue
                same = _same_team(me_there, by_player[sid])
                if same is None:
                    continue
                entry = history.setdefault(sid, {"matches_with": 0, "matches_against": 0})
                entry["matches_with" if same else "matches_against"] += 1
    out = []
    for sid, rounds in players.items():
        if mine:
            team = "you" if sid == steam_id else ("teammate" if _same_team(mine, rounds) else "opponent")
        else:
            team = f"{rounds[0].side}_start"
        line = {"steam_id": sid, "team": team, **_line(rounds)}
        if mine and sid != steam_id:
            line["history"] = history.get(sid, {"matches_with": 0, "matches_against": 0})
        out.append(line)
    out.sort(key=lambda p: (-p["kills"], p["deaths"], p["steam_id"]))
    return out
