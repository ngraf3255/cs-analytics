"""Demo upload without Steam: feature flags (steamlink.config), guest sessions (POST /auth/guest)
and the Steam routes staying off on an upload-only server."""

from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from steamlink import api
from steamlink.config import ConfigError, Settings, load_settings
from steamlink.sessions import CookieSigner
from steamlink.sync import SyncService
from steamlink.valve import UnconfiguredDemoLocator

from fakes import KEY, Clock, FakeFetcher, FakeParser, make_storage
from test_api_steam import H, OCTET, login, make_client, upload_and_wait

DEMO = b"PBDEMS2\0" + b"\x01" * 4096
SECRET = "s" * 40
DB = {"DATABASE_URL": "sqlite://", "SESSION_SECRET": SECRET}
STEAM = {**DB, "TOKEN_ENCRYPTION_KEYS": KEY, "STEAM_WEB_API_KEY": "k",
         "PUBLIC_API_URL": "https://api.example.com", "FRONTEND_URL": "https://example.com"}


# --- settings ------------------------------------------------------------------------

def test_nothing_without_database():
    s = load_settings({"SESSION_SECRET": SECRET})
    assert not s.demo_upload_enabled and not s.steam_enabled and not s.guest_uploads_enabled
    assert "DATABASE_URL" in s.startup_notes()[0]


def test_database_and_session_secret_enable_upload_only():
    s = load_settings(DB)
    assert s.demo_upload_enabled and not s.steam_enabled
    # Guest (anonymous) uploads are off unless explicitly opted in.
    assert not s.guest_uploads and not s.guest_uploads_enabled
    assert any("Steam sign-in and sync are off" in note and "need a Steam sign-in" in note
               for note in s.startup_notes())


def test_guest_uploads_default_off_with_steam():
    s = load_settings(STEAM)
    assert s.steam_enabled and s.demo_upload_enabled and not s.guest_uploads_enabled


def test_encryption_key_without_steam_key_runs_upload_only():
    # Used to refuse to start (crash-loop on the homelab); now Steam just stays off.
    s = load_settings({**DB, "TOKEN_ENCRYPTION_KEYS": KEY, "PUBLIC_API_URL": "https://api.example.com"})
    assert s.demo_upload_enabled and not s.steam_enabled


def test_full_steam_config_enables_steam():
    s = load_settings(STEAM)
    assert s.steam_enabled and s.demo_upload_enabled and s.startup_notes() == []


@pytest.mark.parametrize("env", [
    {**DB, "STEAM_WEB_API_KEY": "k"},  # key without encryption keys
    {"DATABASE_URL": "sqlite://", "TOKEN_ENCRYPTION_KEYS": KEY, "STEAM_WEB_API_KEY": "k"},  # no session secret
    {k: v for k, v in STEAM.items() if k != "FRONTEND_URL"},
    {**DB, "FRONTEND_URL": "not a url"},
])
def test_half_configured_fails_loudly(env):
    with pytest.raises(ConfigError):
        load_settings(env)


def test_short_session_secret_leaves_upload_off_instead_of_crashing():
    s = load_settings({"DATABASE_URL": "sqlite://", "SESSION_SECRET": "short"})
    assert not s.demo_upload_enabled
    assert "SESSION_SECRET" in s.startup_notes()[0]


def test_guest_uploads_opt_in():
    s = load_settings({**DB, "GUEST_UPLOADS": "true"})
    assert s.demo_upload_enabled and s.guest_uploads_enabled
    assert any("guest demo upload is on" in note for note in s.startup_notes())
    assert not load_settings({**DB, "GUEST_UPLOADS": "false"}).guest_uploads_enabled


def test_build_context_without_steam_has_no_steam_pieces(tmp_path):
    s = replace(load_settings(DB), database_url=f"sqlite:///{tmp_path / 'x.db'}")
    ctx = api.build_steam_context(s, main.scorer)
    assert ctx.cipher is None and ctx.history is None and not ctx.steam_enabled
    assert isinstance(ctx.sync.locator, UnconfiguredDemoLocator)
    app = FastAPI()
    api.register(app, ctx)
    assert ctx.jobs is not None and ctx.auto_sync is None


# --- upload-only server --------------------------------------------------------------

def upload_only_client(tmp_path, **overrides):
    overrides.setdefault("guest_uploads", True)  # these tests exercise the opt-in guest flow
    settings = Settings(
        allowed_origins=["https://csgooner.com"], database_url="sqlite://", session_secret=SECRET,
        session_cookie_secure=False, upload_job_dir=str(tmp_path / "upload-jobs"), **overrides,
    )
    storage, clock = make_storage(tmp_path), Clock()
    sync = SyncService(storage=storage, history=None, locator=UnconfiguredDemoLocator(), fetcher=FakeFetcher(),
                       parser=FakeParser(), cipher=None, clock=clock)
    ctx = api.SteamContext(settings=settings, storage=storage, signer=CookieSigner(SECRET), cipher=None,
                           history=None, sync=sync, scorer=main.scorer,
                           http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
                           clock=clock)
    app = FastAPI()
    api.register(app, ctx)
    return TestClient(app, base_url="https://api.example.com", follow_redirects=False), ctx


