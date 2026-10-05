"""Server-side Steam OpenID 2.0 sign-in.

Steam OpenID only proves which SteamID the visitor controls. It never grants
access to match data, and this module never asks for Steam credentials.

Verification rules:
* ``state`` must match the one-time value stored in the signed login-state cookie;
* ``openid.return_to`` must equal the exact return URL we generated;
* ``openid.op_endpoint`` must be Steam's endpoint and the security-relevant
  fields must be covered by ``openid.signed``;
* the response is re-verified server-side with Steam via ``check_authentication``;
* the SteamID is taken only from the verified ``openid.claimed_id``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Mapping
from urllib.parse import urlencode

import httpx

STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"
OPENID_NS = "http://specs.openid.net/auth/2.0"
IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
CLAIMED_ID_RE = re.compile(r"^https://steamcommunity\.com/openid/id/(7656119\d{10})$")
NONCE_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")
REQUIRED_SIGNED_FIELDS = {"op_endpoint", "claimed_id", "identity", "return_to", "response_nonce", "assoc_handle"}
MAX_NONCE_AGE = timedelta(minutes=5)


class OpenIDError(Exception):
    """Verification failed. ``reason`` is a short, safe, machine-readable code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def build_return_to(return_url: str, state: str) -> str:
    return f"{return_url}?{urlencode({'state': state})}"


def build_login_url(*, return_url: str, realm: str, state: str) -> str:
    params = {
        "openid.ns": OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": build_return_to(return_url, state),
        "openid.realm": realm,
        "openid.identity": IDENTIFIER_SELECT,
        "openid.claimed_id": IDENTIFIER_SELECT,
    }
    return f"{STEAM_OPENID_ENDPOINT}?{urlencode(params)}"


def _check_nonce_fresh(nonce: str, now: datetime) -> None:
    match = NONCE_TIME_RE.match(nonce)
    if not match:
        raise OpenIDError("bad_nonce")
    issued = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if abs(now - issued) > MAX_NONCE_AGE:
        raise OpenIDError("stale_nonce")


def verify_callback(
    params: Mapping[str, str],
    *,
    expected_state: str,
    return_url: str,
    http: httpx.Client,
    now: datetime | None = None,
) -> str:
    """Verify a Steam OpenID callback and return the 17-digit SteamID64."""

    now = now or datetime.now(timezone.utc)
    state = params.get("state", "")
    if not expected_state or state != expected_state:
        raise OpenIDError("state_mismatch")
    if params.get("openid.mode") != "id_res":
        raise OpenIDError("not_id_res")
    if params.get("openid.ns") != OPENID_NS:
        raise OpenIDError("bad_ns")
    if params.get("openid.op_endpoint") != STEAM_OPENID_ENDPOINT:
        raise OpenIDError("bad_op_endpoint")
    if params.get("openid.return_to") != build_return_to(return_url, expected_state):
        raise OpenIDError("return_to_mismatch")

    claimed_id = params.get("openid.claimed_id", "")
    match = CLAIMED_ID_RE.match(claimed_id)
    if not match or params.get("openid.identity") != claimed_id:
        raise OpenIDError("bad_claimed_id")

    signed = set((params.get("openid.signed") or "").split(","))
    if not REQUIRED_SIGNED_FIELDS.issubset(signed):
        raise OpenIDError("unsigned_fields")
    _check_nonce_fresh(params.get("openid.response_nonce", ""), now)

    verify_params = {key: value for key, value in params.items() if key.startswith("openid.")}
    verify_params["openid.mode"] = "check_authentication"
    try:
        response = http.post(STEAM_OPENID_ENDPOINT, data=verify_params)
    except httpx.HTTPError as exc:
        raise OpenIDError("steam_unreachable") from exc
    if response.status_code != 200:
        raise OpenIDError("steam_rejected")
    fields = dict(
        line.split(":", 1) for line in response.text.splitlines() if ":" in line
    )
    if fields.get("is_valid", "").strip() != "true":
        raise OpenIDError("not_valid")
    return match.group(1)
