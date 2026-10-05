"""SQLite (default) or PostgreSQL (CSA_TEST_DATABASE_URL) exercises of the storage interface and migrations."""

from datetime import datetime, timedelta, timezone

import pytest

from steamlink.migrate import apply_migrations
from steamlink.storage.base import CursorConflict, NewMatch, RoundRecord
from steamlink.storage.sql import SqlStorage, metadata

from dbutil import make_test_engine


NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def storage(tmp_path):
    engine = make_test_engine(tmp_path)
    applied = apply_migrations(engine)
    assert applied == ["0001_steam_sync", "0002_cross_source_dedupe"]
    # Metadata tables must match the migration (re-create is a no-op if identical).
    metadata.create_all(engine)
    return SqlStorage(engine)


def test_user_session_roundtrip(storage):
    user = storage.get_or_create_user("76561198000000001", NOW)
    again = storage.get_or_create_user("76561198000000001", NOW + timedelta(seconds=1))
    assert again.id == user.id
    storage.create_session("hash1", user.id, NOW, NOW + timedelta(hours=1))
    assert storage.get_session_user("hash1", NOW + timedelta(minutes=1)).id == user.id
    assert storage.get_session_user("hash1", NOW + timedelta(hours=2)) is None
    storage.delete_session("hash1")
    assert storage.get_session_user("hash1", NOW) is None


def test_match_access_and_cursor_idempotent_record(storage):
    user = storage.get_or_create_user("76561198000000002", NOW)
    storage.set_match_access(
        user.id, ciphertext="c", last4="1234", cursor_share_code="CSGO-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE", now=NOW
    )
    access = storage.get_match_access(user.id)
    assert access.cursor_share_code.endswith("EEEEE")
    match = NewMatch(
        share_code="CSGO-FFFFF-GGGGG-HHHHH-IIIII-JJJJJ",
        valve_match_id="1-2-3",
        status="imported",
        status_reason=None,
        map_name="de_mirage",
        rounds=(
            RoundRecord(1, "ct", "t", 12.5, "ak47", None),
            RoundRecord(2, "t", None, None, None, "no_opening_kill"),
        ),
    )
    assert storage.record_match(user.id, expected_cursor=access.cursor_share_code, match=match, now=NOW) is True
    # Retry with the old cursor must fail (cursor already advanced).
    with pytest.raises(CursorConflict):
        storage.record_match(user.id, expected_cursor=access.cursor_share_code, match=match, now=NOW)
    # Same share code with the new cursor: no second insert, cursor stays.
    assert storage.record_match(
        user.id, expected_cursor=match.share_code, match=match, now=NOW + timedelta(seconds=1)
    ) is False
    listed = storage.list_matches(user.id, limit=10, offset=0)
    assert len(listed) == 1
    detail = storage.get_match(user.id, listed[0].id)
    assert detail is not None
    assert len(detail[1]) == 2
    assert storage.get_match_access(user.id).cursor_share_code == match.share_code


def test_sync_lock_and_delete_user(storage):
    user = storage.get_or_create_user("76561198000000003", NOW)
    storage.set_match_access(
        user.id, ciphertext="c", last4="9999", cursor_share_code="CSGO-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE", now=NOW
    )
    assert storage.try_acquire_sync_lock(user.id, "tok-a", NOW, ttl_seconds=60) is True
    assert storage.try_acquire_sync_lock(user.id, "tok-b", NOW + timedelta(seconds=1), ttl_seconds=60) is False
    # Expired lock can be taken over.
    assert storage.try_acquire_sync_lock(user.id, "tok-c", NOW + timedelta(seconds=61), ttl_seconds=60) is True
    storage.release_sync_lock(user.id, "tok-c", NOW + timedelta(seconds=62), status="ok", error=None, imported=1)
    state = storage.get_sync_state(user.id, NOW + timedelta(seconds=63))
    assert state.status == "ok" and state.last_imported_count == 1 and not state.locked
    storage.delete_user(user.id)
    assert storage.get_match_access(user.id) is None
    assert storage.list_matches(user.id, limit=10, offset=0) == []


def test_migration_columns_match_sqlalchemy_metadata(tmp_path):
    from sqlalchemy import inspect

    engine = make_test_engine(tmp_path, 'drift')
    apply_migrations(engine)
    inspector = inspect(engine)
    for table in metadata.sorted_tables:
        db_cols = {col["name"] for col in inspector.get_columns(table.name)}
        assert db_cols == {col.name for col in table.columns}, table.name


def test_uploaded_match_is_idempotent_and_leaves_cursor(storage):
    user = storage.get_or_create_user("76561198000000009", NOW)
    match = NewMatch(share_code="upload:abc", valve_match_id="upload", status="imported", status_reason=None,
                     map_name="de_nuke", rounds=(RoundRecord(1, "ct", "ct", 5.0, "awp", None),))
    first_id, inserted = storage.record_uploaded_match(user.id, match=match, now=NOW)
    again_id, again = storage.record_uploaded_match(user.id, match=match, now=NOW)
    assert inserted and not again and first_id == again_id
    assert storage.get_match_access(user.id) is None
    assert len(storage.get_match(user.id, first_id)[1]) == 1


def test_backend_is_the_one_requested(storage):
    """Guards the opt-in PostgreSQL mode against silently falling back to SQLite."""

    from dbutil import postgres_url

    assert storage.engine.dialect.name == ("postgresql" if postgres_url() else "sqlite")


def test_concurrent_migration_runs_apply_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from dbutil import postgres_url

    engine = make_test_engine(tmp_path, "race")
    if not postgres_url():
        pytest.skip("advisory-lock race only meaningful on PostgreSQL")
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: apply_migrations(engine), range(4)))
    from steamlink.migrate import MIGRATIONS_DIR

    all_versions = sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))
    # Every migration applied exactly once across the racing runners.
    assert sorted(v for r in results for v in r) == all_versions
