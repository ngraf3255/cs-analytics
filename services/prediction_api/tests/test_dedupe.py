"""Cross-source dedupe: the same match arriving by upload and by Steam sync (either order) is stored once.

Keys (any one matching = same match; across users see test_shared_matches.py): share code, Valve match id
(decoded from the share code; unknown for uploads without one), SHA-256 of the
decompressed .dem (known whenever we had the file).
Runs on SQLite by default and on PostgreSQL with CSA_TEST_DATABASE_URL.
"""

import shutil
from datetime import timedelta

import pytest

from steamlink import sharecode
from steamlink.crypto import AuthCodeCipher
from steamlink.migrate import MIGRATIONS_DIR, apply_migrations
from steamlink.storage.base import NewMatch, RoundRecord
from steamlink.storage.sql import SqlStorage
from steamlink.sync import SyncService
from steamlink.upload import UploadRejected, import_uploaded_demo, sha256_file

from dbutil import make_test_engine
from fakes import KEY, Clock, FakeFetcher, FakeHistory, FakeLocator, FakeParser, code, make_storage

STEAM_ID = "76561198000000077"
AUTH = "AB12-CDE34-FG56"
DEMO = b"PBDEMS2\0" + b"\x07" * 2048


def demo_url(n):
    share = sharecode.decode(code(n))
    return f"http://replay1.valve.net/730/{share.match_id}_{share.outcome_id}.dem.bz2"


@pytest.fixture()
def env(tmp_path):
    storage, clock, cipher = make_storage(tmp_path), Clock(), AuthCodeCipher([KEY])
    user = storage.get_or_create_user(STEAM_ID, clock())
    storage.set_match_access(user.id, ciphertext=cipher.encrypt(STEAM_ID, AUTH), last4="FG56",
                             cursor_share_code=code(0), now=clock())
    fetcher, parser, locator = FakeFetcher(), FakeParser(), FakeLocator()
    sync = SyncService(storage=storage, history=FakeHistory([code(i) for i in range(3)]), locator=locator,
                       fetcher=fetcher, parser=parser, cipher=cipher, clock=clock, max_matches=5,
                       min_interval_seconds=0)

    def upload(data=DEMO, share_code=None):
        raw = tmp_path / "up.bin"
        raw.write_bytes(data)
        work = tmp_path / "work"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        clock.advance(1)
        return import_uploaded_demo(storage=storage, parser=parser, user_id=user.id, raw_path=str(raw),
                                    workdir=str(work), max_compressed_bytes=1 << 20, max_demo_bytes=1 << 20,
                                    now=clock(), share_code=share_code)

    from types import SimpleNamespace

    from steamlink.config import Settings
    from steamlink.jobs import UploadJobWorker

    ctx = SimpleNamespace(storage=storage, clock=clock, sync=sync,
                          settings=Settings(allowed_origins=[], upload_job_dir=str(tmp_path / "jobs")))
    ctx.jobs = UploadJobWorker(ctx)

    def run_sync():
        """POST /steam/sync queues jobs; then run them like the worker thread does."""
        clock.advance(60)
        outcome = sync.sync(user, max_active=10, job_file=ctx.jobs.job_file)
        while (job := storage.claim_next_upload_job(clock())) is not None:
            ctx.jobs.process(job)
        return outcome

    def all_matches():
        return storage.list_matches(user.id, limit=50, offset=0)

    return dict(storage=storage, user=user, fetcher=fetcher, parser=parser, locator=locator, upload=upload,
                sync=run_sync, matches=all_matches, clock=clock)


def test_upload_then_sync_dedupes_by_demo_hash_and_attaches_share_code(env):
    env["fetcher"].content[demo_url(1)] = DEMO  # Valve serves the same file the user uploaded
    first = env["upload"]()
    assert first.created
    outcome = env["sync"]()
    assert outcome.status == "up_to_date" and outcome.processed == 2 and outcome.queued == 2
    jobs = {j.share_code: j for j in env["storage"].list_upload_jobs(env["user"].id, limit=10, kind="steam_sync")}
    assert (jobs[code(1)].match_id, jobs[code(1)].match_created) == (first.match_id, False)  # hash dedupe
    assert jobs[code(2)].match_created is True
    by_id = {m.id: m for m in env["matches"]()}
    assert len(by_id) == 2  # code(1) == the upload; code(2) is a new match
    merged = by_id[first.match_id]
    assert merged.share_code == code(1) and merged.valve_match_id == str(sharecode.decode(code(1)).match_id)
    assert merged.source == "upload" and merged.demo_sha256
    assert env["storage"].get_match_access(env["user"].id).cursor_share_code == code(2)


def test_upload_with_share_code_then_sync_skips_the_download(env):
    first = env["upload"](share_code=code(1))
    assert first.created
    env["sync"]()
    assert demo_url(1) not in env["fetcher"].fetched and demo_url(2) in env["fetcher"].fetched
    matches = env["matches"]()
    assert sorted(m.share_code for m in matches) == sorted([code(1), code(2)])
    assert env["parser"].calls == 2  # the upload + code(2); code(1) never parsed again


def test_sync_then_upload_of_same_demo_is_not_parsed_or_stored_twice(env):
    env["fetcher"].content[demo_url(1)] = DEMO
    env["sync"]()
    calls = env["parser"].calls
    synced = {m.share_code: m for m in env["matches"]()}[code(1)]
    for kwargs in ({}, {"share_code": code(1)}):  # by hash, and by share code
        again = env["upload"](**kwargs)
        assert (again.match_id, again.created) == (synced.id, False)
    # A .bz2 of the same demo hashes the decompressed bytes, so it dedupes too.
    import bz2
    assert env["upload"](bz2.compress(DEMO)).match_id == synced.id
    assert env["parser"].calls == calls and len(env["matches"]()) == 2


