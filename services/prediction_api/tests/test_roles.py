"""GET /matches/summary ``you.roles`` (per-side opening-duel involvement vs the 5v5 average)
and the opening duels added to ``you.maps``."""

from test_personal import ME, MIRAGE_PLAYERS, MIRAGE_ROUNDS, app_client, pr, rnd, store  # noqa: F401


def test_maps_carry_opening_duels(app_client):  # noqa: F811
    client, ctx = app_client
    store(ctx, ME, "a" * 64, "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS, score=(3, 1))
    you = client.get("/matches/summary").json()["you"]
    mirage = you["maps"][0]
    assert (mirage["opening_kills"], mirage["opening_deaths"]) == (1, 1)
    assert (mirage["opening_attempt_rate"], mirage["opening_win_rate"]) == (0.5, 0.5)
    assert you["roles"]["baseline_opening_attempt_rate"] == 0.2
    # 2 rounds per side: too few to call a role
    assert you["roles"]["t"]["rounds"] == 2 and you["roles"]["t"]["role"] is None


def test_roles_per_side_from_opening_duel_involvement(app_client):  # noqa: F811
    client, ctx = app_client
    # 24 T rounds taking an opening duel in 10 of them (0.42 > 0.28: entry); 24 CT rounds with
    # 2 duels (0.08 < 0.12: support).
    rounds = [rnd(n, "t" if n <= 24 else "ct") for n in range(1, 49)]
    players = [pr(n, ME, "t", kills=1 if n <= 6 else 0, ok=n <= 6, od=6 < n <= 10, deaths=1 if 6 < n <= 10 else 0)
               for n in range(1, 25)]
    players += [pr(n, ME, "ct", ok=n in (25, 26), kills=1 if n in (25, 26) else 0) for n in range(25, 49)]
    store(ctx, ME, "b" * 64, "de_nuke", rounds, players)
    roles = client.get("/matches/summary").json()["you"]["roles"]
    assert roles["t"] | {"kd": None} == {"rounds": 24, "role": "entry", "kd": None, "kills_per_round": 0.25,
                                         "survival_rate": 1.0, "opening_kills": 6, "opening_deaths": 4,
                                         "opening_attempt_rate": round(10 / 24, 4), "opening_win_rate": 0.6}
    assert roles["ct"]["role"] == "support" and roles["ct"]["opening_attempt_rate"] == round(2 / 24, 4)
