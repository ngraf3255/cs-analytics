"""Match detail (PARSE_VERSION 3, migration 0010): round end reasons, equipment value at
freeze end (each team's buy) and clutches, in the parse and in GET /matches/{id}.
Runs on SQLite by default and on PostgreSQL with CSA_TEST_DATABASE_URL."""

from dataclasses import replace

import pytest

from steamlink.demo_parser import (
    ParsedDeath, ParsedDemo, ParsedEquip, ParsedRound, ParsedSpawn, extract_player_rounds, extract_rounds,
    normalize_reason, parsed_demo_from_json, parsed_demo_to_json, round_outcome,
)
from steamlink.match_detail import buy_type, economy_by_round
from steamlink.storage.base import PARSE_VERSION, PlayerRoundRecord, RoundRecord

from test_personal import ME, THEM, app_client, rnd, store  # noqa: F401  (fixture)

A1, A2, A3, B1, B2, B3 = (f"7656119800000010{n}" for n in range(1, 7))


def three_v_three(rounds, deaths, equipment=()):
    spawns = [ParsedSpawn(r.freeze_end_tick - 10, sid, side) for r in rounds
              for sid, side in ((A1, "t"), (A2, "t"), (A3, "t"), (B1, "ct"), (B2, "ct"), (B3, "ct"))]
    return ParsedDemo("de_mirage", rounds, deaths, spawns=spawns, equipment=list(equipment))


def test_clutch_is_the_enemy_count_when_a_player_becomes_the_last_alive():
    rounds = [ParsedRound(1, 1000, 5000, "ct", "t_killed")]
    deaths = [ParsedDeath(2000, "t", "ct", "ak47", 0, 0, A1, B1),
              ParsedDeath(2100, "t", "ct", "ak47", 0, 0, A1, B2),  # B3 alone vs 3
              ParsedDeath(2200, "ct", "t", "m4a1", 0, 0, B3, A1),
              ParsedDeath(2300, "ct", "t", "m4a1", 0, 0, B3, A2),  # A3 alone vs 1 (B3 keeps 3)
              ParsedDeath(2400, "ct", "t", "m4a1", 0, 0, B3, A3)]
    records = {r.steam_id: r for r in extract_player_rounds(three_v_three(rounds, deaths))}
    assert {sid: r.clutch_vs for sid, r in records.items()} == {A1: 0, A2: 0, A3: 1, B1: 0, B2: 0, B3: 3}


def test_a_side_that_starts_alone_is_not_a_clutch_and_deaths_after_round_end_do_not_count():
    rounds = [ParsedRound(1, 1000, 5000, "t", "bomb_exploded")]
    spawns = [ParsedSpawn(900, A1, "t"), ParsedSpawn(900, B1, "ct"), ParsedSpawn(900, B2, "ct")]
    deaths = [ParsedDeath(2000, "t", "ct", "ak47", 0, 0, A1, B1),  # B2 alone vs 1
              ParsedDeath(5100, "t", "ct", "ak47", 1, 0, A1, B2)]  # exit frag: not part of the round
    records = {r.steam_id: r for r in extract_player_rounds(ParsedDemo("de_nuke", rounds, deaths, spawns=spawns))}
    assert (records[A1].clutch_vs, records[B1].clutch_vs, records[B2].clutch_vs) == (0, 0, 1)


def test_equipment_value_is_read_at_the_rounds_freeze_end():
    rounds = [ParsedRound(1, 1000, 5000, "t"), ParsedRound(2, 6000, 9000, "ct")]
    equipment = [ParsedEquip(1000, A1, 850), ParsedEquip(6000, A1, 4700), ParsedEquip(6000, B1, 5100),
                 ParsedEquip(3000, B1, 9999)]  # not a freeze end: ignored
    records = extract_player_rounds(three_v_three(rounds, [], equipment))
    values = {(r.round_number, r.steam_id): r.equip_value for r in records}
    assert (values[(1, A1)], values[(2, A1)], values[(1, B1)], values[(2, B1)]) == (850, 4700, None, 5100)
    # A parse without equipment values (e.g. parse_ticks failed) records None, not 0.
    assert {r.equip_value for r in extract_player_rounds(three_v_three(rounds, []))} == {None}


