"""Signed cookies for the one-time login state and server-side sessions.

The session cookie carries a random token signed with ``SESSION_SECRET``. Only
the SHA-256 hash of the token is stored server-side, so a database leak does not
leak usable sessions. Logout or account deletion removes the server row, which
revokes the session immediately.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner, URLSafeTimedSerializer

LOGIN_STATE_COOKIE = "csa_login_state"


def safe_next_path(value: str | None) -> str:
    """Allow only same-site relative paths to prevent open redirects."""

    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(ch) < 32 for ch in value) or len(value) > 512:
        return "/"
    return value


@dataclass(frozen=True)
class LoginState:
    state: str
    next_path: str


class CookieSigner:
    def __init__(self, secret: str):
        self._state = URLSafeTimedSerializer(secret, salt="csa-steam-login-state")
        self._session = TimestampSigner(secret, salt="csa-session")

    def new_login_state(self, next_path: str | None) -> tuple[LoginState, str]:
        login_state = LoginState(state=secrets.token_urlsafe(32), next_path=safe_next_path(next_path))
        cookie = self._state.dumps({"s": login_state.state, "n": login_state.next_path})
        return login_state, cookie

    def read_login_state(self, cookie: str | None, max_age: int) -> LoginState | None:
        if not cookie:
            return None
        try:
            data = self._state.loads(cookie, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("s"), str):
            return None
        return LoginState(state=data["s"], next_path=safe_next_path(data.get("n")))

    def new_session_cookie(self) -> tuple[str, str]:
        """Return ``(cookie_value, token_hash)``; store only the hash."""

        token = secrets.token_urlsafe(32)
        return self._session.sign(token).decode(), hash_token(token)

    def session_token_hash(self, cookie: str | None, max_age: int) -> str | None:
        if not cookie:
            return None
        try:
            token = self._session.unsign(cookie, max_age=max_age).decode()
        except (BadSignature, SignatureExpired):
            return None
        return hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
