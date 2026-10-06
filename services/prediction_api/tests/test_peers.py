"""GET /matches/peers: you vs the other players in your own imported matches."""

from test_personal import ME, THEM, app_client, pr, rnd, store  # noqa: F401

P = [f"7656119790000{n:04d}" for n in range(1, 10)]


def lobby(ctx, key, map_name, my_kills):
    """1-round match: ME on CT (won) with my_kills; 9 others with 1 kill each, 1 death each."""
    players = [pr(1, ME, "ct", kills=my_kills, ok=True)]
    players += [pr(1, sid, "ct" if i < 4 else "t", kills=1, deaths=1, survived=False, od=(i == 4)) for i, sid in enumerate(P)]
    return store(ctx, ME, key, map_name, [rnd(1, "ct", side="ct")], players)


def test_you_vs_lobby_overall_and_per_map_with_percentiles(app_client):  # noqa: F811
    client, ctx = app_client
    lobby(ctx, "a" * 64, "de_mirage", 3)
    lobby(ctx, "b" * 64, "de_nuke", 0)
    body = client.get("/matches/peers").json()
    assert body["basis"] == "lobby"
    overall = body["overall"]
    assert overall["matches"] == 2
    assert overall["you"]["kills_per_round"] == 1.5 and overall["you"]["lines"] == 2
    assert overall["peers"]["lines"] == 18 and overall["peers"]["kills_per_round"] == 1.0
    assert overall["peers"]["round_win_rate"] == round(8 / 18, 4)  # 4 CT teammates of 9 won each round
    assert overall["percentiles"]["kills_per_round"] == 100  # 1.5 beats every 1.0 line
    maps = {m["map_name"]: m for m in body["maps"]}
    assert maps["de_mirage"]["percentiles"]["kills_per_round"] == 100
    assert maps["de_nuke"]["percentiles"]["kills_per_round"] == 0


def test_lobbies_you_are_not_in_and_other_users_matches_are_ignored(app_client):  # noqa: F811
    client, ctx = app_client
    store(ctx, ME, "c" * 64, "de_mirage", [rnd(1, "ct")], [pr(1, P[0], "ct", kills=5)])  # pro demo, not in it
    store(ctx, THEM, "d" * 64, "de_mirage", [rnd(1, "ct")], [pr(1, ME, "ct"), pr(1, P[1], "t")])  # THEM's list only
    body = client.get("/matches/peers").json()
    assert body["overall"]["matches"] == 0 and body["overall"]["you"] is None and body["maps"] == []
    assert body["overall"]["percentiles"] == {"kills_per_round": None, "kd": None}


def test_too_few_peers_gives_no_percentile(app_client):  # noqa: F811
    client, ctx = app_client
    store(ctx, ME, "e" * 64, "de_mirage", [rnd(1, "ct")], [pr(1, ME, "ct", kills=1), pr(1, P[0], "t", deaths=1)])
    body = client.get("/matches/peers").json()
    assert body["overall"]["peers"]["lines"] == 1 and body["overall"]["percentiles"]["kd"] is None
