from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_options_lists_model_vocabulary():
    body = client.get("/options").json()
    assert "de_mirage" in body["maps"]
    assert "ak47" in body["weapons"]
    assert body["opening_kill_sides"] == ["ct", "t"]


def test_predict_returns_probabilities():
    response = client.post(
        "/predict",
        json={"map_name": "de_mirage", "opening_kill_side": "t", "opening_kill_seconds": 20, "opening_weapon": "ak47"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["predicted_winner"] in {"ct", "t"}
    assert abs(body["probabilities"]["ct"] + body["probabilities"]["t"] - 1) < 1e-9


def test_predict_rejects_unknown_weapon():
    response = client.post(
        "/predict",
        json={"map_name": "de_mirage", "opening_kill_side": "t", "opening_kill_seconds": 20, "opening_weapon": "nope"},
    )
    assert response.status_code == 422


def test_cors_allows_configured_origin_with_credentials():
    response = client.options(
        "/predict",
        headers={"Origin": "https://csgooner.com", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "https://csgooner.com"
    assert response.headers["access-control-allow-credentials"] == "true"
