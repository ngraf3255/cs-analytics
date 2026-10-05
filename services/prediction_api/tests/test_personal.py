"""The signed-in player's own analytics: GET /matches/summary ``you`` and GET /matches/{id}
``you`` (per-player rounds, migration 0007), match dates (played_at vs imported_at).
Runs on SQLite by default and on PostgreSQL with CSA_TEST_DATABASE_URL."""

from datetime import datetime, timezone

import pytest

from steamlink.storage.base import UNKNOWN_MATCH_ID, UPLOAD_KEY_PREFIX, NewMatch, PlayerRoundRecord, RoundRecord

from test_api_steam import STEAM_ID, login, make_client
from test_summary import OTHER, session

ME, THEM = STEAM_ID, OTHER


def rnd(n, winner, side="t", weapon="ak47"):
    return RoundRecord(round_number=n, winner_side=winner, opening_kill_side=side, opening_kill_seconds=20.0,
                       opening_weapon=weapon, unscored_reason=None)


def pr(n, steam_id, side, kills=0, deaths=0, ok=False, od=False, survived=True):
    return PlayerRoundRecord(n, steam_id, side, kills, deaths, ok, od, survived)


def store(ctx, owner_steam_id, key, map_name, rounds, players=(), *, recorded=True, score=None, played_at=None):
    ctx.clock.advance(60)
    owner = ctx.storage.get_or_create_user(owner_steam_id, ctx.clock())
    match = NewMatch(share_code=UPLOAD_KEY_PREFIX + key, valve_match_id=UNKNOWN_MATCH_ID, status="imported",
                     status_reason=None, map_name=map_name, rounds=tuple(rounds), demo_sha256=key, source="upload",
                     score_ct=score[0] if score else None, score_t=score[1] if score else None,
                     player_rounds=tuple(players), players_recorded=recorded, played_at=played_at,
                     played_at_source="valve_gc" if played_at else None)
    match_id, _ = ctx.storage.record_uploaded_match(owner.id, match=match, now=ctx.clock())
    return match_id


@pytest.fixture()
def app_client(tmp_path):
    client, ctx = make_client(tmp_path)
    login(client, ctx)
    return client, ctx


# Mirage, 4 rounds, halftime after round 2: ME is T then CT; THEM the opposite. Final score
# (sides at the end) CT 3 - T 1: ME ends on CT, so ME wins 3-1.
MIRAGE_ROUNDS = [rnd(1, "t"), rnd(2, "ct"), rnd(3, "ct"), rnd(4, "ct")]
MIRAGE_PLAYERS = [
    pr(1, ME, "t", kills=2, ok=True), pr(2, ME, "t", deaths=1, od=True, survived=False),
    pr(3, ME, "ct", kills=1, deaths=1, survived=False), pr(4, ME, "ct", kills=3),
    pr(1, THEM, "ct", deaths=1, od=True, survived=False), pr(2, THEM, "ct", kills=1, ok=True),
    pr(3, THEM, "t", kills=1, ok=True, deaths=1, survived=False), pr(4, THEM, "t", deaths=1, survived=False),
]
# Nuke, 3 rounds: ME on CT throughout, the CT side lost 2 of 3 -> ME lost 1-2.
NUKE_ROUNDS = [rnd(1, "t"), rnd(2, "t"), rnd(3, "ct")]
NUKE_PLAYERS = [pr(1, ME, "ct", deaths=1, survived=False), pr(2, ME, "ct", kills=1, deaths=1, survived=False),
                pr(3, ME, "ct", kills=2, ok=True)]


