"""Steam sync: the request walks the share-code history and queues background jobs;
the job worker downloads, parses and stores each match (steamlink.sync / steamlink.jobs).
Runs on SQLite by default and on PostgreSQL with CSA_TEST_DATABASE_URL."""

import bz2
import hashlib
import os
from types import SimpleNamespace

import pytest

from steamlink import sharecode
from steamlink.config import Settings
from steamlink.crypto import AuthCodeCipher
from steamlink.demo_parser import DemoParseError
from steamlink.jobs import UploadJobWorker
from steamlink.storage.base import JOB_KIND_SYNC, UploadJob
from steamlink.sync import SyncRejected, SyncService
from steamlink.valve import DemoNotReady, DemoTooLarge, DemoUnavailable, UnconfiguredDemoLocator

from fakes import KEY, Clock, FakeFetcher, FakeHistory, FakeLocator, FakeParser, code, make_storage

STEAM_ID = "76561198000000001"
AUTH = "AB12-CDE34-FG56"
DEMO = b"PBDEMS2\0" + b"\x05" * 4096


def demo_url(n):
    share = sharecode.decode(code(n))
    return f"http://replay1.valve.net/730/{share.match_id}_{share.outcome_id}.dem.bz2"


def match_id_of(n):
    return sharecode.decode(code(n)).match_id


@pytest.fixture()
def env(tmp_path):
    storage = make_storage(tmp_path)
    clock = Clock()
    cipher = AuthCodeCipher([KEY])
    user = storage.get_or_create_user(STEAM_ID, clock())
    storage.set_match_access(user.id, ciphertext=cipher.encrypt(STEAM_ID, AUTH), last4="FG56",
                             cursor_share_code=code(0), now=clock())
    history = FakeHistory([code(i) for i in range(5)])
    locator, fetcher, parser = FakeLocator(), FakeFetcher(), FakeParser()
    settings = Settings(allowed_origins=[], upload_job_dir=str(tmp_path / "jobs"), upload_queue_max=10)
    ctx = SimpleNamespace(storage=storage, clock=clock, settings=settings, sync=None)
    ctx.jobs = UploadJobWorker(ctx)

    def service(**kw):
        opts = dict(storage=storage, history=history, locator=locator, fetcher=fetcher, parser=parser,
                    cipher=cipher, clock=clock, max_matches=2, min_interval_seconds=30)
        opts.update(kw)
        ctx.sync = SyncService(**opts)
        return ctx.sync

    def sync(svc=None, max_active=None):
        svc = svc or ctx.sync or service()
        clock.advance(60)
        return svc.sync(user, max_active=max_active or ctx.settings.upload_queue_max, job_file=ctx.jobs.job_file)

    def drain():
        """Run queued jobs synchronously, like the worker thread does."""
        while (job := storage.claim_next_upload_job(clock())) is not None:
            ctx.jobs.process(job)

    def jobs():
        return {j.share_code: j for j in storage.list_upload_jobs(user.id, limit=50, kind=JOB_KIND_SYNC)}

    def matches():
        return {m.share_code: m for m in storage.list_matches(user.id, limit=50, offset=0)}

    service()
    return SimpleNamespace(storage=storage, clock=clock, user=user, history=history, locator=locator,
                           fetcher=fetcher, parser=parser, service=service, cipher=cipher, ctx=ctx, sync=sync,
                           drain=drain, jobs=jobs, matches=matches)


def cursor(env):
    return env.storage.get_match_access(env.user.id).cursor_share_code


def test_sync_queues_jobs_quickly_and_the_worker_imports_them(env):
    first = env.sync()
    assert (first.status, first.queued, first.skipped, first.has_more) == ("partial", 2, 0, True)
    assert len(first.job_ids) == 2
    assert cursor(env) == code(2)  # advanced when the jobs were queued
    assert env.fetcher.fetched == [] and env.parser.calls == 0  # nothing downloaded in the request
    jobs = env.jobs()
    assert {jobs[code(1)].status, jobs[code(2)].status} == {"queued"}
    assert env.storage.get_sync_state(env.user.id, env.clock()).status == "ok"  # lock released

    env.drain()
    jobs, matches = env.jobs(), env.matches()
    assert [jobs[code(i)].status for i in (1, 2)] == ["done", "done"]
    assert jobs[code(1)].match_id == matches[code(1)].id and jobs[code(1)].match_created is True
    m = matches[code(1)]
    assert (m.status, m.source, m.rounds_count) == ("imported", "steam_sync", 2)
    assert m.demo_sha256 == hashlib.sha256(b"PBDEMS2\0" + demo_url(1).encode()).hexdigest()
    assert m.valve_match_id == str(match_id_of(1))
    assert os.listdir(env.ctx.jobs.job_dir) == []  # downloads and work dirs removed

    second = env.sync()
    assert (second.status, second.queued) == ("partial", 2)
    third = env.sync()
    assert (third.status, third.queued, third.has_more) == ("up_to_date", 0, False)
    env.drain()
    assert cursor(env) == code(4)
    assert sorted(env.matches()) == sorted(code(i) for i in range(1, 5))


