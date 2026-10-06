"""Background parse jobs for uploads: queue limits, queue position, crashes, restart recovery, storage."""

import os
import time
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import FastAPI

from steamlink import upload
from steamlink.api import start_background_work
from steamlink.jobs import UploadJobWorker
from steamlink.storage.base import UploadJob

from fakes import FakeParser
from test_api_steam import OCTET, login, make_client, upload_and_wait

DEMO = b"PBDEMS2\0" + b"\x02" * 2048


@pytest.fixture()
def env(tmp_path):
    client, ctx = make_client(tmp_path)
    login(client, ctx)
    ctx.sync.parser = FakeParser()
    return client, ctx


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def job_status(client, job_id):
    return client.get(f"/matches/upload/{job_id}").json()["job"]


def test_queue_limit_and_queue_position(env):
    client, ctx = env
    ctx.settings = replace(ctx.settings, upload_queue_max=2)
    assert upload._parse_slot.acquire(blocking=False)  # hold the worker at "parsing"
    try:
        first = client.post("/matches/upload", content=DEMO, headers=OCTET).json()["job"]
        wait_for(lambda: job_status(client, first["id"])["stage"] == "parsing")
        ctx.clock.advance(1)
        second = client.post("/matches/upload", content=DEMO + b"2", headers=OCTET)
        assert second.status_code == 202
        second = second.json()["job"]
        assert second["status"] == "queued" and second["queue_position"] == 1  # behind the running job
        assert job_status(client, first["id"])["status"] == "processing"
        full = client.post("/matches/upload", content=DEMO + b"3", headers=OCTET)
        assert full.status_code == 429 and full.json()["detail"] == "upload_queue_full"
        assert len(os.listdir(ctx.jobs.job_dir)) == 2 + 1  # two job files + the running job's work dir
    finally:
        upload._parse_slot.release()
    assert ctx.jobs.wait_idle(10)
    assert [job_status(client, j["id"])["status"] for j in (first, second)] == ["done", "done"]
    assert os.listdir(ctx.jobs.job_dir) == []
    assert upload_and_wait(client, ctx, DEMO + b"3")[1]["status"] == "done"  # room again


def test_a_crashing_job_fails_alone_and_the_queue_continues(env):
    client, ctx = env
    ctx.sync.parser = FakeParser(fail_after_calls=0)  # RuntimeError on every parse
    assert upload._parse_slot.acquire(blocking=False)
    try:
        bad = client.post("/matches/upload", content=DEMO, headers=OCTET).json()["job"]
        # The worker picked its parser when the job started; later jobs use the working one.
        wait_for(lambda: job_status(client, bad["id"])["stage"] == "parsing")
        ctx.sync.parser = FakeParser()
        ctx.clock.advance(1)
        good = client.post("/matches/upload", content=DEMO + b"ok", headers=OCTET).json()["job"]
    finally:
        upload._parse_slot.release()
    assert ctx.jobs.wait_idle(10)
    failed = job_status(client, bad["id"])
    assert failed["status"] == "failed" and failed["error"] == "internal_error"
    assert job_status(client, good["id"])["status"] == "done"
    assert os.listdir(ctx.jobs.job_dir) == []


def test_unexpected_errors_are_internal_error(env):
    client, ctx = env
    ctx.sync.parser = FakeParser(fail_after_calls=0)
    response, job = upload_and_wait(client, ctx, DEMO)
    assert response.status_code == 202 and job["status"] == "failed" and job["error"] == "internal_error"
    ctx.sync.parser = FakeParser()
    assert upload_and_wait(client, ctx, DEMO)[1]["status"] == "done"  # the worker is still usable