def test_upload_fills_in_a_match_steam_could_not_provide(env):
    from steamlink.valve import DemoUnavailable

    env["locator"].errors[sharecode.decode(code(1)).match_id] = DemoUnavailable()
    env["sync"]()
    stub = {m.share_code: m for m in env["matches"]()}[code(1)]
    assert stub.status == "unavailable" and stub.rounds_count == 0
    up = env["upload"](share_code=code(1))
    assert (up.match_id, up.created) == (stub.id, False)
    record, rounds = env["storage"].get_match(env["user"].id, stub.id)
    assert record.status == "imported" and record.rounds_count == len(rounds) == 2 and record.demo_sha256
    assert (stub.score_ct, record.score_ct, record.score_t) == (None, 1, 1)  # the upload brings the score


def test_different_demos_are_different_matches(env):
    assert env["upload"](DEMO).created
    assert env["upload"](DEMO + b"x").created
    assert len(env["matches"]()) == 2


def test_upload_rejects_malformed_share_code(env):
    with pytest.raises(UploadRejected) as err:
        env["upload"](share_code="CSGO-nope")
    assert err.value.reason == "invalid_share_code_format"


def test_storage_level_keys_never_overwrite_known_values(env):
    storage, user, now = env["storage"], env["user"], env["clock"]()
    rounds = (RoundRecord(1, "ct", "t", 3.0, "ak47", None),)
    a = NewMatch(share_code=code(1), valve_match_id="111", status="imported", status_reason=None,
                 map_name="de_inferno", rounds=rounds, demo_sha256="h1")
    first_id, inserted = storage.record_uploaded_match(user.id, match=a, now=now)
    assert inserted
    # Same Valve match id, different (re-encoded) demo hash: still the same match; hash kept.
    b = NewMatch(share_code="upload:h2", valve_match_id="111", status="imported", status_reason=None,
                 map_name="de_inferno", rounds=rounds, demo_sha256="h2", source="upload")
    assert storage.record_uploaded_match(user.id, match=b, now=now + timedelta(seconds=1)) == (first_id, False)
    stored = storage.find_match(user.id, valve_match_id="111")
    assert (stored.share_code, stored.demo_sha256) == (code(1), "h1")
    assert storage.find_match(user.id, demo_sha256="h2") is None
    assert storage.find_match(user.id) is None


def test_unique_indexes_back_up_the_dedupe(env):
    from sqlalchemy import insert
    from sqlalchemy.exc import IntegrityError

    from steamlink.storage.sql import matches

    storage, user, now = env["storage"], env["user"], env["clock"]()
    base = dict(user_id=user.id, status="imported", rounds_count=0, imported_at=now, source="upload")
    with storage.engine.begin() as conn:
        conn.execute(insert(matches).values(id="m1", share_code="upload:a", valve_match_id="upload",
                                            demo_sha256="dup", **base))
        # Unknown match ids ('upload') are excluded from the match-id index.
        conn.execute(insert(matches).values(id="m2", share_code="upload:b", valve_match_id="upload",
                                            demo_sha256="other", **base))
        conn.execute(insert(matches).values(id="m3", share_code="upload:c", valve_match_id="999",
                                            demo_sha256=None, **base))
        conn.execute(insert(matches).values(id="m4", share_code="upload:d", valve_match_id="998",
                                            demo_sha256=None, **base))  # NULL hashes don't collide
    for dup in (dict(share_code="upload:e", valve_match_id="upload", demo_sha256="dup"),
                dict(share_code="upload:f", valve_match_id="999", demo_sha256=None)):
        with pytest.raises(IntegrityError):
            with storage.engine.begin() as conn:
                conn.execute(insert(matches).values(id="mx", **base, **dup))


def test_migration_backfills_uploads_stored_before_dedupe(tmp_path):
    old = tmp_path / "old_migrations"
    old.mkdir()
    shutil.copy(MIGRATIONS_DIR / "0001_steam_sync.sql", old)
    engine = make_test_engine(tmp_path, "backfill")
    apply_migrations(engine, old)
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, steam_id, created_at, updated_at) "
                          "VALUES ('u', '7656', '2026-10-04 12:00:00+00', '2026-10-04 12:00:00+00')"))
        for mid, share in (("m1", "upload:abc123"), ("m2", code(1))):
            conn.execute(text(
                "INSERT INTO matches (id, user_id, share_code, valve_match_id, status, rounds_count, imported_at) "
                "VALUES (:id, 'u', :share, :vid, 'imported', 0, '2026-10-04 12:00:00+00')"),
                {"id": mid, "share": share, "vid": "upload" if share.startswith("upload:") else "1001"})
    assert apply_migrations(engine) == ["0002_cross_source_dedupe", "0003_upload_jobs", "0004_sync_jobs",
                                        "0005_shared_matches", "0006_match_score", "0007_player_rounds", "0008_parse_version"]
    storage = SqlStorage(engine)
    up = storage.find_match("u", demo_sha256="abc123")
    assert up is not None and up.id == "m1" and up.source == "upload" and not up.has_share_code
    synced = storage.find_match("u", share_code=code(1))
    assert synced.source == "steam_sync" and synced.demo_sha256 is None


def test_sha256_matches_hashlib(tmp_path):
    import hashlib

    path = tmp_path / "x.dem"
    path.write_bytes(DEMO)
    assert sha256_file(str(path)) == hashlib.sha256(DEMO).hexdigest()
