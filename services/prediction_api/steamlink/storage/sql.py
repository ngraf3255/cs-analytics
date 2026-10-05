"""SQLAlchemy Core storage for PostgreSQL (production) and SQLite (tests)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    TypeDecorator,
    and_,
    create_engine,
    delete,
    func,
    event,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from .base import (
    JOB_KIND_SYNC,
    UNKNOWN_MATCH_ID,
    UPLOAD_JOB_ACTIVE,
    UPLOAD_KEY_PREFIX,
    CursorConflict,
    MatchAccess,
    MatchRecord,
    NewMatch,
    RoundRecord,
    Storage,
    SyncState,
    UploadJob,
    User,
)


class UTCDateTime(TypeDecorator):
    """Always hand back timezone-aware UTC datetimes, on PostgreSQL and SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


# These definitions mirror migrations/*.sql (a test checks they stay in sync).
metadata = MetaData()
users = Table(
    "users", metadata,
    Column("id", String, primary_key=True),
    Column("steam_id", String, nullable=False, unique=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)
sessions = Table(
    "sessions", metadata,
    Column("token_hash", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("expires_at", UTCDateTime, nullable=False),
)
match_access = Table(
    "match_access", metadata,
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("auth_code_ciphertext", String, nullable=False),
    Column("auth_code_last4", String, nullable=False),
    Column("cursor_share_code", String, nullable=False),
    Column("consented_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)
sync_state = Table(
    "sync_state", metadata,
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("status", String, nullable=False),
    Column("lock_token", String),
    Column("lock_expires_at", UTCDateTime),
    Column("last_started_at", UTCDateTime),
    Column("last_finished_at", UTCDateTime),
    Column("last_error", String),
    Column("last_imported_count", Integer, nullable=False, default=0),
)
matches = Table(
    "matches", metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("share_code", String, nullable=False),
    Column("valve_match_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("status_reason", String),
    Column("map_name", String),
    Column("rounds_count", Integer, nullable=False, default=0),
    Column("imported_at", UTCDateTime, nullable=False),
    Column("source", String, nullable=False, default="steam_sync"),
    Column("demo_sha256", String),
    Column("share_code_verified", Integer, nullable=False, default=0),
    Column("score_ct", Integer),
    Column("score_t", Integer),
)
# Who has a (shared) match in their list. matches.user_id = who imported it first.
match_owners = Table(
    "match_owners", metadata,
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("match_id", String, ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True),
    Column("source", String, nullable=False),
    Column("added_at", UTCDateTime, nullable=False),
)
rounds = Table(
    "rounds", metadata,
    Column("match_id", String, ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True),
    Column("round_number", Integer, primary_key=True),
    Column("winner_side", String),
    Column("opening_kill_side", String),
    Column("opening_kill_seconds", Float),
    Column("opening_weapon", String),
    Column("unscored_reason", String),
)
upload_jobs = Table(
    "upload_jobs", metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("status", String, nullable=False),
    Column("stage", String),
    Column("progress", Float),
    Column("share_code", String),
    Column("demo_path", String, nullable=False),
    Column("demo_sha256", String),
    Column("size_bytes", BigInteger, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("error", String),
    Column("match_id", String),
    Column("match_created", Integer),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    Column("started_at", UTCDateTime),
    Column("finished_at", UTCDateTime),
    Column("kind", String, nullable=False, default="upload"),
)
_JOB_UPDATABLE = frozenset({"status", "stage", "progress", "error", "match_id", "match_created", "finished_at"})


def _upload_job(row) -> UploadJob:
    return UploadJob(
        id=row.id, user_id=row.user_id, status=row.status, demo_path=row.demo_path, size_bytes=row.size_bytes,
        created_at=row.created_at, updated_at=row.updated_at, stage=row.stage, progress=row.progress,
        share_code=row.share_code, demo_sha256=row.demo_sha256, attempts=row.attempts, error=row.error,
        match_id=row.match_id, match_created=None if row.match_created is None else bool(row.match_created),
        started_at=row.started_at, finished_at=row.finished_at, kind=row.kind,
    )


def normalize_database_url(url: str) -> str:
    """Use the psycopg 3 driver for postgres URLs (Render/homelab style URLs)."""

    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(database_url: str) -> Engine:
    url = normalize_database_url(database_url)
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _enable_fks(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def _match_record(row, owner=None) -> MatchRecord:
    """``owner``: the user's match_owners row (their source and time added), if any."""

    return MatchRecord(
        id=row.id, share_code=row.share_code, valve_match_id=row.valve_match_id, status=row.status,
        status_reason=row.status_reason, map_name=row.map_name, rounds_count=row.rounds_count,
        imported_at=owner.added_at if owner is not None else row.imported_at,
        source=owner.source if owner is not None else row.source, demo_sha256=row.demo_sha256,
        share_code_verified=bool(row.share_code_verified), score_ct=row.score_ct, score_t=row.score_t,
    )


def _real_code(share_code: str | None) -> str | None:
    return share_code if share_code and not share_code.startswith(UPLOAD_KEY_PREFIX) else None


# Columns of a match as one user sees it (shared row + that user's ownership row).
_OWNED_COLUMNS = (*matches.c, match_owners.c.source.label("owner_source"), match_owners.c.added_at)


class _Owner:
    def __init__(self, row):
        self.source, self.added_at = row.owner_source, row.added_at


class SqlStorage(Storage):
    def __init__(self, engine: Engine):
        self.engine = engine

    # Users and sessions -------------------------------------------------
    def get_or_create_user(self, steam_id: str, now: datetime) -> User:
        with self.engine.begin() as conn:
            row = conn.execute(select(users).where(users.c.steam_id == steam_id)).first()
            if row:
                return User(id=row.id, steam_id=row.steam_id, created_at=row.created_at)
        try:
            with self.engine.begin() as conn:
                user_id = uuid.uuid4().hex
                conn.execute(insert(users).values(id=user_id, steam_id=steam_id, created_at=now, updated_at=now))
                return User(id=user_id, steam_id=steam_id, created_at=now)
        except IntegrityError:  # concurrent first login
            with self.engine.begin() as conn:
                row = conn.execute(select(users).where(users.c.steam_id == steam_id)).one()
                return User(id=row.id, steam_id=row.steam_id, created_at=row.created_at)

    def create_session(self, token_hash: str, user_id: str, now: datetime, expires_at: datetime) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(and_(sessions.c.user_id == user_id, sessions.c.expires_at < now)))
            conn.execute(insert(sessions).values(
                token_hash=token_hash, user_id=user_id, created_at=now, expires_at=expires_at))

    def get_session_user(self, token_hash: str, now: datetime) -> User | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(users).join(sessions, sessions.c.user_id == users.c.id)
                .where(and_(sessions.c.token_hash == token_hash, sessions.c.expires_at > now))
            ).first()
        return User(id=row.id, steam_id=row.steam_id, created_at=row.created_at) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.token_hash == token_hash))

    # Match-history access ------------------------------------------------
    def set_match_access(self, user_id, *, ciphertext, last4, cursor_share_code, now) -> MatchAccess:
        values = dict(auth_code_ciphertext=ciphertext, auth_code_last4=last4,
                      cursor_share_code=cursor_share_code, consented_at=now, updated_at=now)
        with self.engine.begin() as conn:
            updated = conn.execute(update(match_access).where(match_access.c.user_id == user_id).values(**values))
            if updated.rowcount == 0:
                conn.execute(insert(match_access).values(user_id=user_id, **values))
        return MatchAccess(user_id=user_id, auth_code_ciphertext=ciphertext, auth_code_last4=last4,
                           cursor_share_code=cursor_share_code, consented_at=now, updated_at=now)

    def get_match_access(self, user_id: str) -> MatchAccess | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(match_access).where(match_access.c.user_id == user_id)).first()
        if not row:
            return None
        return MatchAccess(user_id=row.user_id, auth_code_ciphertext=row.auth_code_ciphertext,
                           auth_code_last4=row.auth_code_last4, cursor_share_code=row.cursor_share_code,
                           consented_at=row.consented_at, updated_at=row.updated_at)

    def delete_match_access(self, user_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(match_access).where(match_access.c.user_id == user_id))

    # Sync ------------------------------------------------------------------
    def get_sync_state(self, user_id: str, now: datetime) -> SyncState:
        with self.engine.begin() as conn:
            row = conn.execute(select(sync_state).where(sync_state.c.user_id == user_id)).first()
        if not row:
            return SyncState(user_id=user_id, status="idle")
        locked = row.lock_token is not None and row.lock_expires_at is not None and row.lock_expires_at > now
        return SyncState(user_id=user_id, status=row.status, last_started_at=row.last_started_at,
                         last_finished_at=row.last_finished_at, last_error=row.last_error,
                         last_imported_count=row.last_imported_count, locked=locked)

    def try_acquire_sync_lock(self, user_id: str, token: str, now: datetime, ttl_seconds: int) -> bool:
        try:
            with self.engine.begin() as conn:
                exists = conn.execute(select(sync_state.c.user_id).where(sync_state.c.user_id == user_id)).first()
                if not exists:
                    conn.execute(insert(sync_state).values(user_id=user_id, status="idle", last_imported_count=0))
        except IntegrityError:
            pass  # another request created the row first
        with self.engine.begin() as conn:
            result = conn.execute(
                update(sync_state)
                .where(and_(
                    sync_state.c.user_id == user_id,
                    or_(sync_state.c.lock_token.is_(None), sync_state.c.lock_expires_at < now),
                ))
                .values(lock_token=token, lock_expires_at=now + timedelta(seconds=ttl_seconds),
                        status="running", last_started_at=now, last_error=None)
            )
            return result.rowcount == 1

    def release_sync_lock(self, user_id, token, now, *, status, error, imported) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(sync_state)
                .where(and_(sync_state.c.user_id == user_id, sync_state.c.lock_token == token))
                .values(lock_token=None, lock_expires_at=None, status=status, last_error=error,
                        last_finished_at=now, last_imported_count=imported)
            )

    def _check_cursor(self, conn, user_id: str, expected_cursor: str) -> None:
        current = conn.execute(
            select(match_access.c.cursor_share_code)
            .where(match_access.c.user_id == user_id).with_for_update()
        ).scalar_one_or_none()
        if current is None or current != expected_cursor:
            raise CursorConflict()

    def _advance_cursor(self, conn, user_id: str, share_code: str, now: datetime) -> None:
        conn.execute(update(match_access).where(match_access.c.user_id == user_id)
                     .values(cursor_share_code=share_code, updated_at=now))

    def record_match(self, user_id: str, *, expected_cursor: str, match: NewMatch, now: datetime) -> bool:
        match = replace(match, share_code_verified=True)  # from Valve's match history
        with self.engine.begin() as conn:
            self._check_cursor(conn, user_id, expected_cursor)
            _, added = self._store_deduped(conn, user_id, match, now)
            self._advance_cursor(conn, user_id, match.share_code, now)
            return added

    def skip_known_match(self, user_id, *, expected_cursor, share_code, valve_match_id, now) -> str | None:
        with self.engine.begin() as conn:
            self._check_cursor(conn, user_id, expected_cursor)
            row = self._resolve(conn, user_id, share_code=share_code, valve_match_id=valve_match_id,
                                demo_sha256=None, verified=True)
            if row is None or row.status != "imported":
                return None
            self._merge_keys(conn, row, share_code=share_code, valve_match_id=valve_match_id, demo_sha256=None,
                             verified=True)
            added = self._attach(conn, user_id, row.id, "steam_sync", now)
            self._advance_cursor(conn, user_id, share_code, now)
            return "added" if added else "owned"

    def claim_known_match(self, user_id, *, share_code, valve_match_id, demo_sha256, share_code_verified,
                          source, now):
        def attempt():
            with self.engine.begin() as conn:
                row = self._resolve(conn, user_id, share_code=share_code, valve_match_id=valve_match_id,
                                    demo_sha256=demo_sha256, verified=share_code_verified)
                if row is None or row.status != "imported":
                    return None
                self._merge_keys(conn, row, share_code=share_code, valve_match_id=valve_match_id,
                                 demo_sha256=demo_sha256, verified=share_code_verified)
                added = self._attach(conn, user_id, row.id, source, now)
                return self._owned_record(conn, user_id, row.id), added

        return self._retry_once(attempt)

    def record_uploaded_match(self, user_id: str, *, match: NewMatch, now: datetime) -> tuple[str, bool]:
        def attempt():
            with self.engine.begin() as conn:
                return self._store_deduped(conn, user_id, match, now)

        return self._retry_once(attempt)

    def find_match(self, user_id, *, share_code=None, valve_match_id=None, demo_sha256=None):
        keys = []
        if share_code:
            keys.append(matches.c.share_code == share_code)
        if valve_match_id and valve_match_id != UNKNOWN_MATCH_ID:
            keys.append(matches.c.valve_match_id == valve_match_id)
        if demo_sha256:
            keys.append(matches.c.demo_sha256 == demo_sha256)
        if not keys:
            return None
        with self.engine.begin() as conn:
            row = conn.execute(
                select(*_OWNED_COLUMNS).join(match_owners, match_owners.c.match_id == matches.c.id)
                .where(and_(match_owners.c.user_id == user_id, or_(*keys)))
                .order_by(match_owners.c.added_at, matches.c.id)
            ).first()
        return _match_record(row, _Owner(row)) if row else None

    # Dedupe helpers (see Storage, "Dedupe") ---------------------------------------
    @staticmethod
    def _retry_once(attempt):
        """A concurrent import of the same match (another user or request) can win
        the race to a unique key; then the second try finds and shares its row."""

        try:
            return attempt()
        except IntegrityError:
            return attempt()

    @staticmethod
    def _owns(conn, user_id: str, match_id: str) -> bool:
        return conn.execute(select(match_owners.c.match_id).where(and_(
            match_owners.c.user_id == user_id, match_owners.c.match_id == match_id))).first() is not None

    def _attach(self, conn, user_id: str, match_id: str, source: str, now: datetime) -> bool:
        """Add the match to the user's list; False if it already was there."""

        if self._owns(conn, user_id, match_id):
            return False
        conn.execute(insert(match_owners).values(user_id=user_id, match_id=match_id, source=source, added_at=now))
        return True

    @staticmethod
    def _owned_record(conn, user_id: str, match_id: str) -> MatchRecord:
        row = conn.execute(
            select(*_OWNED_COLUMNS).join(match_owners, match_owners.c.match_id == matches.c.id)
            .where(and_(match_owners.c.user_id == user_id, matches.c.id == match_id))
        ).one()
        return _match_record(row, _Owner(row))

    @staticmethod
    def _code_holder(conn, share_code: str | None, valve_match_id: str | None, *, exclude: str | None = None):
        keys = []
        if _real_code(share_code):
            keys.append(matches.c.share_code == share_code)
        if valve_match_id and valve_match_id != UNKNOWN_MATCH_ID:
            keys.append(matches.c.valve_match_id == valve_match_id)
        if not keys:
            return None
        where = or_(*keys) if exclude is None else and_(or_(*keys), matches.c.id != exclude)
        return conn.execute(
            select(matches).where(where)
            .order_by(matches.c.share_code_verified.desc(), matches.c.imported_at, matches.c.id)
        ).first()

    def _resolve(self, conn, user_id, *, share_code, valve_match_id, demo_sha256, verified: bool):
        """The stored match these keys denote, under the trust rules, or None."""

        if demo_sha256:
            row = conn.execute(select(matches).where(matches.c.demo_sha256 == demo_sha256)).first()
            if row is not None:
                return row
        elif share_code and share_code.startswith(UPLOAD_KEY_PREFIX):  # "upload:<sha256>" names the demo too
            row = conn.execute(select(matches).where(matches.c.share_code == share_code)).first()
            if row is not None:
                return row
        holder = self._code_holder(conn, share_code, valve_match_id)
        if holder is not None and ((verified and holder.share_code_verified) or self._owns(conn, user_id, holder.id)):
            return holder
        return None

    @staticmethod
    def _strip_code(conn, row) -> None:
        """A verified share code wins over a contradicting upload hint: the hinted
        row becomes a plain upload (keyed by its demo hash)."""

        conn.execute(update(matches).where(matches.c.id == row.id).values(
            share_code=UPLOAD_KEY_PREFIX + (row.demo_sha256 or row.id), valve_match_id=UNKNOWN_MATCH_ID,
            share_code_verified=0))

    def _merge_keys(self, conn, row, *, share_code, valve_match_id, demo_sha256, verified: bool) -> None:
        """Fill in keys the stored row lacks; a verified share code also replaces an
        unverified one. Never takes a share code another row holds unless ours is
        verified and theirs is not."""

        values = {}
        if row.demo_sha256 is None and demo_sha256:
            values["demo_sha256"] = demo_sha256
        code = _real_code(share_code)
        known_id = valve_match_id if valve_match_id and valve_match_id != UNKNOWN_MATCH_ID else None
        row_has_code = _real_code(row.share_code) is not None
        if code and row.share_code == code:
            if verified and not row.share_code_verified:
                values["share_code_verified"] = 1
        elif code and (not row_has_code or (verified and not row.share_code_verified)):
            holder = self._code_holder(conn, code, known_id, exclude=row.id)
            if holder is None or (verified and not holder.share_code_verified):
                if holder is not None:
                    self._strip_code(conn, holder)
                values.update(share_code=code, valve_match_id=known_id or UNKNOWN_MATCH_ID,
                              share_code_verified=int(verified))
        elif not code and known_id and row.valve_match_id == UNKNOWN_MATCH_ID:
            if self._code_holder(conn, None, known_id, exclude=row.id) is None:
                values["valve_match_id"] = known_id
        if values:
            conn.execute(update(matches).where(matches.c.id == row.id).values(**values))

    def _store_deduped(self, conn, user_id: str, match: NewMatch, now: datetime) -> tuple[str, bool]:
        verified = match.share_code_verified
        row = self._resolve(conn, user_id, share_code=match.share_code, valve_match_id=match.valve_match_id,
                            demo_sha256=match.demo_sha256, verified=verified)
        if row is None:
            holder = self._code_holder(conn, match.share_code, match.valve_match_id)
            if holder is not None:  # the share code is taken by a match we may not join
                if verified and not holder.share_code_verified:
                    self._strip_code(conn, holder)
                elif match.demo_sha256:  # ignore the upload's hint
                    match = replace(match, share_code=UPLOAD_KEY_PREFIX + match.demo_sha256,
                                    valve_match_id=UNKNOWN_MATCH_ID, share_code_verified=False)
                else:  # pragma: no cover - a hint always comes with a demo hash
                    return holder.id, self._attach(conn, user_id, holder.id, match.source, now)
            match_id = self._insert_match(conn, user_id, match, now)
            return match_id, self._attach(conn, user_id, match_id, match.source, now)
        self._merge_keys(conn, row, share_code=match.share_code, valve_match_id=match.valve_match_id,
                         demo_sha256=match.demo_sha256, verified=verified)
        if row.status != "imported" and match.status == "imported" and match.rounds:
            # e.g. Steam sync had no demo (unavailable), then the user uploaded it.
            conn.execute(delete(rounds).where(rounds.c.match_id == row.id))
            self._insert_rounds(conn, row.id, match)
            conn.execute(update(matches).where(matches.c.id == row.id).values(
                status=match.status, status_reason=match.status_reason, map_name=match.map_name,
                rounds_count=len(match.rounds), score_ct=match.score_ct, score_t=match.score_t))
        return row.id, self._attach(conn, user_id, row.id, match.source, now)

    @staticmethod
    def _insert_rounds(conn, match_id: str, match: NewMatch) -> None:
        if match.rounds:
            conn.execute(insert(rounds), [
                dict(match_id=match_id, round_number=r.round_number, winner_side=r.winner_side,
                     opening_kill_side=r.opening_kill_side, opening_kill_seconds=r.opening_kill_seconds,
                     opening_weapon=r.opening_weapon, unscored_reason=r.unscored_reason)
                for r in match.rounds
            ])

    @classmethod
    def _insert_match(cls, conn, user_id: str, match: NewMatch, now: datetime) -> str:
        match_id = uuid.uuid4().hex
        conn.execute(insert(matches).values(
            id=match_id, user_id=user_id, share_code=match.share_code, valve_match_id=match.valve_match_id,
            status=match.status, status_reason=match.status_reason, map_name=match.map_name,
            rounds_count=len(match.rounds), imported_at=now, source=match.source, demo_sha256=match.demo_sha256,
            share_code_verified=int(match.share_code_verified and _real_code(match.share_code) is not None),
            score_ct=match.score_ct, score_t=match.score_t,
        ))
        cls._insert_rounds(conn, match_id, match)
        return match_id

    # Upload jobs -------------------------------------------------------------
    def create_upload_job(self, job: UploadJob, *, max_active: int | None = None) -> bool:
        values = dict(
            id=job.id, user_id=job.user_id, status=job.status, stage=job.stage, progress=job.progress,
            share_code=job.share_code, demo_path=job.demo_path, demo_sha256=job.demo_sha256,
            size_bytes=job.size_bytes, attempts=job.attempts, error=job.error, match_id=job.match_id,
            match_created=None if job.match_created is None else int(job.match_created),
            created_at=job.created_at, updated_at=job.updated_at, started_at=job.started_at,
            finished_at=job.finished_at, kind=job.kind,
        )
        with self.engine.begin() as conn:
            if max_active is not None and self._active_jobs(conn) >= max_active:
                return False
            conn.execute(insert(upload_jobs).values(**values))
        return True

    @staticmethod
    def _active_jobs(conn) -> int:
        return conn.execute(select(func.count()).select_from(upload_jobs)
                            .where(upload_jobs.c.status.in_(UPLOAD_JOB_ACTIVE))).scalar_one()

    def enqueue_sync_job(self, job: UploadJob, *, expected_cursor: str, max_active: int, now: datetime):
        with self.engine.begin() as conn:
            self._check_cursor(conn, job.user_id, expected_cursor)  # row lock: serialises enqueues per user
            existing = conn.execute(select(upload_jobs).where(and_(
                upload_jobs.c.user_id == job.user_id, upload_jobs.c.kind == JOB_KIND_SYNC,
                upload_jobs.c.share_code == job.share_code))).first()
            if existing is not None and existing.status in UPLOAD_JOB_ACTIVE:
                self._advance_cursor(conn, job.user_id, job.share_code, now)
                return "exists", existing.id
            if self._active_jobs(conn) >= max_active:
                return "queue_full", None
            if existing is not None:  # finished earlier (e.g. the cursor was rewound): run it again
                conn.execute(update(upload_jobs).where(upload_jobs.c.id == existing.id).values(
                    status="queued", stage=None, progress=None, attempts=0, error=None, match_id=None,
                    match_created=None, started_at=None, finished_at=None, created_at=now, updated_at=now))
                job_id = existing.id
            else:
                conn.execute(insert(upload_jobs).values(
                    id=job.id, user_id=job.user_id, status="queued", share_code=job.share_code,
                    demo_path=job.demo_path, size_bytes=job.size_bytes, attempts=0, kind=JOB_KIND_SYNC,
                    created_at=now, updated_at=now))
                job_id = job.id
            self._advance_cursor(conn, job.user_id, job.share_code, now)
            return "queued", job_id

    def requeue_sync_jobs(self, user_id, *, errors, max_attempts, max_active, now) -> list[str]:
        requeued: list[str] = []
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(upload_jobs.c.id).where(and_(
                    upload_jobs.c.user_id == user_id, upload_jobs.c.kind == JOB_KIND_SYNC,
                    upload_jobs.c.status == "failed", upload_jobs.c.error.in_(errors),
                    upload_jobs.c.attempts < max_attempts))
                .order_by(upload_jobs.c.created_at, upload_jobs.c.id)
            ).all()
            active = self._active_jobs(conn)
            for row in rows:
                if active >= max_active:
                    break
                conn.execute(update(upload_jobs).where(upload_jobs.c.id == row.id).values(
                    status="queued", stage=None, progress=None, error=None, finished_at=None, updated_at=now))
                requeued.append(row.id)
                active += 1
        return requeued

    def get_upload_job(self, user_id: str, job_id: str) -> UploadJob | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(upload_jobs).where(
                and_(upload_jobs.c.user_id == user_id, upload_jobs.c.id == job_id))).first()
        return _upload_job(row) if row else None

    def list_upload_jobs(self, user_id: str, *, limit: int, kind: str | None = None) -> list[UploadJob]:
        where = upload_jobs.c.user_id == user_id
        if kind is not None:
            where = and_(where, upload_jobs.c.kind == kind)
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(upload_jobs).where(where)
                .order_by(upload_jobs.c.created_at.desc(), upload_jobs.c.id.desc()).limit(limit)
            ).all()
        return [_upload_job(row) for row in rows]

    def list_active_upload_jobs(self) -> list[UploadJob]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(upload_jobs).where(upload_jobs.c.status.in_(UPLOAD_JOB_ACTIVE))
                .order_by(upload_jobs.c.created_at, upload_jobs.c.id)
            ).all()
        return [_upload_job(row) for row in rows]

    def claim_next_upload_job(self, now: datetime) -> UploadJob | None:
        while True:
            with self.engine.begin() as conn:
                row = conn.execute(
                    select(upload_jobs.c.id).where(upload_jobs.c.status == "queued")
                    .order_by(upload_jobs.c.created_at, upload_jobs.c.id).limit(1)
                ).first()
                if row is None:
                    return None
                claimed = conn.execute(
                    update(upload_jobs).where(and_(upload_jobs.c.id == row.id, upload_jobs.c.status == "queued"))
                    .values(status="processing", stage=None, progress=None, attempts=upload_jobs.c.attempts + 1,
                            started_at=now, updated_at=now)
                ).rowcount
                if claimed == 1:
                    job = conn.execute(select(upload_jobs).where(upload_jobs.c.id == row.id)).one()
                    return _upload_job(job)
            # another worker claimed it first: try the next one

    def update_upload_job(self, job_id: str, now: datetime, **fields) -> None:
        unknown = set(fields) - _JOB_UPDATABLE
        if unknown:
            raise ValueError(f"not updatable: {sorted(unknown)}")
        if "match_created" in fields and fields["match_created"] is not None:
            fields["match_created"] = int(fields["match_created"])
        with self.engine.begin() as conn:
            conn.execute(update(upload_jobs).where(upload_jobs.c.id == job_id).values(updated_at=now, **fields))

    def upload_jobs_ahead(self, job: UploadJob) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                select(func.count()).select_from(upload_jobs).where(or_(
                    upload_jobs.c.status == "processing",
                    and_(upload_jobs.c.status == "queued", or_(
                        upload_jobs.c.created_at < job.created_at,
                        and_(upload_jobs.c.created_at == job.created_at, upload_jobs.c.id < job.id),
                    )),
                ))
            ).scalar_one()

    def delete_finished_upload_jobs(self, before: datetime) -> int:
        with self.engine.begin() as conn:
            return conn.execute(delete(upload_jobs).where(and_(
                upload_jobs.c.status.not_in(UPLOAD_JOB_ACTIVE), upload_jobs.c.updated_at < before))).rowcount

    # Reports ---------------------------------------------------------------
    def list_matches(self, user_id: str, *, limit: int, offset: int) -> list[MatchRecord]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(*_OWNED_COLUMNS).join(match_owners, match_owners.c.match_id == matches.c.id)
                .where(match_owners.c.user_id == user_id)
                .order_by(match_owners.c.added_at.desc(), matches.c.id.desc()).limit(limit).offset(offset)
            ).all()
        return [_match_record(row, _Owner(row)) for row in rows]

    def get_match(self, user_id: str, match_id: str):
        with self.engine.begin() as conn:
            row = conn.execute(
                select(*_OWNED_COLUMNS).join(match_owners, match_owners.c.match_id == matches.c.id)
                .where(and_(match_owners.c.user_id == user_id, matches.c.id == match_id))
            ).first()
            if not row:
                return None
            round_rows = conn.execute(
                select(rounds).where(rounds.c.match_id == match_id).order_by(rounds.c.round_number)
            ).all()
        return _match_record(row, _Owner(row)), [
            RoundRecord(round_number=r.round_number, winner_side=r.winner_side,
                        opening_kill_side=r.opening_kill_side, opening_kill_seconds=r.opening_kill_seconds,
                        opening_weapon=r.opening_weapon, unscored_reason=r.unscored_reason)
            for r in round_rows
        ]

    # Deletion ---------------------------------------------------------------
    def delete_user(self, user_id: str) -> None:
        # Explicit child deletes so this does not depend on FK cascade settings. Shared
        # matches stay for their other owners (handed over if this user imported them).
        with self.engine.begin() as conn:
            owned = select(match_owners.c.match_id).where(match_owners.c.user_id == user_id)
            touched = [r[0] for r in conn.execute(
                select(matches.c.id).where(or_(matches.c.user_id == user_id, matches.c.id.in_(owned)))).all()]
            conn.execute(delete(upload_jobs).where(upload_jobs.c.user_id == user_id))
            conn.execute(delete(match_owners).where(match_owners.c.user_id == user_id))
            for match_id in touched:
                heir = conn.execute(
                    select(match_owners.c.user_id).where(match_owners.c.match_id == match_id)
                    .order_by(match_owners.c.added_at, match_owners.c.user_id).limit(1)
                ).scalar_one_or_none()
                if heir is None:
                    conn.execute(update(upload_jobs).where(upload_jobs.c.match_id == match_id).values(match_id=None))
                    conn.execute(delete(rounds).where(rounds.c.match_id == match_id))
                    conn.execute(delete(matches).where(matches.c.id == match_id))
                else:
                    conn.execute(update(matches).where(and_(matches.c.id == match_id, matches.c.user_id == user_id))
                                 .values(user_id=heir))
            conn.execute(delete(sync_state).where(sync_state.c.user_id == user_id))
            conn.execute(delete(match_access).where(match_access.c.user_id == user_id))
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id))
            conn.execute(delete(users).where(users.c.id == user_id))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