def test_jobs_are_private(env, tmp_path):
    client, ctx = env
    _, job = upload_and_wait(client, ctx, DEMO)
    other = ctx.storage.get_or_create_user("76561198000000002", ctx.clock())
    assert ctx.storage.get_upload_job(other.id, job["id"]) is None
    assert ctx.storage.list_upload_jobs(other.id, limit=10) == []
    response = client.get("/matches/upload/not-a-job")
    assert response.status_code == 404 and response.json()["detail"] == "upload_job_not_found"


def _job(ctx, user_id, job_id, *, status, path, attempts=0, age=timedelta(0)):
    now = ctx.clock() - age
    job = UploadJob(id=job_id, user_id=user_id, status=status, demo_path=path, size_bytes=1, created_at=now,
                    updated_at=now, attempts=attempts)
    assert ctx.storage.create_upload_job(job)
    return job


def test_restart_recovery(env):
    """A new process (new worker) resumes / fails what the previous process left behind."""

    client, ctx = env
    me = client.get("/me").json()
    user = ctx.storage.get_or_create_user(me["steam_id"], ctx.clock())
    job_dir = ctx.jobs.job_dir

    def demo_file(name, data=DEMO):
        path = os.path.join(job_dir, name + ".upload")
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    interrupted = _job(ctx, user.id, "interrupted", status="processing", path=demo_file("interrupted"), attempts=1)
    crashloop = _job(ctx, user.id, "crashloop", status="processing", path=demo_file("crashloop", DEMO + b"c"),
                     attempts=2)
    lost = _job(ctx, user.id, "lost", status="queued", path=os.path.join(job_dir, "lost.upload"))
    waiting = _job(ctx, user.id, "waiting", status="queued", path=demo_file("waiting", DEMO + b"w"))
    old = _job(ctx, user.id, "old", status="done", path=os.path.join(job_dir, "old.upload"), age=timedelta(days=8))
    stray = demo_file("stray")
    os.makedirs(os.path.join(job_dir, "interrupted.work"))  # partial work of the interrupted job
    del old, stray

    restarted = replace(ctx)  # same storage/settings, fresh worker (= a new process)
    restarted.jobs = UploadJobWorker(restarted)
    app = FastAPI()
    app.state.steam = restarted
    start_background_work(app)  # what main.py's lifespan does at startup
    assert restarted.jobs.wait_idle(10)

    def status(job):
        found = ctx.storage.get_upload_job(user.id, job.id)
        return found and (found.status, found.error, found.attempts)

    assert status(interrupted) == ("done", None, 2)  # resumed
    assert status(crashloop) == ("failed", "demo_parse_failed", 2)  # not retried a third time
    assert status(lost) == ("failed", "server_restarted", 0)  # file gone (ephemeral disk)
    assert status(waiting)[0] == "done"
    assert ctx.storage.get_upload_job(user.id, "old") is None  # pruned after a week
    assert os.listdir(job_dir) == []  # stray file, work dir and finished jobs' files removed
    assert ctx.sync.parser.calls == 2


def test_status_poll_restarts_a_stopped_worker(env):
    client, ctx = env
    me = client.get("/me").json()
    user = ctx.storage.get_or_create_user(me["steam_id"], ctx.clock())
    path = ctx.jobs.job_file("orphan")
    with open(path, "wb") as fh:
        fh.write(DEMO)
    _job(ctx, user.id, "orphan", status="queued", path=path)  # e.g. the worker gave up during a DB outage
    assert not ctx.jobs.running
    client.get("/matches/upload/orphan")
    assert ctx.jobs.wait_idle(10)
    assert job_status(client, "orphan")["status"] == "done"