@pytest.mark.parametrize("raw, name, outcome", [
    ("t_killed", "t_killed", "elimination"), ("CT_Killed", "ct_killed", "elimination"),
    ("bomb_exploded", "bomb_exploded", "bomb"), ("bomb_defused", "bomb_defused", "defuse"),
    ("target_saved", "target_saved", "time"), ("time_ran_out", "time_ran_out", "time"),
    ("t_surrender", "t_surrender", "surrender"), ("something_new", "something_new", None),
    # The demoparser2 fixture gives the game's RoundEndReason number instead.
    (9, "ct_killed", "elimination"), (7, "bomb_defused", "defuse"), (9.0, "ct_killed", "elimination"),
    (99, None, None), (float("nan"), None, None), (None, None, None), ("", None, None),
])
def test_round_end_reasons(raw, name, outcome):
    assert normalize_reason(raw) == name
    assert round_outcome(name) == outcome


def test_rounds_keep_their_end_reason_and_the_worker_json_round_trips():
    rounds = [ParsedRound(1, 1000, 5000, "ct", "bomb_defused"), ParsedRound(2, 6000, 9000, "t", None)]
    demo = three_v_three(rounds, [ParsedDeath(2000, "t", "ct", "ak47", 0, 0, A1, B1)], [ParsedEquip(1000, A1, 800)])
    assert [r.end_reason for r in extract_rounds(demo)] == ["bomb_defused", None]
    assert parsed_demo_from_json(parsed_demo_to_json(demo)) == demo
    # Output of an older worker (rounds without a reason, no equipment) still loads.
    old = parsed_demo_to_json(demo)
    old["rounds"] = [r[:4] for r in old["rounds"]]
    del old["equipment"]
    loaded = parsed_demo_from_json(old)
    assert [r.end_reason for r in loaded.rounds] == [None, None] and loaded.equipment == []


@pytest.mark.parametrize("average, first, expected", [
    (850, True, "pistol"), (850, False, "eco"), (1499, False, "eco"), (1500, False, "force"),
    (3499, True, "force"), (3500, False, "full"), (5600, True, "full"),  # overtime: full money, not pistol
])
def test_buy_type(average, first, expected):
    assert buy_type(average, first_of_half=first) == expected


def pr(n, sid, side, *, equip=None, clutch=None, **kw):
    return PlayerRoundRecord(n, sid, side, equip_value=equip, clutch_vs=clutch, **kw)


def test_economy_per_round_with_pistol_rounds_at_the_start_and_after_the_side_switch():
    rounds = [rnd(n, "t") for n in (1, 2, 3, 4)]
    everyone = [
        pr(1, A1, "t", equip=800), pr(1, A2, "t", equip=900), pr(1, B1, "ct", equip=850), pr(1, B2, "ct", equip=750),
        pr(2, A1, "t", equip=4800), pr(2, A2, "t", equip=3000), pr(2, B1, "ct", equip=300), pr(2, B2, "ct", equip=500),
        # halftime: sides switch, money resets
        pr(3, A1, "ct", equip=850), pr(3, A2, "ct", equip=850), pr(3, B1, "t", equip=800), pr(3, B2, "t", equip=800),
        pr(4, A1, "ct", equip=2000), pr(4, A2, "ct", equip=2400), pr(4, B1, "t"), pr(4, B2, "t"),  # T not recorded
    ]
    economy = economy_by_round(rounds, everyone)
    assert economy[1] == {"ct": {"buy": "pistol", "equip_value": 800}, "t": {"buy": "pistol", "equip_value": 850}}
    assert economy[2] == {"ct": {"buy": "eco", "equip_value": 400}, "t": {"buy": "full", "equip_value": 3900}}
    assert (economy[3]["ct"]["buy"], economy[3]["t"]["buy"]) == ("pistol", "pistol")
    assert economy[4] is None


