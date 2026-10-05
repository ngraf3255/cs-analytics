"""SQLAlchemy Core storage for PostgreSQL (production) and SQLite (tests)."""

from __future__ import annotations

import uuid
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


def _match_record(row) -> MatchRecord:
    return MatchRecord(
        id=row.id, share_code=row.share_code, valve_match_id=row.valve_match_id, status=row.status,
        status_reason=row.status_reason, map_name=row.map_name, rounds_count=row.rounds_count,
        imported_at=row.imported_at, source=row.source, demo_sha256=row.demo_sha256,
    )


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
        with self.engine.begin() as conn:
            self._check_cursor(conn, user_id, expected_cursor)
            _, inserted = self._store_deduped(conn, user_id, match, now)
            self._advance_cursor(conn, user_id, match.share_code, now)
            return inserted

    def skip_known_match(self, user_id, *, expected_cursor, share_code, valve_match_id, now) -> bool:
        with self.engine.begin() as conn:
            self._check_cursor(conn, user_id, expected_cursor)
            rows = self._find_rows(conn, user_id, share_code=share_code, valve_match_id=valve_match_id)
            if len(rows) != 1 or rows[0].status != "imported":
                return False
            self._merge_keys(conn, rows[0], NewMatch(
                share_code=share_code, valve_match_id=valve_match_id, status="imported",
                status_reason=None, map_name=None))
            self._advance_cursor(conn, user_id, share_code, now)
            return True

    def record_uploaded_match(self, user_id: str, *, match: NewMatch, now: datetime) -> tuple[str, bool]:
        try:
            with self.engine.begin() as conn:
                return self._store_deduped(conn, user_id, match, now)
        except IntegrityError:  # concurrent duplicate upload / sync of the same match
            with self.engine.begin() as conn:
                rows = self._find_rows(conn, user_id, share_code=match.share_code,
                                       valve_match_id=match.valve_match_id, demo_sha256=match.demo_sha256)
                if not rows:
                    raise
                return rows[0].id, False

    def find_match(self, user_id, *, share_code=None, valve_match_id=None, demo_sha256=None):
        with self.engine.begin() as conn:
            rows = self._find_rows(conn, user_id, share_code=share_code, valve_match_id=valve_match_id,
                                   demo_sha256=demo_sha256)
        return _match_record(rows[0]) if rows else None

    # Dedupe helpers ------------------------------------------------------------
    @staticmethod
    def _find_rows(conn, user_id, *, share_code=None, valve_match_id=None, demo_sha256=None) -> list:
        keys = []
        if share_code:
            keys.append(matches.c.share_code == share_code)
        if valve_match_id and valve_match_id != UNKNOWN_MATCH_ID:
            keys.append(matches.c.valve_match_id == valve_match_id)
        if demo_sha256:
            keys.append(matches.c.demo_sha256 == demo_sha256)
        if not keys:
            return []
        return conn.execute(
            select(matches).where(and_(matches.c.user_id == user_id, or_(*keys)))
            .order_by(matches.c.imported_at, matches.c.id)
        ).all()

    def _store_deduped(self, conn, user_id: str, match: NewMatch, now: datetime) -> tuple[str, bool]:
        rows = self._find_rows(conn, user_id, share_code=match.share_code,
                               valve_match_id=match.valve_match_id, demo_sha256=match.demo_sha256)
        if not rows:
            return self._insert_match(conn, user_id, match, now), True
        row = rows[0]
        if len(rows) == 1:  # >1 means the keys point at different rows; don't merge blindly
            self._merge_keys(conn, row, match)
        if row.status != "imported" and match.status == "imported" and match.rounds:
            # e.g. Steam sync had no demo (unavailable), then the user uploaded it.
            conn.execute(delete(rounds).where(rounds.c.match_id == row.id))
            self._insert_rounds(conn, row.id, match)
            conn.execute(update(matches).where(matches.c.id == row.id).values(
                status=match.status, status_reason=match.status_reason, map_name=match.map_name,
                rounds_count=len(match.rounds)))
        return row.id, False

    @staticmethod
    def _merge_keys(conn, row, match: NewMatch) -> None:
        """Fill in keys the stored row lacks (never overwrite a known key)."""

        values = {}
        if row.share_code.startswith(UPLOAD_KEY_PREFIX) and not match.share_code.startswith(UPLOAD_KEY_PREFIX):
            values["share_code"] = match.share_code
        if row.valve_match_id == UNKNOWN_MATCH_ID and match.valve_match_id != UNKNOWN_MATCH_ID:
            values["valve_match_id"] = match.valve_match_id
        if row.demo_sha256 is None and match.demo_sha256:
            values["demo_sha256"] = match.demo_sha256
        if values:
            conn.execute(update(matches).where(matches.c.id == row.id).values(**values))

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
                select(matches).where(matches.c.user_id == user_id)
                .order_by(matches.c.imported_at.desc(), matches.c.id.desc()).limit(limit).offset(offset)
            ).all()
        return [_match_record(row) for row in rows]

    def get_match(self, user_id: str, match_id: str):
        with self.engine.begin() as conn:
            row = conn.execute(
                select(matches).where(and_(matches.c.user_id == user_id, matches.c.id == match_id))
            ).first()
            if not row:
                return None
            round_rows = conn.execute(
                select(rounds).where(rounds.c.match_id == match_id).order_by(rounds.c.round_number)
            ).all()
        return _match_record(row), [
            RoundRecord(round_number=r.round_number, winner_side=r.winner_side,
                        opening_kill_side=r.opening_kill_side, opening_kill_seconds=r.opening_kill_seconds,
                        opening_weapon=r.opening_weapon, unscored_reason=r.unscored_reason)
            for r in round_rows
        ]

    # Deletion ---------------------------------------------------------------
    def delete_user(self, user_id: str) -> None:
        # Explicit child deletes so this does not depend on FK cascade settings.
        with self.engine.begin() as conn:
            match_ids = select(matches.c.id).where(matches.c.user_id == user_id)
            conn.execute(delete(upload_jobs).where(upload_jobs.c.user_id == user_id))
            conn.execute(delete(rounds).where(rounds.c.match_id.in_(match_ids)))
            conn.execute(delete(matches).where(matches.c.user_id == user_id))
            conn.execute(delete(sync_state).where(sync_state.c.user_id == user_id))
            conn.execute(delete(match_access).where(match_access.c.user_id == user_id))
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id))
            conn.execute(delete(users).where(users.c.id == user_id))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