def test_storage_claims_each_job_once_and_deletes_with_user(env):
    client, ctx = env
    storage = ctx.storage
    user = storage.get_or_create_user("76561198000000003", ctx.clock())
    a = _job(ctx, user.id, "a", status="queued", path="/nonexistent/a")
    ctx.clock.advance(1)
    _job(ctx, user.id, "b", status="queued", path="/nonexistent/b")
    assert storage.create_upload_job(replace(a, id="c"), max_active=2) is False  # two active already
    assert storage.claim_next_upload_job(ctx.clock()).id == "a"
    claimed = storage.claim_next_upload_job(ctx.clock())
    assert claimed.id == "b" and claimed.status == "processing" and claimed.attempts == 1
    assert storage.claim_next_upload_job(ctx.clock()) is None
    storage.update_upload_job("b", ctx.clock(), status="done", match_id="m", match_created=True)
    assert storage.get_upload_job(user.id, "b").match_created is True
    with pytest.raises(ValueError):
        storage.update_upload_job("b", ctx.clock(), user_id="someone-else")
    storage.delete_user(user.id)
    assert storage.list_upload_jobs(user.id, limit=10) == []


def test_queue_settings_from_env():
    from steamlink.config import ConfigError, load_settings

    defaults = load_settings({})
    assert defaults.upload_queue_max == 3 and defaults.upload_job_dir is None
    assert defaults.upload_job_retention_seconds == 7 * 24 * 3600
    assert defaults.upload_job_cleanup_interval_seconds == 300
    loaded = load_settings({"UPLOAD_QUEUE_MAX": "5", "UPLOAD_JOB_DIR": "/var/tmp/jobs",
                            "UPLOAD_JOB_RETENTION_SECONDS": "3600",
                            "UPLOAD_JOB_CLEANUP_INTERVAL_SECONDS": "0"})
    assert loaded.upload_queue_max == 5 and loaded.upload_job_dir == "/var/tmp/jobs"
    assert loaded.upload_job_retention_seconds == 3600
    assert loaded.upload_job_cleanup_interval_seconds == 0
    base = dict(DATABASE_URL="sqlite://", TOKEN_ENCRYPTION_KEYS="k", SESSION_SECRET="s" * 40,
                PUBLIC_API_URL="https://api.example.com", FRONTEND_URL="https://example.com", STEAM_WEB_API_KEY="x")
    with pytest.raises(ConfigError):
        load_settings({**base, "UPLOAD_QUEUE_MAX": "0"})
    with pytest.raises(ConfigError):
        load_settings({**base, "UPLOAD_JOB_RETENTION_SECONDS": "30"})


def test_failed_parse_deletes_demo_and_workdir(env):
    """Parse failures (MalformedMessage → demo_parse_failed) must not leave files on disk."""

    from steamlink.demo_parser import DemoParseError

    client, ctx = env

    class BoomParser:
        calls = 0

        def parse(self, path):
            self.calls += 1
            raise DemoParseError("demo could not be parsed")

    ctx.sync.parser = BoomParser()
    response, job = upload_and_wait(client, ctx, DEMO)
    assert response.status_code == 202
    assert job["status"] == "failed" and job["error"] == "demo_parse_failed"
    assert os.listdir(ctx.jobs.job_dir) == []


def test_cleanup_orphans_removes_stray_files_and_expired_rows(env):
    client, ctx = env
    me = client.get("/me").json()
    user = ctx.storage.get_or_create_user(me["steam_id"], ctx.clock())
    job_dir = ctx.jobs.job_dir
    stray = os.path.join(job_dir, "stray.upload")
    with open(stray, "wb") as fh:
        fh.write(DEMO)
    os.makedirs(os.path.join(job_dir, "stray.work"))
    _job(ctx, user.id, "oldfail", status="failed", path=os.path.join(job_dir, "oldfail.upload"),
         age=timedelta(days=8))
    # Short retention + no throttle so one cleanup call does both jobs.
    ctx.jobs = UploadJobWorker(ctx, retention=timedelta(days=7))
    ctx.jobs._last_cleanup_at = 0.0
    ctx.settings = replace(ctx.settings, upload_job_cleanup_interval_seconds=0)
    ctx.jobs.cleanup_orphans(force=True)
    assert ctx.storage.get_upload_job(user.id, "oldfail") is None
    assert os.listdir(job_dir) == []
