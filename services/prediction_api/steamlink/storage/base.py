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


# valve_match_id value when the Valve match id is not known (uploads without a share code).
UNKNOWN_MATCH_ID = "upload"
# matches.share_code prefix for uploads without a share code: "upload:<demo sha256>".
UPLOAD_KEY_PREFIX = "upload:"


@dataclass(frozen=True)
class NewMatch:
    share_code: str
    valve_match_id: str
    status: str  # imported | unavailable | parse_failed
    status_reason: str | None
    map_name: str | None
    rounds: tuple[RoundRecord, ...] = ()
    # SHA-256 of the decompressed .dem when we had the file (sync download or upload).
    demo_sha256: str | None = None
    source: str = "steam_sync"  # steam_sync | upload (how the match first arrived)


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
    source: str = "steam_sync"
    demo_sha256: str | None = None

    @property
    def has_share_code(self) -> bool:
        return not self.share_code.startswith(UPLOAD_KEY_PREFIX)


UPLOAD_JOB_ACTIVE = ("queued", "processing")


@dataclass(frozen=True)
class UploadJob:
    """A manual upload waiting for / undergoing background parsing (see steamlink.jobs)."""

    id: str
    user_id: str
    status: str  # queued | processing | done | failed
    demo_path: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime
    stage: str | None = None  # decompressing | hashing | parsing | storing (while processing)
    progress: float | None = None  # 0..1 within the stage, when known
    share_code: str | None = None
    demo_sha256: str | None = None
    attempts: int = 0
    error: str | None = None
    match_id: str | None = None
    match_created: bool | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status in UPLOAD_JOB_ACTIVE


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

    # Dedupe (all sources): a match is "the same" for a user when ANY of its
    # share code, Valve match id (if known) or demo SHA-256 (if known) is
    # already stored. Then no second row is written; instead the stored row
    # (a) gains keys it lacked (e.g. an upload learns its share code from sync)
    # and (b) is upgraded with the new rounds if it was not imported yet
    # (e.g. Steam had no demo, then the user uploaded it).

    @abstractmethod
    def record_match(self, user_id: str, *, expected_cursor: str, match: NewMatch, now: datetime) -> bool:
        """In ONE transaction: insert the match + rounds (or dedupe as above) and
        move the cursor from ``expected_cursor`` to ``match.share_code``. Returns
        True if a new match row was inserted. Raises :class:`CursorConflict` if
        the cursor no longer equals ``expected_cursor``."""

    @abstractmethod
    def skip_known_match(
        self, user_id: str, *, expected_cursor: str, share_code: str, valve_match_id: str, now: datetime
    ) -> bool:
        """If this share code / Valve match id is already stored AND imported, attach
        the share code to it if missing and advance the cursor (one transaction);
        return True so sync can skip downloading the demo. Otherwise change nothing
        and return False. Raises :class:`CursorConflict` like :meth:`record_match`."""

    @abstractmethod
    def record_uploaded_match(self, user_id: str, *, match: NewMatch, now: datetime) -> tuple[str, bool]:
        """Store a manually uploaded match (cursor untouched), deduped as above.
        ``match.share_code`` is the real share code if the user gave one, else
        ``upload:<sha256>``. Returns ``(match_id, inserted)``."""

    @abstractmethod
    def find_match(
        self, user_id: str, *, share_code: str | None = None, valve_match_id: str | None = None,
        demo_sha256: str | None = None,
    ) -> MatchRecord | None:
        """The user's stored match matching any given key, or None (lets uploads skip re-parsing)."""

    # Upload jobs -------------------------------------------------------------
    @abstractmethod
    def create_upload_job(self, job: UploadJob, *, max_active: int | None = None) -> bool:
        """Insert ``job``. If ``max_active`` is given and that many jobs (all users)
        are already queued/processing, insert nothing and return False."""

    @abstractmethod
    def get_upload_job(self, user_id: str, job_id: str) -> UploadJob | None: ...

    @abstractmethod
    def list_upload_jobs(self, user_id: str, *, limit: int) -> list[UploadJob]:
        """The user's most recent jobs, newest first."""

    @abstractmethod
    def list_active_upload_jobs(self) -> list[UploadJob]:
        """All queued/processing jobs (all users), oldest first."""

    @abstractmethod
    def claim_next_upload_job(self, now: datetime) -> UploadJob | None:
        """Atomically move the oldest queued job to processing (attempts + 1) and return it."""

    @abstractmethod
    def update_upload_job(self, job_id: str, now: datetime, **fields) -> None:
        """Set any of: status, stage, progress, error, match_id, match_created, finished_at."""

    @abstractmethod
    def upload_jobs_ahead(self, job: UploadJob) -> int:
        """How many active jobs will be worked on before this queued job."""

    @abstractmethod
    def delete_finished_upload_jobs(self, before: datetime) -> int: ...

    # Reports ---------------------------------------------------------------
    @abstractmethod
    def list_matches(self, user_id: str, *, limit: int, offset: int) -> list[MatchRecord]: ...

    @abstractmethod
    def get_match(self, user_id: str, match_id: str) -> tuple[MatchRecord, list[RoundRecord]] | None: ...

    # Deletion ---------------------------------------------------------------
    @abstractmethod
    def delete_user(self, user_id: str) -> None:
        """Delete the user and everything linked to them (sessions, codes, matches, rounds, upload jobs)."""
