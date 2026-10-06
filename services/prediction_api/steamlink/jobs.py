"""Background demo jobs: manual uploads and Steam sync downloads.

Two kinds of ``upload_jobs`` rows, one worker:

* ``upload``: ``POST /matches/upload`` only receives the file: it writes the
  body to ``<job dir>/<job id>.upload``, records a job and returns 202.
* ``steam_sync``: ``POST /steam/sync`` walks the share-code history and queues
  one job per new match (see :mod:`steamlink.sync`). The worker first locates
  and downloads the demo to ``<job dir>/<job id>.upload``.

Then the worker decompresses, hashes, dedupes, parses (in the parse child
process, holding the parse slot) and stores the match, updating the job's
stage as it goes. Clients poll ``GET /matches/upload/{job_id}`` (any kind) or
``GET /steam/sync`` (the user's sync jobs).

Why: on Render's free plan (0.1 CPU) a full-length demo takes ~15-50 s and a
260 MB ``.dem.bz2`` ~6 minutes (bzip2 decompression alone is ~35 CPU-seconds),
too long to hold one HTTP request open.

Worker model: one thread per process, started on demand (``start()`` after an
upload is queued, at app startup, and when a client polls a queued job). It
works through queued jobs oldest first and exits when none are left, so idle
processes have no polling thread. The first run in a process also recovers
jobs a previous process left behind:

* ``processing`` uploads were interrupted (crash, deploy, Render restart):
  queued again if their file still exists and they haven't been tried twice
  already, else failed (``server_restarted`` / ``demo_parse_failed``);
* ``queued`` uploads whose file is gone (Render's disk is ephemeral) fail with
  ``server_restarted`` (the user uploads again);
* sync jobs don't need a surviving file (the demo is downloaded again): an
  interrupted one is queued again (partial download removed) unless it has
  already run ``SyncService.max_job_attempts`` times, then a ``parse_failed``
  stub match is stored; queued ones simply stay queued;
* finished jobs older than ``UPLOAD_JOB_RETENTION_SECONDS`` (default one week)
  are deleted, and stray files in the job directory that no active job owns
  are removed (also after each queue drain, so a long-lived process still
  frees disk without a restart). Demo files and workdirs are removed as soon
  as a job finishes — success or failure (e.g. ``demo_parse_failed``).

Assumes ONE API process per job directory (Render: one instance, one uvicorn
worker); with several processes, recovery in one would requeue another's
running job.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import timedelta

from .storage.base import JOB_KIND_SYNC
from .sync import SyncJobFailed
from .upload import UploadRejected, import_uploaded_demo

logger = logging.getLogger(__name__)

JOB_FILE_SUFFIX = ".upload"
JOB_WORKDIR_SUFFIX = ".work"


def default_job_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "csa-upload-jobs")


class UploadJobWorker:
    """``ctx``: the API's SteamContext (storage, settings, sync.parser, clock),
    read at use time so tests can swap the parser or settings."""

    def __init__(self, ctx, *, max_attempts: int = 2, retention: timedelta | None = None,
                 db_retry_seconds: float = 2.0, db_retries: int = 5):
        self._ctx = ctx
        self.max_attempts = max_attempts
        # Prefer Settings.upload_job_retention_seconds when retention is not passed (tests pass it).
        if retention is None:
            seconds = getattr(getattr(ctx, "settings", None), "upload_job_retention_seconds", None)
            retention = timedelta(seconds=seconds) if seconds is not None else timedelta(days=7)
        self.retention = retention
        self.db_retry_seconds = db_retry_seconds
        self.db_retries = db_retries
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._recovered = False
        self._last_cleanup_at = 0.0  # monotonic; cleanup_orphans throttled while draining

    # Paths -------------------------------------------------------------------
    @property
    def job_dir(self) -> str:
        path = self._ctx.settings.upload_job_dir or default_job_dir()
        os.makedirs(path, exist_ok=True)
        return path

    def job_file(self, job_id: str) -> str:
        return os.path.join(self.job_dir, job_id + JOB_FILE_SUFFIX)

    # Thread lifecycle ----------------------------------------------------------
    def start(self) -> None:
        """Make sure a worker thread is running (no-op if one is)."""

        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="csa-upload-jobs", daemon=True)
                self._thread.start()

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until the worker thread has exited (tests, shutdown)."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return True
            thread.join(min(0.05, max(0.0, deadline - time.monotonic())))
        return False

    def _run(self) -> None:
        failures = 0
        while True:
            try:
                if not self._recovered:
                    self.recover()
                    self._recovered = True
                idle = False
                with self._lock:
                    job = self._ctx.storage.claim_next_upload_job(self._ctx.clock())
                    if job is None:
                        # Exit under the lock: a concurrent start() either sees this thread
                        # alive before the claim (and its job is claimed here) or starts anew.
                        self._thread = None
                        idle = True
                if idle:
                    # Outside the lock: prune finished rows + stray files so a long-lived
                    # process that rarely restarts still frees disk after each drain.
                    self.cleanup_orphans()
                    return
                failures = 0
            except Exception:  # database unavailable etc.: retry a few times, then stop
                failures += 1
                logger.exception("upload job worker: storage error (%s/%s)", failures, self.db_retries)
                if failures >= self.db_retries:
                    with self._lock:
                        self._thread = None
                    return
                time.sleep(self.db_retry_seconds)
                continue
            self.process(job)

    # Recovery --------------------------------------------------------------------
    def recover(self) -> None:
        storage, now = self._ctx.storage, self._ctx.clock()
        for job in storage.list_active_upload_jobs():
            if job.kind == JOB_KIND_SYNC:
                self._recover_sync_job(job, now)
            elif not os.path.exists(job.demo_path):
                logger.warning("upload job %s: file gone after restart", job.id)
                storage.update_upload_job(job.id, now, status="failed", stage=None, progress=None,
                                          error="server_restarted", finished_at=now)
                self._remove_files(job)  # leftover .work from a crashed parse
            elif job.status == "processing":
                if job.attempts >= self.max_attempts:
                    # The process died while working on this demo more than once: don't loop on it.
                    storage.update_upload_job(job.id, now, status="failed", stage=None, progress=None,
                                              error="demo_parse_failed", finished_at=now)
                    self._remove_files(job)
                else:
                    storage.update_upload_job(job.id, now, status="queued", stage=None, progress=None)
        self.cleanup_orphans(force=True)

    def _recover_sync_job(self, job, now) -> None:
        if job.status != "processing":
            return  # queued: nothing on disk to lose
        self._remove_files(job)  # partial download
        storage = self._ctx.storage
        if job.attempts >= self._ctx.sync.max_job_attempts:
            logger.warning("sync job %s: interrupted %s times, giving up", job.id, job.attempts)
            try:
                stub = self._ctx.sync.record_stub(job, "parse_failed", "parser_error")
            except Exception:  # e.g. the user was deleted meanwhile
                logger.exception("sync job %s: could not store the stub match", job.id)
                stub = None
            storage.update_upload_job(job.id, now, status="done" if stub else "failed", stage=None, progress=None,
                                      error=None if stub else "demo_parse_failed",
                                      match_id=stub.match_id if stub else None,
                                      match_created=stub.created if stub else None, finished_at=now)
        else:
            storage.update_upload_job(job.id, now, status="queued", stage=None, progress=None)

    # One job -----------------------------------------------------------------------
    def process(self, job) -> None:
        ctx = self._ctx
        storage, settings = ctx.storage, ctx.settings
        workdir = os.path.join(os.path.dirname(job.demo_path), job.id + JOB_WORKDIR_SUFFIX)
        shutil.rmtree(workdir, ignore_errors=True)
        started = time.monotonic()

        def on_stage(stage: str, progress: float | None = None) -> None:
            storage.update_upload_job(job.id, ctx.clock(), stage=stage, progress=progress)

        try:
            os.makedirs(workdir)
            if job.kind == JOB_KIND_SYNC:
                result = ctx.sync.import_job(
                    job, workdir=workdir, max_compressed_bytes=settings.demo_max_download_bytes,
                    max_demo_bytes=settings.demo_max_decompressed_bytes, on_stage=on_stage,
                )
            else:
                result = import_uploaded_demo(
                    storage=storage, parser=ctx.sync.parser, user_id=job.user_id, raw_path=job.demo_path,
                    workdir=workdir, max_compressed_bytes=settings.demo_max_download_bytes,
                    max_demo_bytes=settings.demo_max_decompressed_bytes, now=ctx.clock(),
                    share_code=job.share_code, demo_sha256=job.demo_sha256, wait_for_parse_slot=True,
                    on_stage=on_stage,
                )
        except (UploadRejected, SyncJobFailed) as exc:
            logger.info("%s job %s failed: %s", job.kind, job.id, exc.reason)
            self._finish(job, status="failed", error=exc.reason)
        except Exception:
            logger.exception("%s job %s crashed", job.kind, job.id)
            self._finish(job, status="failed", error="internal_error")
        else:
            logger.info("%s job %s done in %.1fs (match %s, created=%s)", job.kind, job.id,
                        time.monotonic() - started, result.match_id, result.created)
            # match_created set at queue time: the upload request already added the (outdated,
            # re-parsed here) match to the user's list.
            self._finish(job, status="done", match_id=result.match_id,
                         match_created=result.created or bool(job.match_created), match_updated=result.updated)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            self._remove_files(job)

    def _finish(self, job, *, status: str, error: str | None = None, match_id: str | None = None,
                match_created: bool | None = None, match_updated: bool | None = None) -> None:
        now = self._ctx.clock()
        try:
            self._ctx.storage.update_upload_job(job.id, now, status=status, stage=None, progress=None, error=error,
                                                match_id=match_id, match_created=match_created,
                                                match_updated=match_updated, finished_at=now)
        except Exception:  # e.g. the database went away; recovery fixes the row on the next start
            logger.exception("upload job %s: could not record the result", job.id)

    def cleanup_orphans(self, *, force: bool = False) -> None:
        """Delete finished job rows past retention and any ``.upload`` / ``.work`` files
        no active job owns.

        Safe to call while the worker is idle or between jobs. Demo bytes for a
        finished job are already removed in :meth:`process`'s ``finally`` (success
        and failure); this catches leftovers after a crash/OOM and prunes DB rows.
        Throttled by ``Settings.upload_job_cleanup_interval_seconds`` unless
        ``force`` (startup recovery).
        """

        settings = getattr(self._ctx, "settings", None)
        interval = float(getattr(settings, "upload_job_cleanup_interval_seconds", 300) or 300)
        now_mono = time.monotonic()
        if not force and (now_mono - self._last_cleanup_at) < interval:
            return
        self._last_cleanup_at = now_mono
        storage, now = self._ctx.storage, self._ctx.clock()
        try:
            deleted = storage.delete_finished_upload_jobs(now - self.retention)
            if deleted:
                logger.info("upload jobs: pruned %s finished row(s) older than %s", deleted, self.retention)
            owned = {os.path.basename(job.demo_path) for job in storage.list_active_upload_jobs()}
        except Exception:
            logger.exception("upload jobs: cleanup could not list/prune job rows")
            return
        job_dir = self.job_dir
        try:
            names = os.listdir(job_dir)
        except OSError:
            return
        for name in names:
            path = os.path.join(job_dir, name)
            if name.endswith(JOB_WORKDIR_SUFFIX):
                # Workdirs are only live inside process(). On force (startup recovery) every
                # ``.work`` is stale — even if the job was requeued and still owns its
                # ``.upload``. Otherwise keep a workdir only when its ``.upload`` is owned
                # (a job mid-process); idle drains have no such jobs.
                if not force:
                    job_id = name[: -len(JOB_WORKDIR_SUFFIX)]
                    if (job_id + JOB_FILE_SUFFIX) in owned:
                        continue
                shutil.rmtree(path, ignore_errors=True)
            elif name.endswith(JOB_FILE_SUFFIX) and name not in owned:
                try:
                    os.remove(path)
                except OSError:
                    pass

    @staticmethod
    def _remove_files(job) -> None:
        """Drop the job's demo file and workdir (success, failure, or recovery give-up)."""

        try:
            os.remove(job.demo_path)
        except OSError:
            pass
        workdir = os.path.join(os.path.dirname(job.demo_path), job.id + JOB_WORKDIR_SUFFIX)
        shutil.rmtree(workdir, ignore_errors=True)