def detail_match(ctx, key="d" * 64):
    """Mirage, 3 rounds; ME on T (rounds 1-2) then CT. Round 2: ME alone vs 2, team won."""

    rounds = [replace(rnd(1, "ct"), end_reason="t_killed"), replace(rnd(2, "t"), end_reason="bomb_exploded"),
              replace(rnd(3, "ct"), end_reason="target_saved")]
    players = [
        pr(1, ME, "t", equip=800, clutch=0, opening_death=True, deaths=1, survived=False),
        pr(1, THEM, "ct", equip=850, clutch=1, opening_kill=True, kills=1),
        pr(2, ME, "t", equip=4300, clutch=2, opening_kill=True, kills=2),
        pr(2, THEM, "ct", equip=600, clutch=0, deaths=1, survived=False),
        pr(3, ME, "ct", equip=850, clutch=1, deaths=1, survived=False),
        pr(3, THEM, "t", equip=900, clutch=0),
    ]
    return store(ctx, ME, key, "de_mirage", rounds, players, score=(2, 1))


def test_match_report_has_round_ends_economy_and_your_clutches(app_client):
    client, ctx = app_client
    report = client.get(f"/matches/{detail_match(ctx)}").json()
    assert report["match"]["detail_recorded"] is True and report["match"]["outdated"] is None
    rounds = report["rounds"]
    assert [r["end"] for r in rounds] == [{"reason": "t_killed", "outcome": "elimination"},
                                          {"reason": "bomb_exploded", "outcome": "bomb"},
                                          {"reason": "target_saved", "outcome": "time"}]
    assert rounds[0]["economy"] == {"ct": {"buy": "pistol", "equip_value": 850},
                                    "t": {"buy": "pistol", "equip_value": 800}}
    assert rounds[1]["economy"]["t"] == {"buy": "full", "equip_value": 4300}
    assert rounds[1]["economy"]["ct"]["buy"] == "eco"
    assert rounds[2]["economy"]["ct"]["buy"] == "pistol"  # sides switched: second-half pistol
    assert [r["you"]["equip_value"] for r in rounds] == [800, 4300, 850]
    assert [r["you"]["clutch"] for r in rounds] == [None, {"vs": 2, "won": True}, {"vs": 1, "won": True}]
    you = report["you"]
    assert you["opening_duels"] == {"taken": 2, "won": 1, "lost": 1, "win_rate": 0.5}
    assert you["clutches"] == {"attempts": 2, "won": 2, "win_rate": 1.0,
                               "by_size": [{"vs": 1, "attempts": 1, "won": 1}, {"vs": 2, "attempts": 1, "won": 1}]}
    assert you["buys"]["pistol"] == {"rounds": 2, "won": 1, "win_rate": 0.5}
    assert you["buys"]["full"] == {"rounds": 1, "won": 1, "win_rate": 1.0}
    assert you["buys"]["eco"] == {"rounds": 0, "won": 0, "win_rate": None}


def test_a_match_parsed_before_match_detail_is_not_outdated_but_reupload_fills_it_in(app_client):
    client, ctx = app_client
    owner = ctx.storage.get_or_create_user(ME, ctx.clock())
    old_id = store(ctx, ME, "e" * 64, "de_mirage", [rnd(1, "ct")], [pr(1, ME, "t", deaths=1, survived=False)])
    # As stored by the previous parser: no end reasons / equipment / clutches, parse_version 2.
    from sqlalchemy import update

    from steamlink.storage.sql import matches

    with ctx.storage.engine.begin() as conn:
        conn.execute(update(matches).where(matches.c.id == old_id).values(parse_version=2))
    record = ctx.storage.get_match(owner.id, old_id)[0]
    assert (record.outdated_reason, record.detail_recorded, record.reparse_on_upload) == (None, False, True)
    report = client.get(f"/matches/{old_id}").json()
    assert report["match"]["outdated"] is None and report["match"]["detail_recorded"] is False
    assert report["rounds"][0]["end"] is None and report["rounds"][0]["economy"] is None
    assert report["rounds"][0]["you"]["clutch"] is None and report["you"]["clutches"] is None
    assert report["you"]["buys"] is None
    # The same demo parsed again (current parser) replaces it with the detail.
    assert detail_match(ctx, key="e" * 64) == old_id
    record = ctx.storage.get_match(owner.id, old_id)[0]
    assert (record.parse_version, record.detail_recorded, record.reparse_on_upload) == (PARSE_VERSION, True, False)
    assert client.get(f"/matches/{old_id}").json()["rounds"][1]["end"]["outcome"] == "bomb"


def test_round_record_defaults_keep_older_callers_working():
    assert RoundRecord(1, "t", "t", 1.0, "ak47", None).end_reason is None
    assert PlayerRoundRecord(1, A1, "t").clutch_vs is None