def test_downloaded_bz2_is_decompressed_and_hashed_like_an_upload(env):
    env.fetcher.content[demo_url(1)] = bz2.compress(DEMO)
    env.sync(env.service(max_matches=1))
    env.drain()
    m = env.matches()[code(1)]
    assert m.status == "imported" and m.demo_sha256 == hashlib.sha256(DEMO).hexdigest()


def test_queue_cap_stops_queueing_without_advancing_the_cursor(env):
    out = env.sync(env.service(max_matches=10), max_active=1)
    assert (out.status, out.queued, out.has_more) == ("queue_full", 1, True)
    assert cursor(env) == code(1)
    again = env.sync(max_active=1)  # the first job is still queued: nothing new fits
    assert (again.status, again.queued) == ("queue_full", 0) and cursor(env) == code(1)
    env.drain()
    rest = env.sync(max_active=10)
    assert (rest.status, rest.queued) == ("up_to_date", 3)


def test_a_known_match_is_skipped_without_download(env):
    env.sync(env.service(max_matches=1))
    env.drain()
    assert len(env.fetcher.fetched) == 1
    # Stale cursor (e.g. re-linked with an older share code): the stored match is skipped.
    env.storage.set_match_access(env.user.id, ciphertext=env.cipher.encrypt(STEAM_ID, AUTH), last4="FG56",
                                 cursor_share_code=code(0), now=env.clock())
    out = env.sync(env.service())
    assert (out.skipped, out.queued) == (1, 1)  # code(1) known, code(2) new
    assert env.fetcher.fetched == [demo_url(1)]
    assert len(env.jobs()) == 2


def test_a_match_already_queued_is_not_queued_twice(env):
    env.sync(env.service(max_matches=1))
    env.storage.set_match_access(env.user.id, ciphertext=env.cipher.encrypt(STEAM_ID, AUTH), last4="FG56",
                                 cursor_share_code=code(0), now=env.clock())
    out = env.sync(env.service())
    assert out.queued == 1 and len(out.job_ids) == 2  # code(1)'s existing job + code(2)
    assert len(env.jobs()) == 2 and cursor(env) == code(2)
    env.drain()
    assert len(env.matches()) == 2 and len(env.fetcher.fetched) == 2


def test_upload_while_queued_skips_the_download(env):
    from steamlink.upload import import_uploaded_demo

    env.sync(env.service(max_matches=1))
    raw = os.path.join(env.ctx.jobs.job_dir, "manual.bin")
    with open(raw, "wb") as fh:
        fh.write(DEMO)
    work = os.path.join(env.ctx.jobs.job_dir, "manual.work")
    os.makedirs(work)
    up = import_uploaded_demo(storage=env.storage, parser=env.parser, user_id=env.user.id, raw_path=raw,
                              workdir=work, max_compressed_bytes=1 << 20, max_demo_bytes=1 << 20,
                              now=env.clock(), share_code=code(1))
    env.drain()
    job = env.jobs()[code(1)]
    assert (job.status, job.match_id, job.match_created) == ("done", up.match_id, False)
    assert env.fetcher.fetched == [] and env.parser.calls == 1


def test_lock_and_rate_limit(env):
    storage, user, clock = env.storage, env.user, env.clock
    assert storage.try_acquire_sync_lock(user.id, "other", clock(), 900)
    with pytest.raises(SyncRejected) as exc:
        env.ctx.sync.sync(user, max_active=10, job_file=env.ctx.jobs.job_file)
    assert exc.value.reason == "already_running"
    storage.release_sync_lock(user.id, "other", clock(), status="ok", error=None, imported=0)
    with pytest.raises(SyncRejected) as exc:
        env.ctx.sync.sync(user, max_active=10, job_file=env.ctx.jobs.job_file)
    assert exc.value.reason == "too_soon"


def test_not_linked(env):
    env.storage.delete_match_access(env.user.id)
    with pytest.raises(SyncRejected) as exc:
        env.sync()
    assert exc.value.reason == "not_linked"


def test_demo_not_ready_is_retried_by_the_next_sync(env):
    env.locator.errors[match_id_of(1)] = DemoNotReady()
    env.sync(env.service(max_matches=1))
    env.drain()
    job = env.jobs()[code(1)]
    assert (job.status, job.error, job.attempts) == ("failed", "demo_not_ready", 1)
    assert code(1) not in env.matches()
    del env.locator.errors[match_id_of(1)]  # Valve has the demo now
    out = env.sync()
    assert job.id in out.job_ids
    env.drain()
    job = env.jobs()[code(1)]
    assert (job.status, job.attempts) == ("done", 2)
    assert env.matches()[code(1)].status == "imported"


