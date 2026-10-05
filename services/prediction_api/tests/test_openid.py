from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from steamlink import openid
from steamlink.sessions import CookieSigner, safe_next_path

RETURN_URL = "https://api.example.com/auth/steam/callback"
STEAM_ID = "76561198000000001"
NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


def callback_params(state="abc", **overrides):
    claimed = f"https://steamcommunity.com/openid/id/{STEAM_ID}"
    params = {
        "state": state,
        "openid.ns": openid.OPENID_NS,
        "openid.mode": "id_res",
        "openid.op_endpoint": openid.STEAM_OPENID_ENDPOINT,
        "openid.claimed_id": claimed,
        "openid.identity": claimed,
        "openid.return_to": openid.build_return_to(RETURN_URL, state),
        "openid.response_nonce": "2026-10-04T11:59:30Zxyz",
        "openid.assoc_handle": "1234567890",
        "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
        "openid.sig": "sig",
    }
    params.update(overrides)
    return params


def steam_client(body="ns:http://specs.openid.net/auth/2.0\nis_valid:true\n", status=200, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_login_url_uses_exact_return_url_and_identifier_select():
    url = openid.build_login_url(return_url=RETURN_URL, realm="https://api.example.com/", state="s1")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == openid.STEAM_OPENID_ENDPOINT
    query = parse_qs(parts.query)
    assert query["openid.return_to"] == [RETURN_URL + "?state=s1"]
    assert query["openid.claimed_id"] == [openid.IDENTIFIER_SELECT]
    assert query["openid.mode"] == ["checkid_setup"]


def test_valid_callback_returns_steam_id_and_checks_with_steam():
    seen = []
    steam_id = openid.verify_callback(
        callback_params(), expected_state="abc", return_url=RETURN_URL, http=steam_client(seen=seen), now=NOW
    )
    assert steam_id == STEAM_ID
    assert len(seen) == 1
    sent = parse_qs(seen[0].content.decode())
    assert sent["openid.mode"] == ["check_authentication"]
    assert "state" not in sent


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"openid.return_to": "https://evil.example.com/auth/steam/callback?state=abc"}, "return_to_mismatch"),
        ({"openid.op_endpoint": "https://evil.example.com/openid/login"}, "bad_op_endpoint"),
        ({"openid.claimed_id": "https://evil.example.com/openid/id/76561198000000001"}, "bad_claimed_id"),
        ({"openid.identity": "https://steamcommunity.com/openid/id/76561198000000002"}, "bad_claimed_id"),
        ({"openid.signed": "op_endpoint,claimed_id"}, "unsigned_fields"),
        ({"openid.response_nonce": "2026-10-04T10:00:00Zold"}, "stale_nonce"),
        ({"openid.mode": "cancel"}, "not_id_res"),
    ],
)
def test_tampered_callbacks_are_rejected_before_contacting_steam(overrides, reason):
    seen = []
    with pytest.raises(openid.OpenIDError) as exc:
        openid.verify_callback(
            callback_params(**overrides), expected_state="abc", return_url=RETURN_URL,
            http=steam_client(seen=seen), now=NOW,
        )
    assert exc.value.reason == reason
    assert seen == []


def test_state_mismatch_is_rejected():
    with pytest.raises(openid.OpenIDError) as exc:
        openid.verify_callback(
            callback_params(state="other"), expected_state="abc", return_url=RETURN_URL,
            http=steam_client(), now=NOW,
        )
    assert exc.value.reason == "state_mismatch"


def test_steam_saying_not_valid_is_rejected():
    with pytest.raises(openid.OpenIDError) as exc:
        openid.verify_callback(
            callback_params(), expected_state="abc", return_url=RETURN_URL,
            http=steam_client(body="ns:http://specs.openid.net/auth/2.0\nis_valid:false\n"), now=NOW,
        )
    assert exc.value.reason == "not_valid"


def test_login_state_cookie_roundtrip_and_tamper():
    signer = CookieSigner("x" * 40)
    state, cookie = signer.new_login_state("/matches")
    assert signer.read_login_state(cookie, max_age=600) == state
    assert signer.read_login_state(cookie + "x", max_age=600) is None
    assert CookieSigner("y" * 40).read_login_state(cookie, max_age=600) is None


def test_session_cookie_hash_matches_and_rejects_forgery():
    signer = CookieSigner("x" * 40)
    cookie, token_hash = signer.new_session_cookie()
    assert signer.session_token_hash(cookie, max_age=3600) == token_hash
    assert signer.session_token_hash("forged.value.sig", max_age=3600) is None


@pytest.mark.parametrize("value,expected", [
    ("/matches/1", "/matches/1"), ("//evil.com", "/"), ("https://evil.com", "/"), (None, "/"), ("/\\evil", "/"),
])
def test_safe_next_path(value, expected):
    assert safe_next_path(value) == expected
