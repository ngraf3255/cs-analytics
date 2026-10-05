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
class PlayerRoundRecord:
    """One player's round (steamlink.demo_parser.extract_player_rounds). Stored for
    every player in the demo, so each owner of a shared match sees their own side."""

    round_number: int
    steam_id: str
    side: str  # ct | t: the side this player was on in this round
    kills: int = 0
    deaths: int = 0
    opening_kill: bool = False
    opening_death: bool = False
    survived: bool = True


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
    source: str = "steam_sync"  # steam_sync | upload (how the match arrived for this user)
    # True when share_code came from Valve's match history (Steam sync); a share code
    # typed in with an upload is only a hint (see Storage, "Dedupe").
    share_code_verified: bool = False
    score_ct: int | None = None  # final score (demo_parser.final_score), when the demo was parsed
    score_t: int | None = None
    # Every player's side and stats per round (demo_parser.extract_player_rounds).
    player_rounds: tuple[PlayerRoundRecord, ...] = ()
    players_recorded: bool = False  # True when the parse recorded player rounds (even if empty)
    played_at: datetime | None = None  # when the match was played, if known (not in CS2 demos)
    played_at_source: str | None = None  # valve_gc


@dataclass(frozen=True)
class MatchRecord:
    id: str
    share_code: str
    valve_match_id: str
    status: str
    status_reason: str | None
    map_name: str | None
    rounds_count: int
    imported_at: datetime  # when the match was added to this user's list
    source: str = "steam_sync"  # how it arrived for this user: steam_sync | upload
    demo_sha256: str | None = None
    share_code_verified: bool = False
    score_ct: int | None = None  # final score: rounds won by the team on CT / T at the end
    score_t: int | None = None
    players_recorded: bool = False  # per-player rounds stored (False: parsed before they were)
    played_at: datetime | None = None  # when the match was played, if known
    played_at_source: str | None = None

    @property
    def has_share_code(self) -> bool:
        return not self.share_code.startswith(UPLOAD_KEY_PREFIX)


UPLOAD_JOB_ACTIVE = ("queued", "processing")
JOB_KIND_UPLOAD = "upload"
JOB_KIND_SYNC = "steam_sync"


