"""SQLAlchemy Core storage for PostgreSQL (production) and SQLite (tests)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
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
    CursorConflict,
    MatchAccess,
    MatchRecord,
    NewMatch,
    RoundRecord,
    Storage,
    SyncState,
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
        imported_at=row.imported_at,
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

    def record_match(self, user_id: str, *, expected_cursor: str, match: NewMatch, now: datetime) -> bool:
        with self.engine.begin() as conn:
            current = conn.execute(
                select(match_access.c.cursor_share_code)
                .where(match_access.c.user_id == user_id).with_for_update()
            ).scalar_one_or_none()
            if current is None or current != expected_cursor:
                raise CursorConflict()
            existing = conn.execute(
                select(matches.c.id).where(and_(matches.c.user_id == user_id, matches.c.share_code == match.share_code))
            ).first()
            inserted = False
            if not existing:
                self._insert_match(conn, user_id, match, now)
                inserted = True
            conn.execute(
                update(match_access).where(match_access.c.user_id == user_id)
                .values(cursor_share_code=match.share_code, updated_at=now)
            )
            return inserted

    def record_uploaded_match(self, user_id: str, *, match: NewMatch, now: datetime) -> tuple[str, bool]:
        try:
            with self.engine.begin() as conn:
                existing = conn.execute(select(matches.c.id).where(
                    and_(matches.c.user_id == user_id, matches.c.share_code == match.share_code))).first()
                if existing:
                    return existing.id, False
                match_id = self._insert_match(conn, user_id, match, now)
                return match_id, True
        except IntegrityError:  # concurrent duplicate upload
            with self.engine.begin() as conn:
                row = conn.execute(select(matches.c.id).where(
                    and_(matches.c.user_id == user_id, matches.c.share_code == match.share_code))).one()
                return row.id, False

    @staticmethod
    def _insert_match(conn, user_id: str, match: NewMatch, now: datetime) -> str:
        match_id = uuid.uuid4().hex
        conn.execute(insert(matches).values(
            id=match_id, user_id=user_id, share_code=match.share_code, valve_match_id=match.valve_match_id,
            status=match.status, status_reason=match.status_reason, map_name=match.map_name,
            rounds_count=len(match.rounds), imported_at=now,
        ))
        if match.rounds:
            conn.execute(insert(rounds), [
                dict(match_id=match_id, round_number=r.round_number, winner_side=r.winner_side,
                     opening_kill_side=r.opening_kill_side, opening_kill_seconds=r.opening_kill_seconds,
                     opening_weapon=r.opening_weapon, unscored_reason=r.unscored_reason)
                for r in match.rounds
            ])
        return match_id

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
            conn.execute(delete(rounds).where(rounds.c.match_id.in_(match_ids)))
            conn.execute(delete(matches).where(matches.c.user_id == user_id))
            conn.execute(delete(sync_state).where(sync_state.c.user_id == user_id))
            conn.execute(delete(match_access).where(match_access.c.user_id == user_id))
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id))
            conn.execute(delete(users).where(users.c.id == user_id))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
