"""Valve Rush (rush_001) thin-parse recovery after #22 PacketEntities soft-skip.

Noah's Rush GOTV demo emits 15 deaths / 4 freeze-ends / 3 round_officially_ended but only
1 round_end, and the first deaths lack team_num. These unit tests replay that shape without
needing demoparser2 or the .dem file.
"""

from __future__ import annotations

from steamlink.demo_parser import (
    ParsedDeath,
    ParsedDemo,
    ParsedSpawn,
    _recover_missing_sides,
    _rounds_from_events,
    extract_player_rounds,
    extract_rounds,
    final_score,
    match_rounds,
)


# SteamIDs from Noah's rush_001 upload (Hyphen325 = ME).
ME = "76561198088238031"
CT2 = "76561198121250399"
CT3 = "76561198250836452"
T1 = "76561198049004781"
T2 = "76561198192743898"
T3 = "76561198291389505"


def test_rounds_from_officially_ended_when_round_end_is_sparse():
    """Rush shape: 3 officially-ended, 1 round_end mid-match → 3 rounds, winners from scores."""

    freeze = [1345, 4248, 7121, 9438]  # trailing freeze has no officially-ended → dropped
    ended = [
        {"tick": 3544, "ct_team_rounds_total": 1, "t_team_rounds_total": 0},
        {"tick": 6417, "ct_team_rounds_total": 2, "t_team_rounds_total": 0},
        {"tick": 8734, "ct_team_rounds_total": 3, "t_team_rounds_total": 0},
    ]
    round_end = [{"tick": 6097, "winner": 3, "reason": "t_killed"}]  # only round 2

    rounds = _rounds_from_events(round_end, freeze, ended)
    assert [(r.number, r.freeze_end_tick, r.end_tick, r.winner_side, r.end_reason) for r in rounds] == [
        (1, 1345, 3544, "ct", None),
        (2, 4248, 6417, "ct", "t_killed"),  # reason from the in-window round_end
        (3, 7121, 8734, "ct", None),
    ]


def test_rounds_prefer_round_end_when_counts_match():
    """Competitive shape: equal round_end / officially_ended → keep round_end path."""

    freeze = [1000, 5000]
    round_end = [
        {"tick": 4000, "winner": 2, "reason": "ct_killed"},
        {"tick": 8000, "winner": 3, "reason": "bomb_defused"},
    ]
    ended = [
        {"tick": 4100, "ct_team_rounds_total": 0, "t_team_rounds_total": 1},
        {"tick": 8100, "ct_team_rounds_total": 1, "t_team_rounds_total": 1},
    ]
    rounds = _rounds_from_events(round_end, freeze, ended)
    assert [(r.end_tick, r.winner_side, r.end_reason) for r in rounds] == [
        (4000, "t", "ct_killed"),
        (8000, "ct", "bomb_defused"),
    ]


def test_recover_missing_death_and_spawn_sides_from_later_observations():
    """First deaths/spawns lack sides; later ones for the same SteamIDs carry them."""

    deaths = [
        ParsedDeath(1919, None, None, "usp_silencer", None, None, ME, T2),
        ParsedDeath(2161, None, None, "p250", None, None, CT2, T3),
        ParsedDeath(4979, "ct", "t", "mp7", 1, 0, ME, T1),
        ParsedDeath(4996, "ct", "t", "famas", 1, 0, CT2, T2),
    ]
    spawns = [
        (65, ME, None),
        (65, T2, None),
        (65, T3, None),
        (6417, ME, "ct"),
        (6417, T2, "t"),
        (6417, T3, "t"),
    ]
    fixed_deaths, fixed_spawns = _recover_missing_sides(deaths, spawns)
    assert [(d.attacker_side, d.victim_side) for d in fixed_deaths[:2]] == [("ct", "t"), ("ct", "t")]
    assert fixed_deaths[2].attacker_side == "ct"
    assert dict((sid, side) for _, sid, side in fixed_spawns)[ME] == "ct"
    assert dict((sid, side) for _, sid, side in fixed_spawns)[T2] == "t"


