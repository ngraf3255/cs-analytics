"""GET /matches/{id} ``players``: everyone in the demo, their team relative to the signed-in
player, and how often the signed-in player had them as teammate / opponent elsewhere."""

from test_personal import ME, MIRAGE_PLAYERS, MIRAGE_ROUNDS, THEM, app_client, pr, rnd, store  # noqa: F401

MATE = "76561198000000101"
FOE = "76561198000000202"


def test_players_are_split_into_your_team_and_opponents_with_their_lines(app_client):  # noqa: F811
    client, ctx = app_client
    players = MIRAGE_PLAYERS + [pr(1, MATE, "t", kills=1), pr(2, MATE, "t"), pr(3, MATE, "ct", deaths=1, survived=False),
                                pr(4, MATE, "ct")]
    match_id = store(ctx, ME, "a" * 64, "de_mirage", MIRAGE_ROUNDS, players, score=(3, 1))
    report = client.get(f"/matches/{match_id}").json()
    by_id = {p["steam_id"]: p for p in report["players"]}
    assert set(by_id) == {ME, THEM, MATE}
    assert (by_id[ME]["team"], by_id[MATE]["team"], by_id[THEM]["team"]) == ("you", "teammate", "opponent")
    assert by_id[ME] == {"steam_id": ME, "team": "you", "rounds": 4, "first_side": "t", "kills": 6, "deaths": 2,
                         "kd": 3.0, "kills_per_round": 1.5, "opening_kills": 1, "opening_deaths": 1, "survived": 2}
    assert "history" not in by_id[ME]
    assert by_id[THEM]["history"] == {"matches_with": 0, "matches_against": 0}
    assert [p["steam_id"] for p in report["players"]][0] == ME  # most kills first


def test_history_counts_other_matches_with_and_against_each_player(app_client):  # noqa: F811
    client, ctx = app_client
    # Two earlier matches: MATE with ME in both; FOE against in one, with in the other.
    store(ctx, ME, "b" * 64, "de_nuke", [rnd(1, "ct")],
          [pr(1, ME, "ct"), pr(1, MATE, "ct"), pr(1, FOE, "t")])
    store(ctx, ME, "c" * 64, "de_inferno", [rnd(1, "t")],
          [pr(1, ME, "t"), pr(1, MATE, "t"), pr(1, FOE, "t")])
    match_id = store(ctx, ME, "d" * 64, "de_mirage", [rnd(1, "ct")],
                     [pr(1, ME, "ct"), pr(1, MATE, "ct"), pr(1, FOE, "t")])
    players = {p["steam_id"]: p for p in client.get(f"/matches/{match_id}").json()["players"]}
    assert players[MATE]["history"] == {"matches_with": 2, "matches_against": 0}
    assert players[FOE]["history"] == {"matches_with": 1, "matches_against": 1}


def test_not_in_the_demo_groups_by_starting_side_and_has_no_history(app_client):  # noqa: F811
    client, ctx = app_client
    match_id = store(ctx, ME, "e" * 64, "de_mirage", [rnd(1, "ct")], [pr(1, MATE, "ct", kills=2), pr(1, FOE, "t")])
    players = client.get(f"/matches/{match_id}").json()["players"]
    assert [(p["steam_id"], p["team"]) for p in players] == [(MATE, "ct_start"), (FOE, "t_start")]
    assert all("history" not in p for p in players)


def test_matches_parsed_before_player_tracking_have_no_players(app_client):  # noqa: F811
    client, ctx = app_client
    match_id = store(ctx, ME, "f" * 64, "de_mirage", [rnd(1, "ct")], recorded=False)
    assert client.get(f"/matches/{match_id}").json()["players"] == []


def test_history_only_covers_the_users_own_matches(app_client):  # noqa: F811
    client, ctx = app_client
    store(ctx, THEM, "g" * 64, "de_nuke", [rnd(1, "ct")], [pr(1, ME, "ct"), pr(1, MATE, "ct")])  # not in ME's list
    match_id = store(ctx, ME, "h" * 64, "de_mirage", [rnd(1, "ct")], [pr(1, ME, "ct"), pr(1, MATE, "ct")])
    players = {p["steam_id"]: p for p in client.get(f"/matches/{match_id}").json()["players"]}
    assert players[MATE]["history"] == {"matches_with": 0, "matches_against": 0}
