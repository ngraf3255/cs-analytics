"""Linking match history the way users actually paste codes (Leetify-style onboarding):
pasted-form normalisation, codes swapped between the two boxes, share-code-only re-link, and
the ``needs_relink`` state after a sync that only new codes can fix."""

import pytest

from steamlink.sharecode import ShareCode, encode, extract_share_code, is_valid_share_code
from steamlink.valve import normalize_auth_code

from fakes import code
from test_api_steam import AUTH, H, STEAM_ID, app_client  # noqa: F401 (fixture)

SHARE = "CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xK"
LINK = "steam://rungame/730/76561202255233023/+csgo_download_match%20" + SHARE


# --- pure helpers -----------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    SHARE, f"  {SHARE}\n", LINK, LINK.replace("%20", " "), f'"{SHARE}"', f"Match code: {SHARE}.",
])
def test_extract_share_code_from_pasted_text(text):
    assert extract_share_code(text) == SHARE


@pytest.mark.parametrize("text", [
    "", None, "CSGO-bad", SHARE.lower(), SHARE + "x", "x" + SHARE, f"{SHARE} {code(1)}", AUTH,
])
def test_extract_share_code_rejects(text):
    assert extract_share_code(text) is None


def test_extract_share_code_same_code_twice_is_fine():
    assert extract_share_code(f"{SHARE} {SHARE}") == SHARE


def test_out_of_range_share_code_is_not_valid():
    biggest = "CSGO-" + "-".join(["99999"] * 5)  # well-formed but above 2**144
    assert not is_valid_share_code(biggest)
    assert is_valid_share_code(encode(ShareCode(2**64 - 1, 2**64 - 1, 2**16 - 1)))


@pytest.mark.parametrize("text, expected", [
    ("AB12-CDE34-FG56", "AB12-CDE34-FG56"),
    ("ab12-cde34-fg56", "AB12-CDE34-FG56"),
    (" AB12 CDE34 FG56 ", "AB12-CDE34-FG56"),
    ("ab12cde34fg56", "AB12-CDE34-FG56"),
    ("AB12-CDE34", "AB12-CDE34"),  # left alone: the format check rejects it
])
def test_normalize_auth_code(text, expected):
    assert normalize_auth_code(text) == expected


# --- PUT /steam/match-access -------------------------------------------------------------

def link(client, auth=AUTH, share=None, consent=True):
    return client.put("/steam/match-access", json={"auth_code": auth, "share_code": share or code(0), "consent": consent},
                      headers=H)


def cursor(ctx):
    user = ctx.storage.get_or_create_user(STEAM_ID, ctx.clock())
    return ctx.storage.get_match_access(user.id).cursor_share_code


def test_link_accepts_lowercase_auth_code_without_dashes_and_a_steam_share_link(app_client):
    client, ctx = app_client
    pasted = "steam://rungame/730/76561202255233023/+csgo_download_match%20" + code(0)
    response = link(client, auth=" ab12cde34fg56 ", share=pasted)
    assert response.status_code == 200, response.text
    assert response.json()["auth_code_hint"] == "****-*****-FG56"
    assert cursor(ctx) == code(0)
    assert ctx.history.calls[-1] == code(0)


@pytest.mark.parametrize("auth, share, detail", [
    (code(0), code(0), "auth_code_is_share_code"),
    (AUTH, AUTH, "share_code_is_auth_code"),
    (AUTH, "ab12cde34fg56", "share_code_is_auth_code"),
    ("", code(0), "auth_code_required"),  # not linked yet: the auth code can't be kept
    ("   ", code(0), "auth_code_required"),
])
def test_link_names_the_field_to_fix(app_client, auth, share, detail):
    client, ctx = app_client
    response = link(client, auth=auth, share=share)
    assert response.status_code == 422 and response.json()["detail"] == detail
    assert ctx.history.calls == []  # rejected before asking Valve


def test_upload_share_code_hint_accepts_a_steam_link(app_client):
    client, _ = app_client
    pasted = "steam://rungame/730/1/+csgo_download_match%20" + code(0)
    response = client.post("/matches/upload", params={"share_code": pasted}, content=b"not a demo",
                           headers={**H, "Content-Type": "application/octet-stream"})
    assert response.json()["detail"] != "invalid_share_code_format"


# --- needs_relink + share-code-only update -------------------------------------------

def sync_with(ctx, client, status):
    ctx.history.forced = status
    ctx.clock.advance(60)
    response = client.post("/steam/sync", headers=H)
    ctx.history.forced = None
    return response


def test_expired_cursor_asks_for_a_new_share_code_and_keeps_the_auth_code(app_client):
    client, ctx = app_client
    assert link(client).status_code == 200
    assert client.get("/me").json()["match_access"]["needs_relink"] is None

    response = sync_with(ctx, client, "invalid_known_code")  # e.g. no match played for 30+ days
    assert response.json()["status"] == "error" and response.json()["error"] == "invalid_known_code"
    access = client.get("/me").json()["match_access"]
    assert access["linked"] is True
    assert access["needs_relink"] == {"reason": "invalid_known_code", "field": "share_code"}

    ctx.clock.advance(5)
    response = link(client, auth="", share=code(2))  # new share code only
    assert response.status_code == 200, response.text
    assert response.json()["needs_relink"] is None
    assert response.json()["auth_code_hint"] == "****-*****-FG56"
    assert cursor(ctx) == code(2)
    assert client.get("/me").json()["match_access"]["needs_relink"] is None

    ctx.clock.advance(60)
    sync = client.post("/steam/sync", headers=H).json()
    assert sync["status"] == "up_to_date" and [j["share_code"] for j in sync["jobs"]] == [code(3)]


def test_revoked_auth_code_asks_for_a_new_auth_code(app_client):
    client, ctx = app_client
    assert link(client).status_code == 200
    sync_with(ctx, client, "invalid_auth_code")
    assert client.get("/me").json()["match_access"]["needs_relink"] == {"reason": "invalid_auth_code", "field": "auth_code"}

    # Keeping the stored (revoked) code: Valve still rejects it, nothing changes.
    ctx.history.valid_auth = "NEW1-CODE2-AB34"
    ctx.clock.advance(5)
    response = link(client, auth="", share=code(0))
    assert response.status_code == 422 and response.json()["detail"] == "invalid_auth_code"
    assert client.get("/me").json()["match_access"]["needs_relink"]["field"] == "auth_code"

    response = link(client, auth="new1code2ab34", share=code(0))
    assert response.status_code == 200 and response.json()["auth_code_hint"] == "****-*****-AB34"
    assert response.json()["needs_relink"] is None


def test_transient_sync_errors_do_not_ask_for_new_codes(app_client):
    client, ctx = app_client
    assert link(client).status_code == 200
    sync_with(ctx, client, "rate_limited")
    assert client.get("/me").json()["match_access"]["needs_relink"] is None


def test_disconnect_then_relink(app_client):
    client, ctx = app_client
    assert link(client).status_code == 200
    sync_with(ctx, client, "invalid_known_code")
    assert client.delete("/steam/match-access", headers=H).status_code == 204
    me = client.get("/me").json()["match_access"]
    assert me == {"linked": False, "auth_code_hint": None, "linked_at": None, "updated_at": None, "needs_relink": None,
                  "awaiting_share_code": False}
    assert link(client, auth="", share=code(1)).json()["detail"] == "auth_code_required"  # stored code is gone
    ctx.clock.advance(5)
    response = link(client, share=code(1))
    assert response.status_code == 200 and response.json()["needs_relink"] is None
    assert cursor(ctx) == code(1)
