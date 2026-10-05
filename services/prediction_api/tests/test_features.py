import pytest

from steamlink.demo_parser import ParsedDeath, ParsedDemo, ParsedRound, extract_rounds, normalize_weapon
from steamlink.scoring import RoundScorer
from steamlink.storage.base import RoundRecord

import main


def demo(deaths, rounds=None, map_name="de_mirage"):
    rounds = rounds or [ParsedRound(1, freeze_end_tick=1000, end_tick=8000, winner_side="ct")]
    return ParsedDemo(map_name=map_name, rounds=rounds, deaths=deaths)


def test_opening_kill_matches_training_definition():
    rounds = [
        ParsedRound(1, 1000, 8000, "ct"),
        ParsedRound(2, 9000, 15000, "t"),
    ]
    deaths = [
        ParsedDeath(1640, "ct", "t", "weapon_usp_silencer"),  # first death of round 1 -> 10s
        ParsedDeath(1700, "t", "ct", "glock"),
        ParsedDeath(10280, "t", "ct", "ak47"),  # round 2 -> 20s
    ]
    records = extract_rounds(demo(deaths, rounds))
    assert records[0] == RoundRecord(1, "ct", "ct", 10.0, "usp_silencer", None)
    assert records[1] == RoundRecord(2, "t", "t", 20.0, "ak47", None)


def test_world_death_uses_victim_side_like_training_data():
    records = extract_rounds(demo([ParsedDeath(1640, None, "t", "world")]))
    assert records[0].opening_kill_side == "t"
    assert records[0].unscored_reason is None


@pytest.mark.parametrize("deaths,rounds,reason", [
    ([], None, "no_opening_kill"),
    ([ParsedDeath(1640, None, "t", "ak47")], None, "opening_kill_side_unknown"),
    ([ParsedDeath(500, "t", "ct", "ak47")], None, "opening_kill_before_freeze_end"),
    ([ParsedDeath(1640, "t", "ct", "ak47")], [ParsedRound(1, None, 8000, "t")], "freeze_end_missing"),
    ([ParsedDeath(1640, "t", "ct", "ak47")], [ParsedRound(1, 1000, 8000, None)], "winner_unknown"),
])
def test_unscorable_rounds_get_reasons(deaths, rounds, reason):
    assert extract_rounds(demo(deaths, rounds))[0].unscored_reason == reason


def test_normalize_weapon():
    assert normalize_weapon("weapon_AK47") == "ak47"
    assert normalize_weapon(None) is None


@pytest.fixture(scope="module")
def scorer():
    return RoundScorer(main.model, main.map_options, main.weapon_options)


def test_scorer_matches_predict_endpoint(scorer):
    rnd = RoundRecord(1, "t", "t", 20.0, "ak47", None)
    score = scorer.score_rounds("de_mirage", [rnd])[0]
    from fastapi.testclient import TestClient
    body = TestClient(main.app).post("/predict", json={
        "map_name": "de_mirage", "opening_kill_side": "t", "opening_kill_seconds": 20.0, "opening_weapon": "ak47",
    }).json()
    assert score.probability_t == pytest.approx(body["probabilities"]["t"])
    assert score.predicted_winner == body["predicted_winner"]


@pytest.mark.parametrize("map_name,rnd,reason", [
    ("de_vertigo", RoundRecord(1, "t", "t", 20.0, "ak47", None), "map_not_in_model"),
    ("de_mirage", RoundRecord(1, "t", "t", 20.0, "bayonet", None), "weapon_not_in_model"),
    ("de_mirage", RoundRecord(1, "t", "t", 130.0, "ak47", None), "opening_kill_time_out_of_range"),
    ("de_mirage", RoundRecord(1, None, None, None, None, "no_opening_kill"), "no_opening_kill"),
])
def test_scorer_unscored_reasons(scorer, map_name, rnd, reason):
    score = scorer.score_rounds(map_name, [rnd])[0]
    assert score.unscored_reason == reason
    assert score.probability_t is None