def test_summary_you_section_uses_the_players_own_side_each_round(app_client):
    client, ctx = app_client
    store(ctx, ME, "a" * 64, "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS, score=(3, 1))
    store(ctx, ME, "b" * 64, "de_nuke", NUKE_ROUNDS, NUKE_PLAYERS, score=(1, 2))
    store(ctx, ME, "c" * 64, "de_nuke", [rnd(1, "ct"), rnd(2, "ct")], [pr(1, "76561197960287930", "ct")])  # pro demo
    store(ctx, ME, "d" * 64, "de_ancient", [rnd(1, "t")], recorded=False)  # parsed before player tracking

    body = client.get("/matches/summary").json()
    you = body["you"]
    assert you["steam_id"] == ME
    assert (you["matches"], you["matches_without_you"], you["matches_unknown"]) == (2, 1, 1)
    # Mirage: won rounds 1 (T), 3, 4 (CT) = 3/4; Nuke: won round 3 = 1/3.
    assert (you["rounds"], you["rounds_with_winner"], you["won"], you["win_rate"]) == (7, 7, 4, round(4 / 7, 4))
    assert you["sides"] == {"ct": {"rounds": 5, "won": 3, "win_rate": 0.6},
                            "t": {"rounds": 2, "won": 1, "win_rate": 0.5}}
    assert you["results"] == {"won": 1, "lost": 1, "tied": 0, "unknown": 0}
    assert (you["kills"], you["deaths"], you["kd"], you["kills_per_round"]) == (9, 4, 2.25, 1.29)
    assert (you["survived"], you["survival_rate"]) == (3, round(3 / 7, 4))
    assert you["opening_duels"] == {"taken": 3, "won": 2, "lost": 1, "win_rate": round(2 / 3, 4),
                                    "round_win_rate_after_opening_kill": 1.0,
                                    "round_win_rate_after_opening_death": 0.0}
    maps = {m["map_name"]: m for m in you["maps"]}
    assert list(maps) == ["de_mirage", "de_nuke"] or list(maps) == ["de_nuke", "de_mirage"]
    assert (maps["de_mirage"]["win_rate"], maps["de_mirage"]["sides"]["t"]["win_rate"],
            maps["de_mirage"]["sides"]["ct"]["win_rate"], maps["de_mirage"]["kd"]) == (0.75, 0.5, 1.0, 3.0)
    assert (maps["de_nuke"]["win_rate"], maps["de_nuke"]["sides"]["t"]["rounds"], maps["de_nuke"]["kd"]) == (
        round(1 / 3, 4), 0, 1.5)
    form = you["recent_form"]
    assert [m["map_name"] for m in form["matches"]] == ["de_nuke", "de_mirage"]  # newest first
    assert form["matches"][0] | {"id": None, "date": None} == {
        "id": None, "map_name": "de_nuke", "date": None, "date_source": "imported", "played_at": None,
        "first_side": "ct", "rounds": 3, "won": 1, "win_rate": round(1 / 3, 4), "kills": 3, "deaths": 2,
        "score": {"you": 1, "them": 2}, "result": "lost"}
    assert form["matches"][1]["score"] == {"you": 3, "them": 1} and form["matches"][1]["result"] == "won"

    # The all-player numbers still cover every imported match (pro demo and unknown one too), labelled.
    assert body["totals"]["imported_matches"] == 4 and body["sides"]["rounds_with_winner"] == 10
    assert body["scope"]["sides"] == "all_players" and body["scope"]["you"] == "signed_in_player"


def test_recent_form_for_you_compares_last_matches_with_earlier(app_client):
    client, ctx = app_client
    for n in range(4):  # 2 older losses (1 round lost, 0 K / 1 D), then 2 wins (1 round won, 2 K / 1 D)
        win = n >= 2
        store(ctx, ME, f"{n:064x}", "de_mirage", [rnd(1, "t" if win else "ct")],
              [pr(1, ME, "t", kills=2 if win else 0, deaths=1, survived=False)], score=(0, 1) if win else (1, 0))
    form = client.get("/matches/summary?recent=2").json()["you"]["recent_form"]
    assert (form["recent"]["matches"], form["recent"]["win_rate"], form["recent"]["kd"]) == (2, 1.0, 2.0)
    assert (form["earlier"]["matches"], form["earlier"]["win_rate"], form["earlier"]["kd"]) == (2, 0.0, 0.0)
    assert (form["win_rate_change"], form["kd_change"]) == (1.0, 2.0)
    assert form["recent"]["results"] == {"won": 2, "lost": 0, "tied": 0, "unknown": 0}


def test_each_owner_of_a_shared_match_sees_their_own_team(app_client):
    client, ctx = app_client
    match_id = store(ctx, ME, "e" * 64, "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS, score=(3, 1))
    assert store(ctx, THEM, "e" * 64, "de_mirage", MIRAGE_ROUNDS) == match_id  # THEM attaches it, no parse
    mine = client.get("/matches/summary").json()["you"]
    theirs = session(client, ctx, THEM).get("/matches/summary").json()["you"]
    assert (mine["won"], theirs["won"]) == (3, 1)  # complementary: opposite teams
    assert (mine["results"]["won"], theirs["results"]["lost"]) == (1, 1)
    assert (mine["sides"]["t"]["rounds"], theirs["sides"]["t"]["rounds"]) == (2, 2)
    their_report = session(client, ctx, THEM).get(f"/matches/{match_id}").json()
    assert their_report["you"]["status"] == "in_match" and their_report["you"]["first_side"] == "ct"
    assert their_report["you"]["score"] == {"you": 1, "them": 3} and their_report["you"]["result"] == "lost"


def test_no_player_data_yet(app_client):
    client, ctx = app_client
    you = client.get("/matches/summary").json()["you"]
    assert (you["matches"], you["rounds"], you["win_rate"], you["kd"], you["maps"]) == (0, 0, None, None, [])
    assert you["recent_form"]["matches"] == [] and you["recent_form"]["win_rate_change"] is None


def test_match_report_highlights_your_rounds(app_client):
    client, ctx = app_client
    match_id = store(ctx, ME, "f" * 64, "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS, score=(3, 1))
    report = client.get(f"/matches/{match_id}").json()
    assert report["you"] == {
        "status": "in_match", "steam_id": ME, "first_side": "t", "last_side": "ct", "rounds": 4, "won": 3,
        "win_rate": 0.75, "kills": 6, "deaths": 2, "kd": 3.0, "opening_kills": 1, "opening_deaths": 1,
        "survived": 2, "score": {"you": 3, "them": 1}, "result": "won"}
    rounds = report["rounds"]
    assert [r["you"]["side"] for r in rounds] == ["t", "t", "ct", "ct"]
    assert [r["you"]["won"] for r in rounds] == [True, False, True, True]
    assert rounds[0]["you"]["opening_kill"] is True and rounds[1]["you"]["opening_death"] is True
    for r in rounds:
        if r["prediction"]:  # the model's probability for the player's side that round
            assert r["you"]["win_probability"] == r["prediction"]["probabilities"][r["you"]["side"]]
    assert report["match"]["players_recorded"] is True


def test_match_report_you_not_in_match_or_unknown(app_client):
    client, ctx = app_client
    pro = store(ctx, ME, "1" * 64, "de_nuke", NUKE_ROUNDS, [pr(1, "76561197960287930", "ct")])
    old = store(ctx, ME, "2" * 64, "de_nuke", NUKE_ROUNDS, recorded=False)
    pro_report, old_report = client.get(f"/matches/{pro}").json(), client.get(f"/matches/{old}").json()
    assert pro_report["you"] == {"status": "not_in_match", "steam_id": ME}
    assert old_report["you"] == {"status": "unknown", "steam_id": ME}
    assert all(r["you"] is None for r in pro_report["rounds"] + old_report["rounds"])


def test_match_date_is_when_it_was_played_if_known_else_imported(app_client):
    client, ctx = app_client
    played = datetime(2026, 9, 1, 18, 30, tzinfo=timezone.utc)
    synced = store(ctx, ME, "3" * 64, "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS, played_at=played)
    uploaded = store(ctx, ME, "4" * 64, "de_nuke", NUKE_ROUNDS, NUKE_PLAYERS)  # added later, no date
    listed = {m["id"]: m for m in client.get("/matches").json()["matches"]}
    assert listed[synced]["date"] == "2026-09-01T18:30:00Z" and listed[synced]["date_source"] == "played"
    assert listed[synced]["played_at"] == "2026-09-01T18:30:00Z"
    assert listed[uploaded]["date_source"] == "imported" and listed[uploaded]["played_at"] is None
    assert listed[uploaded]["date"] == listed[uploaded]["imported_at"]
    report = client.get(f"/matches/{synced}").json()
    assert report["match"]["date_source"] == "played"
    # Recent form is ordered by when matches were played (else added): the synced match was
    # played in September, the upload was added now (and has no date) -> upload first.
    summary = client.get("/matches/summary").json()
    assert [m["id"] for m in summary["you"]["recent_form"]["matches"]] == [uploaded, synced]
    assert [m["id"] for m in summary["recent_form"]["matches"]] == [uploaded, synced]
    assert summary["recent_form"]["matches"][1]["date_source"] == "played"