def test_status_reports_upload_without_steam(tmp_path):
    client, ctx = upload_only_client(tmp_path)
    status = client.get("/steam/status").json()
    assert status["enabled"] is False and status["steam"] is False
    assert status["upload"] is True and status["guest"] is True
    assert ctx.auto_sync is None


def test_steam_routes_are_off(tmp_path):
    client, _ = upload_only_client(tmp_path)
    assert client.get("/auth/steam/login").json()["detail"] == "steam_sync_disabled"
    assert client.get("/auth/steam/callback").status_code == 503
    client.post("/auth/guest", headers=H)
    assert client.post("/steam/sync", headers=H).json()["detail"] == "steam_sync_disabled"
    assert client.get("/steam/sync").status_code == 503
    body = {"auth_code": "AB12-CDE34-FG56", "share_code": "x", "consent": True}
    assert client.put("/steam/match-access", json=body, headers=H).status_code == 503
    assert client.put("/steam/auto-sync", json={"enabled": False}, headers=H).status_code == 503


def test_guest_session_needs_csrf_header(tmp_path):
    client, _ = upload_only_client(tmp_path)
    assert client.post("/auth/guest").status_code == 403
    assert client.post("/auth/guest", headers={**H, "Origin": "https://evil.example"}).status_code == 403


def test_guest_uploads_and_views_report(tmp_path):
    client, ctx = upload_only_client(tmp_path)
    assert client.get("/me").status_code == 401
    response = client.post("/auth/guest", headers=H)
    assert response.status_code == 200
    me = response.json()
    assert me["account"] == "guest" and me["steam_id"] is None and me["match_access"]["linked"] is False
    assert client.get("/me").json()["account"] == "guest"
    # A second call keeps the same session / account.
    assert client.post("/auth/guest", headers=H).json()["created_at"] == me["created_at"]
    assert ctx.storage.find_user("76561198000000001") is None

    post, job = upload_and_wait(client, ctx, DEMO, headers=OCTET)
    assert post.status_code == 202 and job["status"] == "done" and job["created"] is True
    matches = client.get("/matches").json()["matches"]
    assert len(matches) == 1 and matches[0]["source"] == "upload"
    report = client.get(f"/matches/{matches[0]['id']}").json()
    assert report["summary"]["rounds"] == 2 and "you" not in report
    assert all(r["you"] is None for r in report["rounds"])
    summary = client.get("/matches/summary").json()
    assert "you" not in summary
    assert client.get("/matches/export/rounds.csv").status_code == 200

    assert client.post("/auth/logout", headers=H).status_code == 204
    assert client.get("/me").status_code == 401
    assert client.get("/matches").status_code == 401


def test_guest_can_delete_their_data(tmp_path):
    client, ctx = upload_only_client(tmp_path)
    client.post("/auth/guest", headers=H)
    upload_and_wait(client, ctx, DEMO, headers=OCTET)
    assert client.delete("/me", headers=H).status_code == 204
    assert client.get("/me").status_code == 401


def test_guest_uploads_off(tmp_path):
    client, _ = upload_only_client(tmp_path, guest_uploads=False)
    response = client.post("/auth/guest", headers=H)
    assert response.status_code == 503 and response.json()["detail"] == "guest_uploads_disabled"
    status = client.get("/steam/status").json()
    assert status["upload"] is False and status["guest"] is False


# --- Steam on: guests coexist, Steam flow unchanged ----------------------------------

def test_steam_server_default_has_no_guest_sessions(tmp_path):
    client, ctx = make_client(tmp_path)
    status = client.get("/steam/status").json()
    assert status["steam"] is True and status["upload"] is True and status["guest"] is False
    response = client.post("/auth/guest", headers=H)
    assert response.status_code == 503 and response.json()["detail"] == "guest_uploads_disabled"
    # Anonymous upload is rejected; a Steam-signed-in user can upload.
    assert client.post("/matches/upload", content=DEMO, headers=OCTET).status_code == 401
    login(client, ctx)
    post, job = upload_and_wait(client, ctx, DEMO, headers=OCTET)
    assert post.status_code == 202 and job["status"] == "done"


def test_guest_on_steam_server_cannot_use_steam_routes(tmp_path):
    client, ctx = make_client(tmp_path)
    ctx.settings = replace(ctx.settings, guest_uploads=True)
    me = client.post("/auth/guest", headers=H).json()
    assert me["account"] == "guest"
    response = client.post("/steam/sync", headers=H)
    assert response.status_code == 403 and response.json()["detail"] == "steam_sign_in_required"
    assert client.get("/steam/sync").status_code == 403


def test_steam_login_still_works_and_is_not_a_guest(tmp_path):
    client, ctx = make_client(tmp_path)
    ctx.settings = replace(ctx.settings, guest_uploads=True)
    assert client.get("/steam/status").json()["steam"] is True
    login(client, ctx)
    me = client.get("/me").json()
    assert me["account"] == "steam" and me["steam_id"] == "76561198000000001"
    # Already signed in with Steam: POST /auth/guest keeps that session.
    assert client.post("/auth/guest", headers=H).json()["account"] == "steam"
    assert client.get("/steam/sync").status_code == 200
