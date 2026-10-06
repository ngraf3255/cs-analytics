"""Environment-driven settings for demo uploads, Steam linking and match sync.

Two feature levels, each validated at startup so a half-configured deploy fails
loudly instead of running with insecure defaults:

* **Demo upload** (``demo_upload_enabled``): ``DATABASE_URL`` + ``SESSION_SECRET``.
  Signed-in users (and, with ``GUEST_UPLOADS`` on, guest sessions without Steam)
  upload ``.dem`` files, get them parsed and view match reports. No Steam key needed.
* **Steam** (``steam_enabled``): additionally ``TOKEN_ENCRYPTION_KEYS`` +
  ``STEAM_WEB_API_KEY`` (+ ``PUBLIC_API_URL`` / ``FRONTEND_URL``): Steam OpenID
  sign-in, match-history linking, sync and automatic sync.

``TOKEN_ENCRYPTION_KEYS`` without ``STEAM_WEB_API_KEY`` runs upload-only (Steam
stays off until the key is added); ``STEAM_WEB_API_KEY`` without
``TOKEN_ENCRYPTION_KEYS`` is rejected (codes could not be stored encrypted).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

DEFAULT_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,https://csgooner.com,https://www.csgooner.com"
)


class ConfigError(RuntimeError):
    """Raised when Steam sync is enabled but its configuration is incomplete or unsafe."""


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    allowed_origins: list[str]
    database_url: str | None = None
    token_encryption_keys: list[str] = field(default_factory=list)
    session_secret: str | None = None
    # Public base URL of this API, e.g. https://api-site.csgooner.com. Used for the
    # OpenID realm and the exact return URL.
    public_api_url: str | None = None
    # Where to send the browser after login, e.g. https://csgooner.com.
    frontend_url: str | None = None
    steam_web_api_key: str | None = None
    session_cookie_name: str = "csa_session"
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "lax"
    session_cookie_domain: str | None = None
    session_ttl_seconds: int = 14 * 24 * 3600
    login_state_ttl_seconds: int = 600
    # Per POST /steam/sync: share codes walked (each new match becomes a background
    # job; the job queue cap UPLOAD_QUEUE_MAX applies too). A sync job whose demo
    # isn't ready yet (or whose download failed transiently) is retried by later
    # syncs until it ran SYNC_JOB_MAX_ATTEMPTS times.
    sync_max_matches_per_request: int = 3
    # Also import the match of the share code the user linked with (not only newer ones).
    sync_import_start_match: bool = True
    sync_job_max_attempts: int = 5
    sync_lock_ttl_seconds: int = 900
    sync_min_interval_seconds: int = 30
    # Automatic background sync (steamlink.autosync): every linked user who has not
    # turned it off is synced once their last sync is older than this (0 disables it).
    # The scheduler checks every AUTO_SYNC_TICK_SECONDS and syncs at most
    # AUTO_SYNC_MAX_USERS_PER_TICK users per check (all processes together); repeated
    # failures back off exponentially up to AUTO_SYNC_MAX_BACKOFF_SECONDS.
    auto_sync_interval_seconds: int = 1800
    auto_sync_tick_seconds: int = 60
    auto_sync_max_users_per_tick: int = 5
    auto_sync_max_backoff_seconds: int = 6 * 3600
    demo_max_download_bytes: int = 300 * 1024 * 1024
    demo_max_decompressed_bytes: int = 1024 * 1024 * 1024
    # Max request body for POST /matches/upload (.dem or .dem.bz2 as sent).
    # Render itself does not cap request bodies; lower this if a proxy does
    # (e.g. Cloudflare-proxied hostnames: 100 MB on Free/Pro plans).
    upload_max_bytes: int = 1024 * 1024 * 1024
    # Uploads are parsed in the background (steamlink.jobs). Received files wait
    # in UPLOAD_JOB_DIR (default: <tmp>/csa-upload-jobs; Render's disk is
    # ephemeral, so a restart fails queued jobs); at most UPLOAD_QUEUE_MAX jobs
    # (all users) may be queued or processing, further uploads get 429.
    upload_job_dir: str | None = None
    upload_queue_max: int = 3
    http_timeout_seconds: float = 20.0
    # Demo parsing (see demo_parser.Demoparser2Parser): "subprocess" (default)
    # parses in a short-lived child so its memory goes back to the OS and an
    # out-of-memory kill hits the child, not the API; "inprocess" for debugging.
    demo_parse_isolation: str = "subprocess"
    demo_parse_timeout_seconds: float = 600.0
    demo_parse_threads: int = 2
    # Dedicated Steam bot for CS2 Game Coordinator demo URL lookups. Preferred:
    # a refresh token (STEAM_BOT_REFRESH_TOKEN or STEAM_BOT_REFRESH_TOKEN_FILE).
    # Fallback: username + password + shared_secret (no interactive Steam Guard).
    steam_bot_refresh_token: str | None = None
    steam_bot_username: str | None = None
    steam_bot_password: str | None = None
    steam_bot_shared_secret: str | None = None
    # Guest sessions (POST /auth/guest): upload demos and view reports without Steam.
    guest_uploads: bool = True

    @property
    def demo_bot_configured(self) -> bool:
        return bool(self.steam_bot_refresh_token) or bool(
            self.steam_bot_username and self.steam_bot_password and self.steam_bot_shared_secret
        )

    @property
    def demo_upload_enabled(self) -> bool:
        """Accounts, demo upload, parsing and match reports (no Steam key needed)."""
        # A missing / too-short SESSION_SECRET leaves uploads off (see startup_notes) rather than
        # crash-looping a deploy that only ran round prediction before.
        return bool(self.database_url) and len(self.session_secret or "") >= 32

    @property
    def steam_enabled(self) -> bool:
        """Steam OpenID sign-in, match-history linking and sync."""
        return self.demo_upload_enabled and bool(self.token_encryption_keys) and bool(self.steam_web_api_key)

    @property
    def guest_uploads_enabled(self) -> bool:
        return self.demo_upload_enabled and self.guest_uploads

    @property
    def openid_return_url(self) -> str:
        if not self.public_api_url:
            raise ConfigError("PUBLIC_API_URL is not configured")
        return self.public_api_url.rstrip("/") + "/auth/steam/callback"

    @property
    def openid_realm(self) -> str:
        if not self.public_api_url:
            raise ConfigError("PUBLIC_API_URL is not configured")
        return self.public_api_url.rstrip("/") + "/"

    def startup_notes(self) -> list[str]:
        """Human-readable reasons a feature is off (logged at startup; never contains secrets)."""

        notes = []
        if not self.database_url:
            notes.append("DATABASE_URL not set: demo upload, match reports and Steam are off (round prediction only)")
            return notes
        if not self.demo_upload_enabled:
            notes.append("SESSION_SECRET missing or shorter than 32 characters: demo upload and Steam are off")
            return notes
        if not self.steam_enabled:
            notes.append("STEAM_WEB_API_KEY / TOKEN_ENCRYPTION_KEYS not set: Steam sign-in and sync are off; "
                         + ("guest demo upload is on" if self.guest_uploads else "GUEST_UPLOADS=false, so uploads need a Steam sign-in"))
        return notes

    def validate(self) -> None:
        if "*" in self.allowed_origins:
            raise ConfigError("ALLOWED_ORIGINS cannot contain '*' because credentials are allowed")
        if (self.steam_bot_username or self.steam_bot_password) and not self.demo_bot_configured:
            raise ConfigError(
                "STEAM_BOT_USERNAME/STEAM_BOT_PASSWORD need STEAM_BOT_SHARED_SECRET (or use STEAM_BOT_REFRESH_TOKEN)"
            )
        if not self.database_url:
            return
        if self.steam_web_api_key and not self.token_encryption_keys:
            raise ConfigError(
                "STEAM_WEB_API_KEY is set but TOKEN_ENCRYPTION_KEYS is missing (needed to store "
                "Game Authentication Codes encrypted)"
            )
        if (self.token_encryption_keys or self.steam_web_api_key) and not self.demo_upload_enabled:
            raise ConfigError("Steam settings are set but SESSION_SECRET is missing or shorter than 32 characters")
        if not self.demo_upload_enabled:
            return
        if self.steam_enabled:
            missing = [
                name
                for name, value in (("PUBLIC_API_URL", self.public_api_url), ("FRONTEND_URL", self.frontend_url))
                if not value
            ]
            if missing:
                raise ConfigError(
                    "Steam is enabled (DATABASE_URL, TOKEN_ENCRYPTION_KEYS and STEAM_WEB_API_KEY are set) but "
                    f"these settings are missing: {', '.join(missing)}"
                )
        if len(self.session_secret or "") < 32:
            raise ConfigError("SESSION_SECRET must be at least 32 characters")
        for name, url in (("PUBLIC_API_URL", self.public_api_url), ("FRONTEND_URL", self.frontend_url)):
            if not url:
                continue  # only required for Steam (checked above)
            parts = urlsplit(url or "")
            if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment:
                raise ConfigError(f"{name} must be an absolute http(s) URL without query or fragment")
        if self.session_cookie_samesite not in {"lax", "strict", "none"}:
            raise ConfigError("SESSION_COOKIE_SAMESITE must be lax, strict, or none")
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ConfigError("SESSION_COOKIE_SAMESITE=none requires SESSION_COOKIE_SECURE=true")
        if self.demo_parse_isolation not in ("subprocess", "inprocess"):
            raise ConfigError("DEMO_PARSE_ISOLATION must be subprocess or inprocess")
        if self.demo_parse_timeout_seconds <= 0 or self.demo_parse_threads < 1:
            raise ConfigError("DEMO_PARSE_TIMEOUT_SECONDS and DEMO_PARSE_THREADS must be positive")
        if self.upload_max_bytes < 1:
            raise ConfigError("UPLOAD_MAX_BYTES must be positive")
        if self.upload_queue_max < 1:
            raise ConfigError("UPLOAD_QUEUE_MAX must be positive")
        if self.sync_max_matches_per_request < 1 or self.sync_max_matches_per_request > 10:
            raise ConfigError("SYNC_MAX_MATCHES_PER_REQUEST must be between 1 and 10")
        if self.sync_job_max_attempts < 1:
            raise ConfigError("SYNC_JOB_MAX_ATTEMPTS must be positive")
        if self.auto_sync_interval_seconds < 0:
            raise ConfigError("AUTO_SYNC_INTERVAL_SECONDS must be 0 (off) or positive")
        if 0 < self.auto_sync_interval_seconds < max(60, self.sync_min_interval_seconds):
            raise ConfigError("AUTO_SYNC_INTERVAL_SECONDS must be at least 60 and SYNC_MIN_INTERVAL_SECONDS")
        if self.auto_sync_tick_seconds < 5 or self.auto_sync_max_users_per_tick < 1:
            raise ConfigError("AUTO_SYNC_TICK_SECONDS must be at least 5 and AUTO_SYNC_MAX_USERS_PER_TICK positive")
        if self.auto_sync_max_backoff_seconds < self.auto_sync_interval_seconds:
            raise ConfigError("AUTO_SYNC_MAX_BACKOFF_SECONDS must be at least AUTO_SYNC_INTERVAL_SECONDS")


def _read_secret_file(path: str | None) -> str | None:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError as exc:
        raise ConfigError("STEAM_BOT_REFRESH_TOKEN_FILE could not be read") from exc


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    settings = Settings(
        allowed_origins=_split_csv(env.get("ALLOWED_ORIGINS", DEFAULT_ORIGINS)),
        database_url=env.get("DATABASE_URL") or None,
        token_encryption_keys=_split_csv(env.get("TOKEN_ENCRYPTION_KEYS", "")),
        session_secret=env.get("SESSION_SECRET") or None,
        public_api_url=(env.get("PUBLIC_API_URL") or "").rstrip("/") or None,
        frontend_url=(env.get("FRONTEND_URL") or "").rstrip("/") or None,
        steam_web_api_key=env.get("STEAM_WEB_API_KEY") or None,
        session_cookie_secure=_bool(env, "SESSION_COOKIE_SECURE", True),
        session_cookie_samesite=(env.get("SESSION_COOKIE_SAMESITE") or "lax").lower(),
        session_cookie_domain=env.get("SESSION_COOKIE_DOMAIN") or None,
        session_ttl_seconds=_int(env, "SESSION_TTL_SECONDS", 14 * 24 * 3600),
        sync_max_matches_per_request=_int(env, "SYNC_MAX_MATCHES_PER_REQUEST", 3),
        sync_import_start_match=_bool(env, "SYNC_IMPORT_START_MATCH", True),
        sync_job_max_attempts=_int(env, "SYNC_JOB_MAX_ATTEMPTS", 5),
        sync_lock_ttl_seconds=_int(env, "SYNC_LOCK_TTL_SECONDS", 900),
        sync_min_interval_seconds=_int(env, "SYNC_MIN_INTERVAL_SECONDS", 30),
        auto_sync_interval_seconds=_int(env, "AUTO_SYNC_INTERVAL_SECONDS", 1800),
        auto_sync_tick_seconds=_int(env, "AUTO_SYNC_TICK_SECONDS", 60),
        auto_sync_max_users_per_tick=_int(env, "AUTO_SYNC_MAX_USERS_PER_TICK", 5),
        auto_sync_max_backoff_seconds=_int(env, "AUTO_SYNC_MAX_BACKOFF_SECONDS", 6 * 3600),
        demo_max_download_bytes=_int(env, "DEMO_MAX_DOWNLOAD_BYTES", 300 * 1024 * 1024),
        demo_max_decompressed_bytes=_int(env, "DEMO_MAX_DECOMPRESSED_BYTES", 1024 * 1024 * 1024),
        upload_max_bytes=_int(env, "UPLOAD_MAX_BYTES", 1024 * 1024 * 1024),
        upload_job_dir=(env.get("UPLOAD_JOB_DIR") or "").strip() or None,
        upload_queue_max=_int(env, "UPLOAD_QUEUE_MAX", 3),
        demo_parse_isolation=(env.get("DEMO_PARSE_ISOLATION") or "subprocess").strip().lower(),
        demo_parse_timeout_seconds=float(_int(env, "DEMO_PARSE_TIMEOUT_SECONDS", 600)),
        demo_parse_threads=_int(env, "DEMO_PARSE_THREADS", 2),
        steam_bot_refresh_token=(env.get("STEAM_BOT_REFRESH_TOKEN") or None)
        or _read_secret_file(env.get("STEAM_BOT_REFRESH_TOKEN_FILE")),
        steam_bot_username=env.get("STEAM_BOT_USERNAME") or None,
        steam_bot_password=env.get("STEAM_BOT_PASSWORD") or None,
        steam_bot_shared_secret=env.get("STEAM_BOT_SHARED_SECRET") or None,
        guest_uploads=_bool(env, "GUEST_UPLOADS", True),
    )
    settings.validate()
    return settings
