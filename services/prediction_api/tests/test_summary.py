"""GET /matches/summary: analytics across all the user's previous matches / rounds.
Runs on SQLite by default and on PostgreSQL with CSA_TEST_DATABASE_URL."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import main
from steamlink.analytics import CALIBRATION_BINS
from steamlink.storage.base import UNKNOWN_MATCH_ID, UPLOAD_KEY_PREFIX, NewMatch, RoundRecord

from fakes import code
from test_api_steam import H, STEAM_ID, make_client, login

OTHER = "76561198000000002"


def rnd(n, winner, side, weapon="ak47", seconds=20.0, reason=None):
    return RoundRecord(round_number=n, winner_side=winner, opening_kill_side=side, opening_kill_seconds=seconds,
                       opening_weapon=weapon, unscored_reason=reason)


def store(ctx, user_id, key, map_name, rounds, *, status="imported", score=None):
    """Store a parsed match for ``user_id`` (or add the stored one with the same demo hash)."""
    ctx.clock.advance(60)  # imported_at orders the list (newest first)
    match = NewMatch(share_code=UPLOAD_KEY_PREFIX + key, valve_match_id=UNKNOWN_MATCH_ID, status=status,
                     status_reason=None if status == "imported" else "demo_unavailable", map_name=map_name,
                     rounds=tuple(rounds), demo_sha256=key, source="upload",
                     score_ct=score[0] if score else None, score_t=score[1] if score else None)
    match_id, _ = ctx.storage.record_uploaded_match(user_id, match=match, now=ctx.clock())
    return match_id


def user_id(ctx, steam_id=STEAM_ID):
    return ctx.storage.get_or_create_user(steam_id, ctx.clock()).id


def session(client, ctx, steam_id):
    u = ctx.storage.get_or_create_user(steam_id, ctx.clock())
    cookie, token_hash = ctx.signer.new_session_cookie()
    ctx.storage.create_session(token_hash, u.id, ctx.clock(), ctx.clock() + timedelta(days=1))
    other = TestClient(client.app, base_url="https://api.example.com", follow_redirects=False)
    other.cookies.set(ctx.settings.session_cookie_name, cookie)
    return other


@pytest.fixture()
def app_client(tmp_path):
    client, ctx = make_client(tmp_path)
    login(client, ctx)
    return client, ctx


def expected_brier(map_name, rounds):
    scores = main.scorer.score_rounds(map_name, rounds)
    errors = [(s.probability_ct - (r.winner_side == "ct")) ** 2 for r, s in zip(rounds, scores)
              if s.unscored_reason is None and r.winner_side in ("ct", "t")]
    return sum(errors), len(errors)


def test_requires_sign_in_and_is_not_shadowed_by_match_id(tmp_path):
    client, ctx = make_client(tmp_path)
    assert client.get("/matches/summary").status_code == 401
    login(client, ctx)
    response = client.get("/matches/summary")
    assert response.status_code == 200, response.text  # not 404 match_not_found
    assert client.get("/matches/summary?recent=0").status_code == 422
    assert client.get("/matches/summary?recent=51").status_code == 422


def test_zero_matches(app_client):
    client, _ = app_client
    body = client.get("/matches/summary").json()
    assert body["model"]["calibrated_for_matchmaking"] is False and body["model"]["note"]
    assert body["totals"] == {
        "matches": 0, "imported_matches": 0, "not_imported_matches": 0, "outdated_matches": 0, "rounds": 0, "rounds_with_winner": 0,
        "scored_rounds": 0, "unscored_rounds": 0, "first_imported_at": None, "last_imported_at": None}
    prediction = body["prediction"]
    assert (prediction["scored_rounds"], prediction["hit_rate"], prediction["brier_score"],
            prediction["opening_kill_baseline_hit_rate"]) == (0, None, None, None)
    assert [(b["min"], b["max"]) for b in prediction["calibration"]] == list(CALIBRATION_BINS)
    assert all(b["rounds"] == 0 and b["hit_rate"] is None for b in prediction["calibration"])
    assert body["sides"]["ct_win_rate"] is None and body["opening_kills"]["conversion_rate"] is None
    assert body["opening_kills"]["top_weapons"] == [] and body["maps"] == [] and body["unscored_reasons"] == []
    form = body["recent_form"]
    assert form["matches"] == [] and form["recent"]["matches"] == 0 and form["hit_rate_change"] is None


def test_aggregates_previous_matches_and_rounds(app_client):
    client, ctx = app_client
    uid = user_id(ctx)
    mirage = [rnd(1, "t", "t"), rnd(2, "ct", "t"), rnd(3, "ct", "ct", "m4a1_silencer", 30.0),
              rnd(4, "t", "ct", "bayonet"),  # weapon not in the model: unscored, still counts for sides
              rnd(5, None, None, None, None, "no_opening_kill")]  # no winner known
    nuke = [rnd(1, "ct", "ct", "awp", 5.0), rnd(2, "ct", "t", "glock", 3.0), rnd(3, "t", "t")]
    unknown_map = [rnd(1, "t", "t"), rnd(2, "ct", "ct")]  # map not in the model: nothing scored
    store(ctx, uid, "a" * 64, "de_mirage", mirage, score=(2, 2))
    store(ctx, uid, "b" * 64, "de_nuke", nuke, score=(2, 1))
    store(ctx, uid, "c" * 64, "de_mirage", mirage[:3])
    store(ctx, uid, "d" * 64, "cs_office", unknown_map)
    store(ctx, uid, "e" * 64, None, [], status="unavailable")  # stub: listed, no rounds

    body = client.get("/matches/summary").json()
    totals = body["totals"]
    assert {k: totals[k] for k in ("matches", "imported_matches", "not_imported_matches", "rounds",
                                   "rounds_with_winner", "scored_rounds", "unscored_rounds")} == {
        "matches": 5, "imported_matches": 4, "not_imported_matches": 1, "rounds": 13, "rounds_with_winner": 12,
        "scored_rounds": 9, "unscored_rounds": 4}
    assert totals["first_imported_at"] < totals["last_imported_at"]

    # Model: it backs the opening-kill side here, so it hits whenever that side won.
    prediction = body["prediction"]
    assert (prediction["scored_rounds"], prediction["correct"]) == (9, 6)
    assert prediction["hit_rate"] == round(6 / 9, 4)
    assert prediction["opening_kill_baseline_hit_rate"] == round(6 / 9, 4)
    s1, n1 = expected_brier("de_mirage", mirage)
    s2, n2 = expected_brier("de_mirage", mirage[:3])
    s3, n3 = expected_brier("de_nuke", nuke)
    assert n1 + n2 + n3 == 9
    assert prediction["brier_score"] == pytest.approx((s1 + s2 + s3) / 9, abs=1e-4)
    assert 0 < prediction["brier_score"] < 1
    bins = prediction["calibration"]
    assert sum(b["rounds"] for b in bins) == 9 and sum(b["hit_rate"] * b["rounds"] for b in bins if b["rounds"]) == pytest.approx(6, abs=1e-3)
    for b in bins:
        if b["rounds"]:
            assert b["min"] <= b["mean_confidence"] <= b["max"]

    # Sides: every round with a known winner (scored or not), all players in the match.
    assert body["sides"] == {"rounds_with_winner": 12, "ct_won": 7, "t_won": 5, "ct_win_rate": 0.5833,
                             "t_win_rate": 0.4167}
    opening = body["opening_kills"]
    assert (opening["rounds"], opening["converted"]) == (12, 8)
    assert opening["by_side"]["t"] == {"rounds": 7, "converted": 4, "conversion_rate": round(4 / 7, 4)}
    assert opening["by_side"]["ct"] == {"rounds": 5, "converted": 4, "conversion_rate": 0.8}
    assert opening["top_weapons"][0] == {"weapon": "ak47", "rounds": 7, "converted": 5, "conversion_rate": 0.7143}
    assert {w["weapon"] for w in opening["top_weapons"]} == {"ak47", "m4a1_silencer", "bayonet", "awp", "glock"}

    maps = {m["map_name"]: m for m in body["maps"]}
    assert [m["map_name"] for m in body["maps"]] == ["de_mirage", "de_nuke", "cs_office"]  # matches, rounds, name
    assert (maps["de_mirage"]["matches"], maps["de_mirage"]["rounds"], maps["de_mirage"]["ct_won"],
            maps["de_mirage"]["t_won"], maps["de_mirage"]["scored_rounds"], maps["de_mirage"]["correct"]) == (2, 8, 4, 3, 6, 4)
    assert maps["cs_office"]["scored_rounds"] == 0 and maps["cs_office"]["hit_rate"] is None
    assert maps["cs_office"]["ct_win_rate"] == 0.5
    assert (maps["de_nuke"]["ct_win_rate"], maps["de_nuke"]["hit_rate"]) == (round(2 / 3, 4), round(2 / 3, 4))
    assert body["unscored_reasons"] == [{"reason": "map_not_in_model", "rounds": 2},
                                        {"reason": "no_opening_kill", "rounds": 1},
                                        {"reason": "weapon_not_in_model", "rounds": 1}]


def test_recent_form_window_newest_first(app_client):
    client, ctx = app_client
    uid = user_id(ctx)
    hit, miss = rnd(1, "t", "t"), rnd(1, "ct", "t")
    ids = [store(ctx, uid, f"{n:064x}", "de_mirage", [hit if n >= 3 else miss], score=(0, 1)) for n in range(5)]
    form = client.get("/matches/summary?recent=2").json()["recent_form"]
    assert form["window"] == 2
    assert [m["id"] for m in form["matches"]] == [ids[4], ids[3]]  # newest first
    assert form["matches"][0]["score"] == {"ct": 0, "t": 1} and form["matches"][0]["hit_rate"] == 1.0
    assert (form["recent"]["matches"], form["recent"]["hit_rate"]) == (2, 1.0)
    assert (form["earlier"]["matches"], form["earlier"]["scored_rounds"], form["earlier"]["hit_rate"]) == (3, 3, 0.0)
    assert form["hit_rate_change"] == 1.0
    default = client.get("/matches/summary").json()["recent_form"]
    assert default["window"] == 10 and len(default["matches"]) == 5 and default["earlier"]["matches"] == 0
    assert default["hit_rate_change"] is None


def test_only_matches_the_user_owns_shared_matches_count_for_each_owner(app_client):
    client, ctx = app_client
    alice, bob = user_id(ctx), user_id(ctx, OTHER)
    shared = store(ctx, alice, "f" * 64, "de_mirage", [rnd(1, "t", "t"), rnd(2, "ct", "ct")])
    store(ctx, alice, "1" * 64, "de_nuke", [rnd(1, "t", "t")])
    store(ctx, bob, "2" * 64, "de_ancient", [rnd(1, "ct", "t"), rnd(2, "ct", "t"), rnd(3, "ct", "t")])
    # Bob uploads the same demo as Alice: one shared row, in both lists.
    assert store(ctx, bob, "f" * 64, "de_mirage", [rnd(1, "t", "t"), rnd(2, "ct", "ct")]) == shared

    a = client.get("/matches/summary").json()
    b = session(client, ctx, OTHER).get("/matches/summary").json()
    assert (a["totals"]["matches"], a["totals"]["rounds"]) == (2, 3)
    assert (b["totals"]["matches"], b["totals"]["rounds"]) == (2, 5)
    assert {m["map_name"] for m in a["maps"]} == {"de_mirage", "de_nuke"}
    assert {m["map_name"] for m in b["maps"]} == {"de_mirage", "de_ancient"}
    assert (a["prediction"]["correct"], b["prediction"]["correct"]) == (3, 2)
    assert b["recent_form"]["matches"][0]["id"] == shared  # newest in Bob's list (added last)

    # A third user with nothing stored sees nothing.
    assert session(client, ctx, "76561198000000003").get("/matches/summary").json()["totals"]["matches"] == 0


def test_summary_matches_the_per_match_reports_after_sync(app_client):
    client, ctx = app_client
    body = {"auth_code": "AB12-CDE34-FG56", "share_code": code(0), "consent": True}
    assert client.put("/steam/match-access", json=body, headers=H).status_code == 200
    assert client.post("/steam/sync", headers=H).status_code == 202 and ctx.jobs.wait_idle(30)
    matches = client.get("/matches").json()["matches"]
    reports = [client.get(f"/matches/{m['id']}").json() for m in matches]
    summary = client.get("/matches/summary").json()
    assert summary["totals"]["matches"] == len(matches) == 3
    assert summary["totals"]["rounds"] == sum(r["summary"]["rounds"] for r in reports)
    assert summary["prediction"]["scored_rounds"] == sum(r["summary"]["scored"] for r in reports)
    assert summary["prediction"]["correct"] == sum(r["summary"]["correct_predictions"] for r in reports)
    assert [m["id"] for m in summary["recent_form"]["matches"]] == [m["id"] for m in matches]
