"""Storage interface for Steam linking and match sync.

Implementations: :class:`steamlink.storage.sql.SqlStorage` (PostgreSQL in
production via ``DATABASE_URL``; SQLite for tests and local development).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class User:
    id: str
    steam_id: str
    created_at: datetime


@dataclass(frozen=True)
class Session:
    token_hash: str
    user_id: str
    expires_at: datetime


@dataclass(frozen=True)
class MatchAccess:
    user_id: str
    auth_code_ciphertext: str
    auth_code_last4: str
    cursor_share_code: str
    consented_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SyncState:
    user_id: str
    status: str  # idle | running | ok | error
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_error: str | None = None
    last_imported_count: int = 0
    locked: bool = False


@dataclass(frozen=True)
class RoundRecord:
    round_number: int
    winner_side: str | None
    opening_kill_side: str | None
    opening_kill_seconds: float | None
    opening_weapon: str | None
    unscored_reason: str | None


@dataclass(frozen=True)
class NewMatch:
    share_code: str
    valve_match_id: str
    status: str  # imported | unavailable | parse_failed
    status_reason: str | None
    map_name: str | None
    rounds: tuple[RoundRecord, ...] = ()


@dataclass(frozen=True)
class MatchRecord:
    id: str
    share_code: str
    valve_match_id: str
    status: str
    status_reason: str | None
    map_name: str | None
    rounds_count: int
    imported_at: datetime


class CursorConflict(Exception):
    """The cursor changed underneath a sync (e.g. the user re-linked)."""


class Storage(ABC):
    # Users and sessions -------------------------------------------------
    @abstractmethod
    def get_or_create_user(self, steam_id: str, now: datetime) -> User: ...

    @abstractmethod
    def create_session(self, token_hash: str, user_id: str, now: datetime, expires_at: datetime) -> None: ...

    @abstractmethod
    def get_session_user(self, token_hash: str, now: datetime) -> User | None: ...

    @abstractmethod
    def delete_session(self, token_hash: str) -> None: ...

    # Match-history access ------------------------------------------------
    @abstractmethod
    def set_match_access(
        self, user_id: str, *, ciphertext: str, last4: str, cursor_share_code: str, now: datetime
    ) -> MatchAccess: ...

    @abstractmethod
    def get_match_access(self, user_id: str) -> MatchAccess | None: ...

    @abstractmethod
    def delete_match_access(self, user_id: str) -> None: ...

    # Sync ------------------------------------------------------------------
    @abstractmethod
    def get_sync_state(self, user_id: str, now: datetime) -> SyncState: ...

    @abstractmethod
    def try_acquire_sync_lock(self, user_id: str, token: str, now: datetime, ttl_seconds: int) -> bool:
        """Atomically take the per-user sync lease. Expired leases can be taken over."""

    @abstractmethod
    def release_sync_lock(
        self, user_id: str, token: str, now: datetime, *, status: str, error: str | None, imported: int
    ) -> None: ...

    @abstractmethod
    def record_match(self, user_id: str, *, expected_cursor: str, match: NewMatch, now: datetime) -> bool:
        """In ONE transaction: insert the match + rounds (no-op if the share code is
        already stored) and move the cursor from ``expected_cursor`` to
        ``match.share_code``. Returns True if a new match row was inserted.
        Raises :class:`CursorConflict` if the cursor no longer equals ``expected_cursor``."""

    @abstractmethod
    def record_uploaded_match(self, user_id: str, *, match: NewMatch, now: datetime) -> tuple[str, bool]:
        """Store a manually uploaded match (cursor untouched). ``match.share_code``
        must be a unique upload key (e.g. ``upload:<sha256>``). Returns
        ``(match_id, inserted)``; re-uploading the same file is a no-op."""

    @abstractmethod
    def find_match_id_by_share_code(self, user_id: str, share_code: str) -> str | None:
        """Match id for this user's share code / upload key, or None (lets uploads skip re-parsing)."""

    # Reports ---------------------------------------------------------------
    @abstractmethod
    def list_matches(self, user_id: str, *, limit: int, offset: int) -> list[MatchRecord]: ...

    @abstractmethod
    def get_match(self, user_id: str, match_id: str) -> tuple[MatchRecord, list[RoundRecord]] | None: ...

    # Deletion ---------------------------------------------------------------
    @abstractmethod
    def delete_user(self, user_id: str) -> None:
        """Delete the user and everything linked to them (sessions, codes, matches, rounds)."""