def test_demo_never_ready_becomes_an_unavailable_stub_after_max_attempts(env):
    env.locator.errors[match_id_of(1)] = DemoNotReady()
    svc = env.service(max_matches=1, max_job_attempts=2)
    env.sync(svc)
    env.drain()
    env.sync(svc)  # re-queues code(1) (attempt 2) and queues code(2)
    env.drain()
    job = env.jobs()[code(1)]
    assert (job.status, job.attempts) == ("done", 2)
    stub = env.matches()[code(1)]
    assert (stub.status, stub.status_reason, stub.id) == ("unavailable", "demo_unavailable", job.match_id)
    out = env.sync(svc)
    assert job.id not in out.job_ids  # finished: not retried again


@pytest.mark.parametrize("error,reason", [(DemoUnavailable(), "demo_unavailable"), (DemoTooLarge(), "demo_too_large")])
def test_permanent_download_failures_store_a_stub(env, error, reason):
    env.fetcher.errors[demo_url(1)] = error
    env.sync()
    env.drain()
    matches = env.matches()
    assert (matches[code(1)].status, matches[code(1)].status_reason) == ("unavailable", reason)
    assert matches[code(2)].status == "imported"
    assert env.jobs()[code(1)].status == "done"


def test_locator_unavailable_is_recorded_and_skipped(env):
    env.locator.errors[match_id_of(1)] = DemoUnavailable()
    env.sync()
    env.drain()
    matches = env.matches()
    assert matches[code(1)].status == "unavailable" and matches[code(2)].status == "imported"


def test_parse_failure_recorded(env):
    class Broken(FakeParser):
        def parse(self, demo_path):
            raise DemoParseError("bad")

    env.sync(env.service(parser=Broken(), max_matches=1))
    env.drain()
    match = env.matches()[code(1)]
    assert (match.status, match.status_reason) == ("parse_failed", "parser_error")


def test_not_a_demo_download_is_unavailable(env):
    env.fetcher.content[demo_url(1)] = b"<html>error page</html>"
    env.sync(env.service(max_matches=1))
    env.drain()
    assert env.matches()[code(1)].status_reason == "demo_unavailable"


def test_unconfigured_locator_stops_without_advancing(env):
    out = env.sync(env.service(locator=UnconfiguredDemoLocator()))
    assert (out.status, out.error, out.queued) == ("error", "demo_retrieval_not_configured", 0)
    assert cursor(env) == code(0) and env.jobs() == {}


def test_unconfigured_locator_still_skips_known_matches(env):
    env.sync(env.service(max_matches=1))
    env.drain()
    env.storage.set_match_access(env.user.id, ciphertext=env.cipher.encrypt(STEAM_ID, AUTH), last4="FG56",
                                 cursor_share_code=code(0), now=env.clock())
    out = env.sync(env.service(locator=UnconfiguredDemoLocator()))
    assert (out.status, out.skipped) == ("error", 1) and cursor(env) == code(1)


@pytest.mark.parametrize("forced", ["invalid_auth_code", "invalid_known_code", "rate_limited", "valve_error"])
def test_valve_errors_surface_safely(env, forced):
    env.history.forced = forced
    out = env.sync()
    assert (out.status, out.error) == ("error", forced)
    state = env.storage.get_sync_state(env.user.id, env.clock())
    assert state.status == "error" and state.last_error == forced
    assert cursor(env) == code(0)


def test_demo_bot_auth_failure_fails_the_job_and_the_next_sync_retries(env):
    from steamlink.gc import DemoBotAuthFailed

    env.locator.errors[match_id_of(1)] = DemoBotAuthFailed()
    env.sync(env.service(max_matches=1))
    env.drain()
    job = env.jobs()[code(1)]
    assert (job.status, job.error) == ("failed", "demo_bot_auth_failed")
    del env.locator.errors[match_id_of(1)]  # operator fixed the bot
    env.sync()
    env.drain()
    assert env.matches()[code(1)].status == "imported"


def test_worker_crash_fails_the_job_alone_and_a_retry_has_no_duplicates(env):
    env.sync(env.service(parser=FakeParser(fail_after_calls=1)))
    env.drain()
    jobs = env.jobs()
    assert jobs[code(1)].status == "done"
    assert (jobs[code(2)].status, jobs[code(2)].error) == ("failed", "internal_error")
    env.ctx.sync.parser = FakeParser()
    env.sync(env.service(max_matches=10))
    env.drain()
    assert sorted(env.matches()) == sorted(code(i) for i in range(1, 5))
    assert len(env.jobs()) == 4