def test_rush_shaped_demo_yields_three_rounds_and_player_kd():
    """End-to-end extractors on a ParsedDemo shaped like Noah's Rush after recovery."""

    rounds = _rounds_from_events(
        [{"tick": 6097, "winner": 3, "reason": "t_killed"}],
        [1345, 4248, 7121, 9438],
        [
            {"tick": 3544, "ct_team_rounds_total": 1, "t_team_rounds_total": 0},
            {"tick": 6417, "ct_team_rounds_total": 2, "t_team_rounds_total": 0},
            {"tick": 8734, "ct_team_rounds_total": 3, "t_team_rounds_total": 0},
        ],
    )
    raw_deaths = [
        ParsedDeath(1919, None, None, "usp_silencer", None, None, ME, T2),
        ParsedDeath(2161, None, None, "p250", None, None, CT2, T3),
        ParsedDeath(2816, None, None, "glock", None, None, T1, CT3),
        ParsedDeath(3224, None, None, "p250", None, None, CT2, T1),
        ParsedDeath(4979, "ct", "t", "mp7", 1, 0, ME, T1),
        ParsedDeath(4996, "ct", "t", "famas", 1, 0, CT2, T2),
        ParsedDeath(6097, "ct", "t", "mp7", 1, 0, ME, T3),
        ParsedDeath(7515, "ct", "t", "galilar", 2, 0, ME, T2),
        ParsedDeath(7533, "t", "ct", "galilar", 0, 2, T3, ME),
        ParsedDeath(7653, "ct", "t", "famas", 2, 0, CT2, T3),
        ParsedDeath(7832, "t", "ct", "mp5sd", 0, 2, T1, CT2),
        ParsedDeath(8087, "ct", "t", "mp9", 2, 0, CT3, T1),
        # Round 4 deaths (after last officially-ended) stay outside match_rounds windows.
        ParsedDeath(10094, "ct", "t", "famas", 3, 0, CT3, T1),
        ParsedDeath(10789, "ct", "t", "m4a1", 3, 0, ME, T2),
        ParsedDeath(10929, "ct", "t", "m4a1", 3, 0, CT2, T3),
    ]
    spawn_sides = [
        (65, ME, None), (65, CT2, None), (65, CT3, None), (65, T1, None), (65, T2, None), (65, T3, None),
        (3544, ME, None), (3544, CT2, None), (3544, CT3, None), (3544, T1, None), (3544, T2, None), (3544, T3, None),
        (6417, ME, "ct"), (6417, CT2, "ct"), (6417, CT3, "ct"), (6417, T1, "t"), (6417, T2, "t"), (6417, T3, "t"),
        (8734, ME, "ct"), (8734, CT2, "ct"), (8734, CT3, "ct"), (8734, T1, "t"), (8734, T2, "t"), (8734, T3, "t"),
    ]
    deaths, spawn_sides = _recover_missing_sides(raw_deaths, spawn_sides)
    spawns = [ParsedSpawn(t, sid, side) for t, sid, side in spawn_sides if side in ("ct", "t")]
    demo = ParsedDemo(
        map_name="rush_001",
        rounds=rounds,
        deaths=deaths,
        spawns=spawns,
        match_start_tick=66,
        packet_ents_skips=9713,
    )

    assert len(match_rounds(demo)) == 3
    assert final_score(demo) == (3, 0)

    records = extract_rounds(demo)
    assert len(records) == 3
    assert all(r.winner_side == "ct" for r in records)
    # Round 1 opening kill recovered (ME usp → T2); map still unscored by the model elsewhere.
    assert records[0].opening_kill_side == "ct"
    assert records[0].opening_weapon == "usp_silencer"
    assert records[0].unscored_reason is None  # sides/weapon/winner all known

    players = extract_player_rounds(demo)
    by_me = [p for p in players if p.steam_id == ME]
    assert len(by_me) == 3
    assert sum(p.kills for p in by_me) >= 3  # rounds 1–3 kills; not the thin 2-from-1-round case
    assert sum(p.deaths for p in by_me) == 1  # died once in round 3
