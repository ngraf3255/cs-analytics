"""Per-player rounds: which side each player (SteamID64) was on every round and their
kills / deaths / opening duels / survival (demo_parser.extract_player_rounds), stored per
player so every owner of a shared match sees their own side (migration 0007), plus the
match date from the Game Coordinator. Runs on SQLite and on PostgreSQL (CSA_TEST_DATABASE_URL)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from steamlink.demo_parser import (
    ParsedDeath, ParsedDemo, ParsedRound, ParsedSpawn, extract_player_rounds, normalize_steamid,
)
from steamlink.gc import GameCoordinator, GameCoordinatorDemoLocator, GCMatch
from steamlink.migrate import apply_migrations
from steamlink.sharecode import ShareCode
from steamlink.storage.base import PARSE_VERSION, UNKNOWN_MATCH_ID, NewMatch, PlayerRoundRecord, RoundRecord
from steamlink.storage.sql import SqlStorage
from steamlink.sync import _played_at

from dbutil import make_test_engine
from fakes import FAKE_OPPONENT, FAKE_PLAYER, code, make_storage
from test_sync import env, match_id_of  # noqa: F401  (fixture)

A1, A2, B1, B2 = "76561198000000101", "76561198000000102", "76561198000000201", "76561198000000202"
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


def by_round(records, steam_id):
    return {r.round_number: r for r in records if r.steam_id == steam_id}


def test_sides_follow_spawns_and_kills_including_a_halftime_swap_without_respawn():
    rounds = [ParsedRound(1, 1000, 5000, "t"), ParsedRound(2, 6000, 9000, "ct"), ParsedRound(3, 10000, 14000, "t")]
    spawns = [ParsedSpawn(100, A1, "t"), ParsedSpawn(100, A2, "t"), ParsedSpawn(100, B1, "ct"), ParsedSpawn(100, B2, "ct"),
              # round 2: everyone respawns on the same sides
              ParsedSpawn(5200, A1, "t"), ParsedSpawn(5200, A2, "t"), ParsedSpawn(5200, B1, "ct"), ParsedSpawn(5200, B2, "ct"),
              # round 3 (after halftime): the spawn events still carry the old sides, the kills show the swap
              ParsedSpawn(9200, A1, "t"), ParsedSpawn(9200, A2, "t"), ParsedSpawn(9200, B1, "ct"), ParsedSpawn(9200, B2, "ct")]
    deaths = [ParsedDeath(2000, "t", "ct", "ak47", 0, 0, A1, B1),
              ParsedDeath(7000, "ct", "t", "m4a1", 0, 1, B2, A2),
              ParsedDeath(11000, "t", "ct", "ak47", 1, 1, B1, A1)]  # B1 now T, A1 now CT
    records = extract_player_rounds(ParsedDemo("de_mirage", rounds, deaths, spawns=spawns))
    assert [by_round(records, A1)[n].side for n in (1, 2, 3)] == ["t", "t", "ct"]
    assert [by_round(records, B2)[n].side for n in (1, 2, 3)] == ["ct", "ct", "t"]  # no kill in round 3: swapped spawn
    assert [by_round(records, A2)[n].side for n in (1, 2, 3)] == ["t", "t", "ct"]
    assert len(records) == 12


def test_kills_deaths_opening_duels_and_survival():
    rounds = [ParsedRound(1, 1000, 5000, "t"), ParsedRound(2, 6000, 9000, "ct")]
    spawns = [ParsedSpawn(100, s, side) for s, side in ((A1, "t"), (A2, "t"), (B1, "ct"), (B2, "ct"))]
    spawns += [ParsedSpawn(5200, s.steamid, s.side) for s in spawns]
    deaths = [ParsedDeath(800, "t", "ct", "ak47", 0, 0, A1, B1),  # before freeze end: not counted
              ParsedDeath(2000, "t", "ct", "ak47", 0, 0, A1, B1),  # opening kill A1 -> B1
              ParsedDeath(2100, "t", "t", "ak47", 0, 0, A1, A2),  # teamkill: a death for A2, no kill for A1
              ParsedDeath(2200, None, "ct", "world", None, 0, None, B2),  # fall damage
              ParsedDeath(5100, "t", "ct", "ak47", 1, 0, A1, B2),  # exit frag after round 1 ended
              ParsedDeath(7000, "ct", "ct", "hegrenade", 0, 0, B1, B1),  # suicide opens round 2
              ParsedDeath(9000, "ct", "t", "m4a1", 1, 1, B2, A1),  # on the round_end tick: still in round 2
              ParsedDeath(9500, "t", "ct", "ak47", 1, 2, A2, B2)]  # after the LAST round: post-match, ignored
    records = extract_player_rounds(ParsedDemo("de_nuke", rounds, deaths, spawns=spawns))
    r1, r2 = by_round(records, A1)[1], by_round(records, A1)[2]
    # clutch_vs: B1's opening death leaves B2 alone vs 2; the teamkill then leaves A1 alone vs 1.
    assert r1 == PlayerRoundRecord(1, A1, "t", kills=2, deaths=0, opening_kill=True, opening_death=False, survived=True,
                                   clutch_vs=1)
    assert r2 == PlayerRoundRecord(2, A1, "t", kills=0, deaths=1, opening_kill=False, opening_death=False,
                                   survived=False, clutch_vs=0)
    assert by_round(records, A2)[1] == PlayerRoundRecord(1, A2, "t", 0, 1, False, False, False, clutch_vs=0)
    assert by_round(records, B1)[1] == PlayerRoundRecord(1, B1, "ct", 0, 1, False, True, False, clutch_vs=0)
    assert by_round(records, B1)[2] == PlayerRoundRecord(2, B1, "ct", 0, 1, False, True, False,  # suicide: no kill
                                                         clutch_vs=0)
    assert by_round(records, B2)[1] == PlayerRoundRecord(1, B2, "ct", 0, 2, False, False, False,  # world + exit frag
                                                         clutch_vs=2)
    assert by_round(records, B2)[2] == PlayerRoundRecord(2, B2, "ct", 1, 0, False, False, True, clutch_vs=2)
    assert by_round(records, A2)[2].clutch_vs == 1  # B2 kills A1 on the round_end tick: A2 alone vs 1


def test_rounds_before_the_last_match_restart_and_unknown_players_have_no_records():
    rounds = [ParsedRound(1, 100, 900, "t"), ParsedRound(2, 1500, 3000, "ct")]
    deaths = [ParsedDeath(500, "t", "ct", "knife", 0, 0, A1, B1),  # knife round, then begin_new_match
              ParsedDeath(2000, "ct", "t", "m4a1", 0, 0, B1, A1),
              ParsedDeath(2100, "ct", "t", "m4a1", 0, 0, None, None)]  # bot / unknown players: nothing
    records = extract_player_rounds(ParsedDemo("de_mirage", rounds, deaths, match_start_tick=1000))
    # The knife round is dropped and the match's first round is round 1 (like extract_rounds).
    assert {(r.round_number, r.steam_id, r.side) for r in records} == {(1, A1, "t"), (1, B1, "ct")}


def test_a_round_without_any_spawn_takes_its_players_from_the_next_round():
    """Real HLTV demo: the recording starts after round 1's spawns, so a player without a
    kill or death in round 1 had no side. A round with spawns (e.g. a player who
    disconnected and has none) is not filled in."""

    rounds = [ParsedRound(1, 100, 1000, "t"), ParsedRound(2, 1200, 2000, "ct"), ParsedRound(3, 2200, 3000, "t")]
    spawns = [ParsedSpawn(1100, s, side) for s, side in ((A1, "t"), (A2, "t"), (B1, "ct"), (B2, "ct"))]
    spawns += [ParsedSpawn(2100, s, side) for s, side in ((A1, "t"), (B1, "ct"), (B2, "ct"))]  # A2 left
    deaths = [ParsedDeath(500, "t", "ct", "ak47", 0, 0, A1, B1)]  # round 1: only A1 and B1 known from the kill
    records = extract_player_rounds(ParsedDemo("de_nuke", rounds, deaths, spawns=spawns))
    assert {(r.steam_id, r.side) for r in records if r.round_number == 1} == {
        (A1, "t"), (A2, "t"), (B1, "ct"), (B2, "ct")}
    assert by_round(records, A2)[1] == PlayerRoundRecord(1, A2, "t", 0, 0, False, False, True, clutch_vs=0)
    assert 3 not in by_round(records, A2)  # round 3 had spawns: A2 is not made up
    # The teams switched between the spawn-less round and the next one: the filled sides switch too.
    swapped = [ParsedSpawn(1100, s, side) for s, side in ((A1, "ct"), (A2, "ct"), (B1, "t"), (B2, "t"))]
    records = extract_player_rounds(ParsedDemo("de_nuke", rounds[:2], deaths, spawns=swapped))
    assert {(r.steam_id, r.side) for r in records if r.round_number == 1} == {
        (A1, "t"), (A2, "t"), (B1, "ct"), (B2, "ct")}


@pytest.mark.parametrize("value, expected", [
    ("76561198000000101", "76561198000000101"), (76561198000000101, "76561198000000101"), ("0", None), (0, None),
    (None, None), (float("nan"), None), ("", None), ("BOT", None),
])
def test_normalize_steamid(value, expected):
    assert normalize_steamid(value) == expected


# Storage -----------------------------------------------------------------------------------

def match(key, *, players=(), recorded=True, played_at=None, status="imported", score=(1, 1)):
    return NewMatch(share_code="upload:" + key, valve_match_id=UNKNOWN_MATCH_ID, status=status, status_reason=None,
                    map_name="de_mirage", rounds=(RoundRecord(1, "t", "t", 5.0, "ak47", None),
                                                  RoundRecord(2, "ct", "ct", 9.0, "m4a1", None)),
                    demo_sha256=key, source="upload", score_ct=score[0] if score else None,
                    score_t=score[1] if score else None, player_rounds=tuple(players), players_recorded=recorded,
                    played_at=played_at, played_at_source="valve_gc" if played_at else None)


PLAYERS = (PlayerRoundRecord(1, A1, "t", 1, 0, True, False, True), PlayerRoundRecord(2, A1, "ct", 0, 1, False, True, False),
           PlayerRoundRecord(1, B1, "ct", 0, 1, False, True, False), PlayerRoundRecord(2, B1, "t", 1, 0, True, False, True))


@pytest.fixture()
def storage(tmp_path):
    return make_storage(tmp_path)


def test_player_rounds_are_per_player_so_each_owner_of_a_shared_match_sees_their_own_side(storage):
    a, b = storage.get_or_create_user(A1, NOW), storage.get_or_create_user(B1, NOW)
    match_id, _ = storage.record_uploaded_match(a.id, match=match("a" * 64, players=PLAYERS), now=NOW)
    shared, added = storage.record_uploaded_match(b.id, match=match("a" * 64), now=NOW)  # same demo: shared
    assert shared == match_id and added
    assert [(r.round_number, r.side) for r in storage.get_player_rounds(match_id, A1)] == [(1, "t"), (2, "ct")]
    assert [(r.round_number, r.side) for r in storage.get_player_rounds(match_id, B1)] == [(1, "ct"), (2, "t")]
    assert storage.list_player_rounds(a.id, A1) == {match_id: list(PLAYERS[:2])}
    assert storage.list_player_rounds(b.id, B1) == {match_id: list(PLAYERS[2:])}
    assert storage.list_player_rounds(b.id, A1) == {match_id: list(PLAYERS[:2])}  # any player of b's matches
    assert storage.get_match(b.id, match_id)[0].players_recorded is True
    # A user who doesn't own the match sees nothing through list_player_rounds.
    c = storage.get_or_create_user("76561198000000999", NOW)
    assert storage.list_player_rounds(c.id, A1) == {}


def test_a_reparse_fills_in_player_rounds_score_and_date_once(storage):
    a = storage.get_or_create_user(A1, NOW)
    match_id, _ = storage.record_uploaded_match(a.id, match=match("b" * 64, recorded=False, score=None), now=NOW)
    record = storage.get_match(a.id, match_id)[0]
    assert (record.players_recorded, record.score_ct, record.played_at) == (False, None, None)
    played = datetime(2026, 9, 30, 20, 15, tzinfo=timezone.utc)
    again, added = storage.record_uploaded_match(a.id, match=match("b" * 64, players=PLAYERS, played_at=played),
                                                 now=NOW)
    assert (again, added) == (match_id, False)
    record = storage.get_match(a.id, match_id)[0]
    assert (record.players_recorded, record.score_ct, record.score_t) == (True, 1, 1)
    assert (record.played_at, record.played_at_source) == (played, "valve_gc")
    assert len(storage.get_player_rounds(match_id, A1)) == 2
    # Recorded once: a later parse with different data changes nothing (no duplicates either).
    other = (PlayerRoundRecord(1, A1, "ct", 5, 5, False, False, False),)
    storage.record_uploaded_match(a.id, match=match("b" * 64, players=other, played_at=NOW), now=NOW)
    assert storage.get_player_rounds(match_id, A1) == list(PLAYERS[:2])
    assert storage.get_match(a.id, match_id)[0].played_at == played


def test_stub_upgraded_by_an_upload_gets_player_rounds(storage):
    a = storage.get_or_create_user(A1, NOW)
    stub = NewMatch(share_code=code(1), valve_match_id=str(match_id_of(1)), status="unavailable",
                    status_reason="demo_unavailable", map_name=None, source="steam_sync", share_code_verified=True)
    stub_id, _ = storage.record_uploaded_match(a.id, match=stub, now=NOW)
    assert storage.get_match(a.id, stub_id)[0].players_recorded is False
    upload = NewMatch(share_code=code(1), valve_match_id=str(match_id_of(1)), status="imported", status_reason=None,
                      map_name="de_mirage", rounds=match("c" * 64).rounds, demo_sha256="c" * 64, source="upload",
                      player_rounds=PLAYERS, players_recorded=True)
    up_id, _ = storage.record_uploaded_match(a.id, match=upload, now=NOW)
    assert up_id == stub_id
    assert storage.get_match(a.id, stub_id)[0].players_recorded is True
    assert len(storage.get_player_rounds(stub_id, B1)) == 2


def test_deleting_the_last_owner_deletes_player_rounds(storage):
    a = storage.get_or_create_user(A1, NOW)
    match_id, _ = storage.record_uploaded_match(a.id, match=match("d" * 64, players=PLAYERS), now=NOW)
    storage.delete_user(a.id)
    with storage.engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM player_rounds")).scalar_one() == 0


def test_matches_from_before_the_migration_are_marked_unknown(tmp_path):
    from pathlib import Path
    import shutil

    from steamlink.migrate import MIGRATIONS_DIR

    old = tmp_path / "old_migrations"
    old.mkdir()
    for path in Path(MIGRATIONS_DIR).iterdir():
        if path.suffix in (".sql", ".py") and path.stem[:4].isdigit() and path.stem < "0007":
            shutil.copy(path, old / path.name)
    engine = make_test_engine(tmp_path, "premigration")
    apply_migrations(engine, old)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, steam_id, created_at, updated_at) VALUES ('u', :s, :t, :t)"),
                     {"s": A1, "t": NOW})
        conn.execute(text(
            "INSERT INTO matches (id, user_id, share_code, valve_match_id, status, map_name, rounds_count, imported_at,"
            " source, demo_sha256) VALUES ('m', 'u', 'upload:e', 'upload', 'imported', 'de_nuke', 0, :t, 'upload', 'e')"),
            {"t": NOW})
        conn.execute(text("INSERT INTO match_owners (user_id, match_id, source, added_at) VALUES ('u', 'm', 'upload', :t)"),
                     {"t": NOW})
    assert apply_migrations(engine) == ["0007_player_rounds", "0008_parse_version", "0009_auto_sync",
                                        "0010_match_detail"]
    record = SqlStorage(engine).get_match("u", "m")[0]
    assert (record.players_recorded, record.played_at, record.played_at_source) == (False, None, None)
    assert (record.parse_version, record.outdated_reason) == (0, "players_not_recorded")


def test_migration_0008_flags_matches_parsed_before_warmup_rounds_were_left_out(tmp_path):
    """Matches parsed with per-player rounds (0007) but before warmup / knife rounds were
    left out of all numbers get parse_version 1 ("parser_updated"); nothing is rewritten."""

    from pathlib import Path
    import shutil

    from steamlink.migrate import MIGRATIONS_DIR

    old = tmp_path / "old_migrations"
    old.mkdir()
    for path in Path(MIGRATIONS_DIR).iterdir():
        if path.suffix in (".sql", ".py") and path.stem[:4].isdigit() and path.stem < "0008":
            shutil.copy(path, old / path.name)
    engine = make_test_engine(tmp_path, "pre0008")
    apply_migrations(engine, old)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, steam_id, created_at, updated_at) VALUES ('u', :s, :t, :t)"),
                     {"s": A1, "t": NOW})
        for mid, status, recorded in (("v1", "imported", 1), ("v0", "imported", 0), ("stub", "unavailable", 0)):
            conn.execute(text(
                "INSERT INTO matches (id, user_id, share_code, valve_match_id, status, map_name, rounds_count, imported_at,"
                " source, players_recorded) VALUES (:id, 'u', :code, 'upload', :status, 'de_nuke', 25, :t, 'upload', :r)"),
                {"id": mid, "code": "upload:" + mid, "status": status, "r": recorded, "t": NOW})
            conn.execute(text("INSERT INTO match_owners (user_id, match_id, source, added_at) VALUES ('u', :id, 'upload', :t)"),
                         {"id": mid, "t": NOW})
    assert apply_migrations(engine) == ["0008_parse_version", "0009_auto_sync", "0010_match_detail"]
    storage = SqlStorage(engine)
    got = {mid: storage.get_match("u", mid)[0] for mid in ("v1", "v0", "stub")}
    assert {mid: (m.parse_version, m.outdated_reason, m.rounds_count) for mid, m in got.items()} == {
        "v1": (1, "parser_updated", 25), "v0": (0, "players_not_recorded", 25), "stub": (0, None, 25)}


# Match date from the Game Coordinator ----------------------------------------------------------

class _GC(GameCoordinator):
    def __init__(self, match_time):
        self.match_time = match_time

    def full_match_info(self, match_id, outcome_id, token):
        return GCMatch(match_id=match_id, match_time=self.match_time,
                       round_urls=("", f"http://replay1.valve.net/730/{match_id}_{outcome_id}.dem.bz2"))


def test_gc_locator_returns_the_match_time():
    share = ShareCode(1001, 2001, 3001)
    locator = GameCoordinatorDemoLocator(_GC(1759600000), min_interval_seconds=0)
    info = locator.demo_info(share)
    assert info.url.endswith("/1001_2001.dem.bz2") and info.match_time == 1759600000
    assert locator.demo_url(share) == info.url
    assert GameCoordinatorDemoLocator(_GC(0), min_interval_seconds=0).demo_info(share).match_time is None


@pytest.mark.parametrize("value, expected", [
    (1759600000, datetime(2025, 10, 4, 17, 46, 40, tzinfo=timezone.utc)), (None, None), (0, None), (12345, None),
])
def test_played_at_from_match_time(value, expected):
    assert _played_at(value) == expected


def test_steam_sync_stores_when_the_match_was_played_and_every_players_side(env):  # noqa: F811
    env.locator.match_times[match_id_of(1)] = 1759600000
    env.sync()
    env.drain()
    stored = env.matches()[code(1)]
    assert stored.played_at == datetime(2025, 10, 4, 17, 46, 40, tzinfo=timezone.utc)
    assert stored.played_at_source == "valve_gc" and stored.players_recorded
    assert env.matches()[code(2)].played_at is None  # the GC sent no time for match 2
    mine = env.storage.get_player_rounds(stored.id, FAKE_PLAYER)
    assert [(r.round_number, r.side, r.opening_kill, r.opening_death) for r in mine] == [
        (1, "t", True, False), (2, "t", False, True)]
    assert [r.side for r in env.storage.get_player_rounds(stored.id, FAKE_OPPONENT)] == ["ct", "ct"]


def test_reuploading_a_demo_stored_before_player_tracking_parses_it_once_more(tmp_path):
    import hashlib

    from test_api_steam import login, make_client, upload_and_wait

    client, ctx = make_client(tmp_path)
    login(client, ctx)
    demo = b"PBDEMS2\0" + b"q" * 3000
    sha = hashlib.sha256(demo).hexdigest()
    me = ctx.storage.get_or_create_user(FAKE_PLAYER, ctx.clock())
    old_id, _ = ctx.storage.record_uploaded_match(me.id, match=match(sha, recorded=False), now=ctx.clock())
    response, job = upload_and_wait(client, ctx, demo)
    assert response.status_code == 202 and job["status"] == "done" and job["match"]["id"] == old_id
    assert (job["created"], job["updated"]) == (False, True)  # already in the list; re-parsed
    assert ctx.sync.parser.calls == 1
    assert [r.side for r in ctx.storage.get_player_rounds(old_id, FAKE_PLAYER)] == ["t", "t"]
    # Now recorded: the next upload of the same demo is the instant dedupe again (no parse).
    response, job = upload_and_wait(client, ctx, demo)
    assert response.status_code == 200 and job["match"]["id"] == old_id and ctx.sync.parser.calls == 1


# Outdated matches (an older parse_version) -------------------------------------------------

def test_a_newer_parse_replaces_an_outdated_match_and_an_older_one_changes_nothing(storage):
    """E.g. a match stored with its knife round counted (parse_version 1, 3 rounds) and the
    same demo uploaded again: the current parse (2 rounds) replaces rounds, player rounds,
    rounds_count and score. A parse from an older parser never overwrites a newer one."""

    from dataclasses import replace

    a = storage.get_or_create_user(A1, NOW)
    knife = RoundRecord(1, "t", "t", 1.0, "knife", None)
    shifted = tuple(replace(r, round_number=r.round_number + 1) for r in match("e" * 64).rounds)
    v1 = replace(match("e" * 64, players=tuple(replace(p, round_number=p.round_number + 1) for p in PLAYERS),
                       score=(2, 1)), rounds=(knife, *shifted), parse_version=1)
    match_id, _ = storage.record_uploaded_match(a.id, match=v1, now=NOW)
    record = storage.get_match(a.id, match_id)[0]
    assert (record.rounds_count, record.parse_version, record.outdated_reason) == (3, 1, "parser_updated")

    again, added = storage.record_uploaded_match(a.id, match=match("e" * 64, players=PLAYERS), now=NOW)
    assert (again, added) == (match_id, False)
    record, rounds = storage.get_match(a.id, match_id)
    assert (record.rounds_count, record.parse_version, record.outdated_reason) == (2, PARSE_VERSION, None)
    assert [(r.round_number, r.opening_weapon) for r in rounds] == [(1, "ak47"), (2, "m4a1")]
    assert (record.score_ct, record.score_t) == (1, 1)
    assert storage.get_player_rounds(match_id, A1) == list(PLAYERS[:2])

    storage.record_uploaded_match(a.id, match=v1, now=NOW)  # an older parser's result
    record, rounds = storage.get_match(a.id, match_id)
    assert (record.rounds_count, record.parse_version, len(rounds)) == (2, PARSE_VERSION, 2)


def test_reupload_of_an_outdated_match_someone_else_imported_adds_it_and_updates_it(tmp_path):
    """The upload request adds the stored (outdated) match to the user's list and queues a
    re-parse; the finished job reports the match as new to this user (created true)."""

    import hashlib
    from dataclasses import replace

    from test_api_steam import login, make_client, upload_and_wait

    client, ctx = make_client(tmp_path)
    login(client, ctx)
    demo = b"PBDEMS2\0" + b"w" * 3000
    sha = hashlib.sha256(demo).hexdigest()
    other = ctx.storage.get_or_create_user("76561198000000999", ctx.clock())
    knife = RoundRecord(1, "t", "t", 1.0, "knife", None)
    shifted = tuple(replace(r, round_number=r.round_number + 1) for r in match(sha).rounds)
    old = replace(match(sha), rounds=(knife, *shifted), parse_version=1)
    old_id, _ = ctx.storage.record_uploaded_match(other.id, match=old, now=ctx.clock())
    assert ctx.storage.get_match(other.id, old_id)[0].rounds_count == 3

    response, job = upload_and_wait(client, ctx, demo)
    assert response.status_code == 202 and job["status"] == "done", job
    assert job["match"]["id"] == old_id and job["created"] is True and job["updated"] is True
    assert ctx.sync.parser.calls == 1
    assert job["match"]["outdated"] is None and job["match"]["rounds_count"] == 2
    listed = client.get("/matches").json()["matches"]
    assert [m["id"] for m in listed] == [old_id]
    # The other owner sees the update too (one shared row).
    assert ctx.storage.get_match(other.id, old_id)[0].rounds_count == 2
    # Up to date now: the next upload is the instant dedupe (no parse, not new).
    response, job = upload_and_wait(client, ctx, demo)
    assert response.status_code == 200 and job["created"] is False and job["updated"] is False
    assert ctx.sync.parser.calls == 1


def test_outdated_matches_are_flagged_in_the_api_with_reupload_as_the_fix(tmp_path):
    from dataclasses import replace

    from test_api_steam import login, make_client

    client, ctx = make_client(tmp_path)
    login(client, ctx)
    me = ctx.storage.get_or_create_user(FAKE_PLAYER, ctx.clock())
    ids = {}
    for key, version, recorded in (("1", 1, True), ("0", 0, False), ("2", 2, True)):
        ids[key], _ = ctx.storage.record_uploaded_match(
            me.id, match=replace(match(key * 64, recorded=recorded), parse_version=version), now=ctx.clock())
    views = {m["id"]: m for m in client.get("/matches").json()["matches"]}
    assert views[ids["1"]]["outdated"] == {"reason": "parser_updated", "fix": "reupload"}
    assert views[ids["0"]]["outdated"] == {"reason": "players_not_recorded", "fix": "reupload"}
    assert views[ids["2"]]["outdated"] is None
    assert client.get(f"/matches/{ids['1']}").json()["match"]["outdated"]["reason"] == "parser_updated"
    summary = client.get("/matches/summary").json()
    assert summary["totals"]["outdated_matches"] == 2 and summary["you"]["matches_unknown"] == 1