def test_restart_recovery_redownloads_sync_jobs(env):
    """A new process: interrupted sync jobs are re-downloaded (file gone or partial), bounded."""

    from steamlink.storage.base import JOB_KIND_SYNC as KIND

    svc = env.service(max_job_attempts=3)
    storage, clock, job_dir = env.storage, env.clock, env.ctx.jobs.job_dir

    def job(n, status, attempts, partial=False):
        path = os.path.join(job_dir, f"j{n}.upload")
        if partial:
            with open(path, "wb") as fh:
                fh.write(b"BZh9partial")
        clock.advance(1)
        assert storage.create_upload_job(UploadJob(
            id=f"j{n}", user_id=env.user.id, status=status, demo_path=path, size_bytes=0, created_at=clock(),
            updated_at=clock(), share_code=code(n), attempts=attempts, kind=KIND))

    job(1, "processing", 1, partial=True)  # interrupted mid-download
    job(2, "processing", 3)  # interrupted on its last allowed attempt
    job(3, "queued", 0)  # never started (no file needed)
    restarted = SimpleNamespace(storage=storage, clock=clock, settings=env.ctx.settings, sync=svc)
    restarted.jobs = UploadJobWorker(restarted)
    restarted.jobs.start()
    assert restarted.jobs.wait_idle(10)
    jobs, matches = env.jobs(), env.matches()
    assert (jobs[code(1)].status, jobs[code(1)].attempts) == ("done", 2)
    assert matches[code(1)].status == "imported"
    assert (jobs[code(2)].status, matches[code(2)].status, matches[code(2)].status_reason) == (
        "done", "parse_failed", "parser_error")
    assert jobs[code(3)].status == "done" and matches[code(3)].status == "imported"
    assert env.fetcher.fetched == [demo_url(1), demo_url(3)]
    assert os.listdir(job_dir) == []


def test_storage_enqueue_is_atomic_with_the_cursor(env):
    from steamlink.storage.base import CursorConflict

    storage, clock = env.storage, env.clock
    job = UploadJob(id="x", user_id=env.user.id, status="queued", demo_path="/tmp/x", size_bytes=0,
                    created_at=clock(), updated_at=clock(), share_code=code(1), kind=JOB_KIND_SYNC)
    with pytest.raises(CursorConflict):
        storage.enqueue_sync_job(job, expected_cursor=code(3), max_active=5, now=clock())
    assert storage.enqueue_sync_job(job, expected_cursor=code(0), max_active=5, now=clock()) == ("queued", "x")
    assert cursor(env) == code(1)
    other = UploadJob(**{**job.__dict__, "id": "y", "share_code": code(2)})
    assert storage.enqueue_sync_job(other, expected_cursor=code(1), max_active=1, now=clock()) == ("queue_full", None)
    assert cursor(env) == code(1)
    # Same share code again (rewound cursor): no second row, only the cursor moves.
    storage.set_match_access(env.user.id, ciphertext="c", last4="FG56", cursor_share_code=code(0), now=clock())
    assert storage.enqueue_sync_job(replace_id(job, "z"), expected_cursor=code(0), max_active=1,
                                    now=clock()) == ("exists", "x")
    storage.update_upload_job("x", clock(), status="failed", error="demo_not_ready")
    assert storage.requeue_sync_jobs(env.user.id, errors=("demo_not_ready",), max_attempts=1, max_active=5,
                                     now=clock()) == ["x"]  # attempts 0 < 1
    storage.update_upload_job("x", clock(), status="done")
    storage.set_match_access(env.user.id, ciphertext="c", last4="FG56", cursor_share_code=code(0), now=clock())
    assert storage.enqueue_sync_job(replace_id(job, "w"), expected_cursor=code(0), max_active=5,
                                    now=clock()) == ("queued", "x")  # finished row reused
    assert storage.get_upload_job(env.user.id, "x").status == "queued"
    storage.delete_user(env.user.id)
    assert storage.list_upload_jobs(env.user.id, limit=10) == []


def replace_id(job, new_id):
    from dataclasses import replace

    return replace(job, id=new_id)


def test_sync_settings_from_env():
    from steamlink.config import ConfigError, load_settings

    assert load_settings({}).sync_max_matches_per_request == 3 and load_settings({}).sync_job_max_attempts == 5
    assert load_settings({"SYNC_JOB_MAX_ATTEMPTS": "2"}).sync_job_max_attempts == 2
    base = dict(DATABASE_URL="sqlite://", TOKEN_ENCRYPTION_KEYS="k", SESSION_SECRET="s" * 40,
                PUBLIC_API_URL="https://api.example.com", FRONTEND_URL="https://example.com", STEAM_WEB_API_KEY="x")
    with pytest.raises(ConfigError):
        load_settings({**base, "SYNC_JOB_MAX_ATTEMPTS": "0"})
