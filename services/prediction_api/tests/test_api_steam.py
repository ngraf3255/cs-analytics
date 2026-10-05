from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from steamlink import api, openid
from steamlink.config import Settings
from steamlink.crypto import AuthCodeCipher
from steamlink.sessions import CookieSigner
from steamlink.sync import SyncService

from fakes import KEY, Clock, FakeFetcher, FakeHistory, FakeLocator, FakeParser, code, make_storage

STEAM_ID = "76561198000000001"
AUTH = "AB12-CDE34-FG56"
H = {"X-Requested-With": "csa", "Origin": "https://csgooner.com"}


def make_client(tmp_path):
    settings = Settings(
        allowed_origins=["https://csgooner.com"], database_url="sqlite://", token_encryption_keys=[KEY],
        session_secret="s" * 40, public_api_url="https://api.example.com", frontend_url="https://csgooner.com",
        steam_web_api_key="k", session_cookie_secure=False,
    )
    storage, clock, cipher = make_storage(tmp_path), Clock(), AuthCodeCipher([KEY])
    history = FakeHistory([code(i) for i in range(4)], valid_auth=AUTH)
    steam_http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="ns:http://specs.openid.net/auth/2.0\nis_valid:true\n")))
    sync = SyncService(storage=storage, history=history, locator=FakeLocator(), fetcher=FakeFetcher(),
                       parser=FakeParser(), cipher=cipher, clock=clock, max_matches=5)
    ctx = api.SteamContext(settings=settings, storage=storage, signer=CookieSigner(settings.session_secret),
                           cipher=cipher, history=history, sync=sync,
                           scorer=main.scorer, http=steam_http, clock=clock)
    app = FastAPI()
    api.register(app, ctx)
    return TestClient(app, base_url="https://api.example.com", follow_redirects=False), ctx


def login(client, ctx):
    response = client.get("/auth/steam/login", params={"next": "/matches"})
    assert response.status_code == 302
    state = parse_qs(parse_qs(urlsplit(response.headers["location"]).query)["openid.return_to"][0]
                     .split("?", 1)[1])["state"][0]
    claimed = f"https://steamcommunity.com/openid/id/{STEAM_ID}"
    params = {
        "state": state, "openid.ns": openid.OPENID_NS, "openid.mode": "id_res",
        "openid.op_endpoint": openid.STEAM_OPENID_ENDPOINT, "openid.claimed_id": claimed,
        "openid.identity": claimed, "openid.return_to": openid.build_return_to(ctx.settings.openid_return_url, state),
        "openid.response_nonce": ctx.clock().strftime("%Y-%m-%dT%H:%M:%SZ") + "n",
        "openid.assoc_handle": "1", "openid.sig": "x",
        "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
    }
    response = client.get("/auth/steam/callback", params=params)
    assert response.status_code == 302
    assert response.headers["location"] == "https://csgooner.com/matches"
    return response


@pytest.fixture()
def app_client(tmp_path):
    client, ctx = make_client(tmp_path)
    login(client, ctx)
    return client, ctx


def test_disabled_by_default_in_main_app():
    client = TestClient(main.app)
    assert client.get("/steam/status").json() == {"enabled": False}
    assert client.get("/me").status_code == 503
    assert client.post("/steam/sync", headers=H).json()["detail"] == "steam_sync_disabled"


def test_unauthenticated_requests_rejected(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.get("/me").status_code == 401
    assert client.get("/matches").status_code == 401


def test_callback_without_login_state_fails_safely(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get("/auth/steam/callback", params={"state": "x"})
    assert "steam_login=failed" in response.headers["location"]
    assert client.get("/me").status_code == 401


def test_full_flow_link_sync_report_delete(app_client):
    client, ctx = app_client
    me = client.get("/me").json()
    assert me["steam_id"] == STEAM_ID and me["match_access"]["linked"] is False

    body = {"auth_code": AUTH, "share_code": code(0), "consent": True}
    assert client.put("/steam/match-access", json=body).status_code == 403  # no CSRF header
    response = client.put("/steam/match-access", json=body, headers=H)
    assert response.status_code == 200
    assert response.json()["auth_code_hint"] == "****-*****-FG56"
    assert AUTH not in str(ctx.storage.get_match_access(ctx.storage.get_or_create_user(STEAM_ID, ctx.clock()).id))

    sync = client.post("/steam/sync", headers=H).json()
    assert sync == {"status": "up_to_date", "imported": 3, "processed": 3, "has_more": False, "error": None}
    assert client.post("/steam/sync", headers=H).status_code == 429  # too soon
    assert client.get("/steam/sync").json()["status"] == "ok"

    matches = client.get("/matches").json()["matches"]
    assert len(matches) == 3
    report = client.get(f"/matches/{matches[0]['id']}").json()
    assert report["model"]["calibrated_for_matchmaking"] is False
    first, second = report["rounds"]
    assert first["actual_winner"] == "t" and first["prediction"]["probabilities"]["t"] > 0
    assert first["opening_kill"] == {"side": "t", "seconds": 20.0, "weapon": "ak47"}
    assert second["prediction"] is None and second["unscored_reason"] == "weapon_not_in_model"
    assert report["summary"]["scored"] == 1
    assert client.get("/matches/nope").status_code == 404

    assert client.delete("/steam/match-access", headers=H).status_code == 204
    assert client.get("/me").json()["match_access"]["linked"] is False
    assert client.post("/steam/sync", headers=H).status_code in (409, 429)

    assert client.delete("/me", headers=H).status_code == 204
    assert client.get("/me").status_code == 401


@pytest.mark.parametrize("body,detail", [
    ({"auth_code": AUTH, "share_code": code(0), "consent": False}, "consent_required"),
    ({"auth_code": "bad", "share_code": code(0), "consent": True}, "invalid_auth_code_format"),
    ({"auth_code": AUTH, "share_code": "CSGO-bad", "consent": True}, "invalid_share_code_format"),
    ({"auth_code": "ZZ12-CDE34-FG56", "share_code": code(0), "consent": True}, "invalid_auth_code"),
    ({"auth_code": AUTH, "share_code": code(99), "consent": True}, "invalid_share_code"),
])
def test_link_validation(app_client, body, detail):
    client, _ = app_client
    response = client.put("/steam/match-access", json=body, headers=H)
    assert response.status_code == 422 and response.json()["detail"] == detail


def test_disallowed_origin_rejected(app_client):
    client, _ = app_client
    response = client.post("/steam/sync", headers={"X-Requested-With": "csa", "Origin": "https://evil.com"})
    assert response.status_code == 403


def test_logout_revokes_session(app_client):
    client, _ = app_client
    assert client.post("/auth/logout", headers=H).status_code == 204
    assert client.get("/me").status_code == 401