@dataclass(frozen=True)
class UploadJob:
    """A demo waiting for / undergoing background parsing (see steamlink.jobs):
    a manual upload (``kind="upload"``) or one match of a Steam sync
    (``kind="steam_sync"``: the worker downloads the demo for ``share_code``)."""

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
    kind: str = JOB_KIND_UPLOAD

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

    # Dedupe (all sources and all users). Matches are shared: one ``matches`` row
    # per real match, listed for every user who owns it (``match_owners``). A
    # match is "the same" when its demo SHA-256 is already stored (the server
    # hashed the bytes, so this is always trusted), or when its share code /
    # Valve match id is already stored AND either the share code is verified on
    # both sides (it came from Valve's match history, i.e. Steam sync) or the
    # stored match is already in this user's own list. A share code typed in
    # with an upload is only a hint: it never attaches another user's match.
    # Then no second row is written and nothing is parsed again; instead the
    # stored match (a) is added to this user's list, (b) gains keys it lacked
    # (e.g. an upload learns its share code from sync; a verified share code
    # replaces a contradicting hint) and (c) is upgraded with the new rounds if
    # it was not imported yet (e.g. Steam had no demo, then the user uploaded it).

    @abstractmethod
    def record_match(self, user_id: str, *, expected_cursor: str, match: NewMatch, now: datetime) -> bool:
        """In ONE transaction: store the match + rounds (or dedupe as above; the
        share code counts as verified) and move the cursor from ``expected_cursor``
        to ``match.share_code``. Returns True if the match was newly added to the
        user's list. Raises :class:`CursorConflict` if the cursor no longer equals
        ``expected_cursor``."""

    @abstractmethod
    def skip_known_match(
        self, user_id: str, *, expected_cursor: str, share_code: str, valve_match_id: str, now: datetime
    ) -> str | None:
        """Sync found this (verified) share code. If it resolves to a stored,
        imported match (see "Dedupe"), add that match to the user's list and
        advance the cursor in one transaction, and return ``"added"`` (it was not
        in the user's list yet, e.g. another player imported it) or ``"owned"``
        (already in the list): sync skips the download. Otherwise change nothing
        and return None. Raises :class:`CursorConflict` like :meth:`record_match`."""

    @abstractmethod
    def claim_known_match(
        self, user_id: str, *, share_code: str | None, valve_match_id: str | None, demo_sha256: str | None,
        share_code_verified: bool, source: str, now: datetime,
    ) -> tuple[MatchRecord, bool] | None:
        """If these keys resolve to a stored, imported match (see "Dedupe"), make
        sure it is in the user's list (merging keys it lacked) and return
        ``(match as the user sees it, newly_added)`` so the caller skips parsing.
        Otherwise change nothing and return None."""

    @abstractmethod
    def record_uploaded_match(self, user_id: str, *, match: NewMatch, now: datetime) -> tuple[str, bool]:
        """Store a parsed (or stub) match (cursor untouched), deduped as above.
        ``match.share_code`` is the real share code if known, else
        ``upload:<sha256>``. Returns ``(match_id, newly_added_to_user)``.
        A deduped stored match also gains details it lacked: player rounds (if not
        recorded yet), final score and match date (``played_at``)."""

    @abstractmethod
    def find_match(
        self, user_id: str, *, share_code: str | None = None, valve_match_id: str | None = None,
        demo_sha256: str | None = None,
    ) -> MatchRecord | None:
        """The match in this user's list matching any given key, or None."""

    # Upload jobs -------------------------------------------------------------
    @abstractmethod
    def create_upload_job(self, job: UploadJob, *, max_active: int | None = None) -> bool:
        """Insert ``job``. If ``max_active`` is given and that many jobs (all users)
        are already queued/processing, insert nothing and return False."""

    @abstractmethod
    def get_upload_job(self, user_id: str, job_id: str) -> UploadJob | None: ...

    @abstractmethod
    def list_upload_jobs(self, user_id: str, *, limit: int, kind: str | None = None) -> list[UploadJob]:
        """The user's most recent jobs (of ``kind``, if given), newest first."""

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
    def enqueue_sync_job(
        self, job: UploadJob, *, expected_cursor: str, max_active: int, now: datetime
    ) -> tuple[str, str | None]:
        """In ONE transaction: queue a ``steam_sync`` job for ``job.share_code`` and
        move the user's cursor from ``expected_cursor`` to that share code.

        Returns ``(outcome, job_id)``: ``"queued"`` (new row, or the existing
        finished row for this share code queued again), ``"exists"`` (an active
        job already has this share code; only the cursor moves) or
        ``"queue_full"`` (``max_active`` jobs, all users and kinds, are active;
        nothing changes, ``job_id`` is None). Raises :class:`CursorConflict`."""

    @abstractmethod
    def requeue_sync_jobs(self, user_id: str, *, errors: tuple[str, ...], max_attempts: int,
                          max_active: int, now: datetime) -> list[str]:
        """Queue the user's failed ``steam_sync`` jobs whose error is in ``errors``
        and that ran fewer than ``max_attempts`` times again (oldest first, while
        fewer than ``max_active`` jobs are active). Returns their ids."""

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

    @abstractmethod
    def get_player_rounds(self, match_id: str, steam_id: str) -> list[PlayerRoundRecord]:
        """One player's rounds of a stored match, in round order (empty: not in the demo,
        or not recorded; see ``MatchRecord.players_recorded``)."""

    @abstractmethod
    def list_player_rounds(self, user_id: str, steam_id: str) -> dict[str, list[PlayerRoundRecord]]:
        """``{match id: that player's rounds}`` over every match in the user's list
        (matches the player is not in are absent). One query."""

    @abstractmethod
    def list_matches_with_rounds(self, user_id: str) -> list[tuple[MatchRecord, list[RoundRecord]]]:
        """Every match in the user's list (shared matches included, as the user sees
        them; stubs with no rounds too) with its rounds in round order, newest
        first like :meth:`list_matches`. Two queries, for cross-match analytics."""

    # Deletion ---------------------------------------------------------------
    @abstractmethod
    def delete_user(self, user_id: str) -> None:
        """Delete the user and everything linked to them (sessions, codes, upload jobs,
        their place in shared matches; matches nobody else owns, with their rounds)."""
