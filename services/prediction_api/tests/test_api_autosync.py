"""API surface of automatic background sync: the opt-out toggle and the auto_sync
status in GET /steam/sync, /me and /steam/status (steamlink.api / steamlink.autosync)."""

from datetime import timedelta

from steamlink.config import Settings
from steamlink.valve import UnconfiguredDemoLocator

from fakes import code
from test_api_steam import AUTH, H, login, make_client

LINK = {"auth_code": AUTH, "share_code": code(0), "consent": True}


def client_for(tmp_path):
    client, ctx = make_client(tmp_path)
    login(client, ctx)
    return client, ctx


def test_status_shows_last_synced_next_auto_sync_and_errors(tmp_path):
    client, ctx = client_for(tmp_path)
    view = client.get("/steam/sync").json()
    assert view["last_synced_at"] is None
    assert view["auto_sync"] == {"enabled": True, "active": False, "paused_reason": "not_linked",
                                 "interval_seconds": 1800, "next_at": None, "last_run_at": None,
                                 "last_error": None, "failures": 0}
    assert client.put("/steam/match-access", json=LINK, headers=H).status_code == 200
    auto = client.get("/me").json()["sync"]["auto_sync"]
    assert auto["active"] and auto["paused_reason"] is None
    assert auto["next_at"] == ctx.clock().isoformat().replace("+00:00", "Z")  # never synced: due now

    ctx.clock.advance(60)
    response = client.post("/steam/sync", headers=H)  # the Sync button
    assert response.status_code == 202
    ctx.jobs.wait_idle(30)
    view = client.get("/steam/sync").json()
    synced_at = ctx.clock()
    assert view["last_synced_at"] == synced_at.isoformat().replace("+00:00", "Z")
    # up to date (3 new matches, max_matches=5): the next automatic sync is a full interval away
    state = ctx.storage.get_sync_state(ctx.storage.get_or_create_user("76561198000000001", synced_at).id, synced_at)
    assert view["auto_sync"]["next_at"] == state.next_auto_sync_at.isoformat().replace("+00:00", "Z")
    assert state.next_auto_sync_at - synced_at >= timedelta(seconds=1800)
    assert view["auto_sync"]["last_run_at"] is None  # that was a manual sync

    # an automatic run that hits Valve's rate limit shows up as last_error + failures
    ctx.clock.advance(2000)
    ctx.history.forced = "rate_limited"
    result = ctx.auto_sync.run_tick()
    assert result.stopped == "rate_limited"
    auto = client.get("/steam/sync").json()["auto_sync"]
    assert (auto["last_error"], auto["failures"], auto["active"]) == ("rate_limited", 1, True)
    assert auto["last_run_at"] == ctx.clock().isoformat().replace("+00:00", "Z")


def test_opt_out_toggle(tmp_path):
    client, ctx = client_for(tmp_path)
    assert client.put("/steam/match-access", json=LINK, headers=H).status_code == 200
    assert client.put("/steam/auto-sync", json={"enabled": False}).status_code == 403  # CSRF header required
    response = client.put("/steam/auto-sync", json={"enabled": False}, headers=H)
    assert response.status_code == 200
    body = response.json()
    assert (body["enabled"], body["active"], body["paused_reason"], body["next_at"]) == (
        False, False, "turned_off", None)
    assert client.get("/me").json()["sync"]["auto_sync"]["enabled"] is False
    ctx.clock.advance(60)
    assert ctx.auto_sync.run_tick().synced == []  # opted out
    assert client.put("/steam/auto-sync", json={"enabled": "nope"}, headers=H).status_code == 422
    body = client.put("/steam/auto-sync", json={"enabled": True}, headers=H).json()
    assert body["enabled"] and body["active"]
    assert len(ctx.auto_sync.run_tick().synced) == 1


def test_needs_relink_pauses_auto_sync(tmp_path):
    client, ctx = client_for(tmp_path)
    assert client.put("/steam/match-access", json=LINK, headers=H).status_code == 200
    ctx.clock.advance(60)
    ctx.history.forced = "invalid_known_code"
    assert client.post("/steam/sync", headers=H).json()["error"] == "invalid_known_code"
    auto = client.get("/steam/sync").json()["auto_sync"]
    assert (auto["active"], auto["paused_reason"], auto["next_at"]) == (False, "needs_relink", None)


def test_server_side_states(tmp_path):
    client, ctx = client_for(tmp_path)
    status = client.get("/steam/status").json()
    assert status == {"enabled": True, "auto_sync": {"enabled": True, "available": True, "interval_seconds": 1800}}
    ctx.sync.locator = UnconfiguredDemoLocator()
    assert client.get("/steam/sync").json()["auto_sync"]["paused_reason"] == "demo_retrieval_not_configured"
    assert client.get("/steam/status").json()["auto_sync"]["available"] is False
    ctx.settings = Settings(**{**ctx.settings.__dict__, "auto_sync_interval_seconds": 0})
    auto = client.get("/steam/sync").json()["auto_sync"]
    assert (auto["paused_reason"], auto["interval_seconds"]) == ("server_disabled", None)
    assert client.get("/steam/status").json()["auto_sync"] == {"enabled": False, "available": False,
                                                               "interval_seconds": None}


def test_lifespan_starts_and_stops_the_scheduler(tmp_path):
    from fastapi import FastAPI

    from steamlink import api

    _, ctx = make_client(tmp_path)
    app = FastAPI()
    app.state.steam = ctx
    api.start_background_work(app)
    assert ctx.auto_sync.running
    api.stop_background_work(app)
    assert not ctx.auto_sync.running
