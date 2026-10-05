"""Automatic background match sync (Leetify-style): linked users get new matches
pulled without pressing Sync.

A scheduler thread in the API process (started with the upload / sync job worker,
see ``steamlink.api.start_background_work``) wakes every ``AUTO_SYNC_TICK_SECONDS``
(with jitter) and runs :meth:`AutoSyncScheduler.run_tick`:

1. Lease: only the holder of the ``auto_sync`` row in ``scheduler_leases`` runs a
   tick (renewed by its holder, taken over when it expires), so with several API
   processes / instances the per-tick user cap stays global. SQLite deployments are
   single-process; the same code runs there.
2. Candidates (``Storage.list_auto_sync_candidates``): match history linked, auto
   sync not turned off by the user, no sync running, not waiting for a re-link
   (``steamlink.sync.RELINK_ERRORS``) and due: ``next_auto_sync_at`` passed, or
   never scheduled and the last sync (manual or automatic) is older than
   ``AUTO_SYNC_INTERVAL_SECONDS``. At most ``AUTO_SYNC_MAX_USERS_PER_TICK``.
3. Per user, a compare-and-set claim on ``next_auto_sync_at`` (``claim_auto_sync``)
   so two schedulers never sync the same user, then exactly the Sync button's code
   path (:meth:`steamlink.sync.SyncService.sync`, with its per-user sync lock): new
   matches become background jobs for :mod:`steamlink.jobs`.
4. Schedule (:meth:`AutoSyncScheduler.after_sync`, also called after a manual sync):

   * up to date -> next in one interval (plus up to 10% jitter), failures reset;
   * more history (``partial``) -> again soon (``followup_seconds``);
   * job queue full -> again soon, and the tick stops (no room for more jobs);
   * Valve rate limit (429) -> per-user backoff and the tick stops for everyone;
   * re-link needed -> recorded; skipped until the user enters new codes;
   * other errors / crashes -> exponential backoff: interval * 2^failures, capped at
     ``AUTO_SYNC_MAX_BACKOFF_SECONDS``.

Auto sync leaves one job-queue slot free (``max_active = UPLOAD_QUEUE_MAX - 1``)
so a user's upload is not refused because of background syncs, and it does nothing
while demo retrieval (the Steam demo bot) is not configured: sync could only fail
with ``demo_retrieval_not_configured`` then.

On Render's free plan the instance sleeps after ~15 minutes without traffic; the
scheduler only runs while the instance is awake (it catches up when it wakes).
"""

from __future__ import annotations

import logging
import os
import random
import secrets
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .storage.base import User
from .sync import RELINK_ERRORS, SyncOutcome, SyncRejected

logger = logging.getLogger(__name__)

LEASE_NAME = "auto_sync"
JITTER_FRACTION = 0.1


@dataclass
class TickResult:
    # ran | disabled | not_configured | not_leader | queue_busy
    status: str
    synced: list[str] = field(default_factory=list)  # user ids synced this tick
    stopped: str | None = None  # why the tick stopped early: rate_limited | queue_full
    queued: int = 0  # jobs queued for the worker


class AutoSyncScheduler:
    """``ctx``: the API's SteamContext (storage, settings, sync, jobs, clock), read at
    use time like :class:`steamlink.jobs.UploadJobWorker`. ``rng``: jitter source (tests)."""

    def __init__(self, ctx, *, rng: random.Random | None = None):
        self._ctx = ctx
        self._rng = rng or random.Random()
        self.holder = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # Settings ------------------------------------------------------------------
    @property
    def interval_seconds(self) -> int:
        return self._ctx.settings.auto_sync_interval_seconds

    @property
    def enabled(self) -> bool:
        """Auto sync is on for this deployment (users can still turn it off for themselves)."""

        return self.interval_seconds > 0

    @property
    def available(self) -> bool:
        """It can actually import matches: enabled and demo retrieval configured."""

        return self.enabled and bool(getattr(self._ctx.sync.locator, "configured", True))

    @property
    def followup_seconds(self) -> int:
        return max(60, self._ctx.sync.min_interval_seconds)

    def max_active(self) -> int:
        return max(1, self._ctx.settings.upload_queue_max - 1)

    # Thread ---------------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="csa-auto-sync", daemon=True)
                self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        try:
            self._ctx.storage.release_lease(LEASE_NAME, self.holder)
        except Exception:  # database gone at shutdown: the lease simply expires
            logger.debug("auto sync: could not release the lease", exc_info=True)

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _tick_delay(self) -> float:
        tick = self._ctx.settings.auto_sync_tick_seconds
        return tick * self._rng.uniform(1 - JITTER_FRACTION, 1 + JITTER_FRACTION)

    def _loop(self) -> None:
        # First tick after a short random delay: instances starting together don't collide.
        delay = min(30.0, self._ctx.settings.auto_sync_tick_seconds) * self._rng.uniform(0.5, 1.0)
        while not self._stop.wait(delay):
            try:
                result = self.run_tick()
                if result.synced:
                    logger.info("auto sync: synced %s users, queued %s jobs%s", len(result.synced), result.queued,
                                f" (stopped: {result.stopped})" if result.stopped else "")
            except Exception:  # database unavailable etc.: try again next tick
                logger.exception("auto sync tick failed")
            delay = self._tick_delay()

    # One tick ---------------------------------------------------------------------
    def run_tick(self) -> TickResult:
        ctx = self._ctx
        if not self.enabled:
            return TickResult("disabled")
        if not self.available:
            return TickResult("not_configured")
        storage, settings = ctx.storage, ctx.settings
        now = ctx.clock()
        lease_ttl = max(2 * settings.auto_sync_tick_seconds, 120)
        if not storage.try_acquire_lease(LEASE_NAME, self.holder, now, lease_ttl):
            return TickResult("not_leader")
        max_active = self.max_active()
        if len(storage.list_active_upload_jobs()) >= max_active:
            return TickResult("queue_busy")
        candidates = storage.list_auto_sync_candidates(
            now, last_finished_before=now - timedelta(seconds=self.interval_seconds),
            limit=settings.auto_sync_max_users_per_tick, relink_errors=tuple(RELINK_ERRORS),
        )
        result = TickResult("ran")
        hold = timedelta(seconds=max(ctx.sync.lock_ttl_seconds, self.followup_seconds))
        for user, seen in candidates:
            if self._stop.is_set():
                break
            if not storage.claim_auto_sync(user.id, seen=seen, hold_until=now + hold, now=ctx.clock()):
                continue  # another scheduler or a manual sync got there first
            outcome = self._sync_one(user, max_active)
            result.synced.append(user.id)
            if outcome is not None:
                result.queued += outcome.queued
                if outcome.status == "queue_full":
                    result.stopped = "queue_full"
                    break
                if outcome.error == "rate_limited":
                    result.stopped = "rate_limited"  # Valve's limit is per API key: back off for everyone
                    break
        if result.queued:
            ctx.jobs.start()
        return result

    def _sync_one(self, user: User, max_active: int) -> SyncOutcome | None:
        ctx = self._ctx
        try:
            outcome = ctx.sync.sync(user, max_active=max_active, job_file=ctx.jobs.job_file)
        except SyncRejected as exc:
            self.after_rejected(user.id, exc.reason)
            return None
        except Exception:
            logger.exception("auto sync failed for user %s", user.id)  # no codes in this message
            self.after_sync(user.id, SyncOutcome("error", error="internal_error"), automatic=True)
            return SyncOutcome("error", error="internal_error")
        self.after_sync(user.id, outcome, automatic=True)
        return outcome

    # Scheduling -------------------------------------------------------------------
    def _jittered(self, seconds: float) -> timedelta:
        return timedelta(seconds=seconds + self._rng.uniform(0, JITTER_FRACTION * seconds))

    def backoff_seconds(self, failures: int) -> float:
        """Delay after ``failures`` consecutive failures (1 -> 2 intervals, 2 -> 4, ...)."""

        cap = self._ctx.settings.auto_sync_max_backoff_seconds
        return float(min(cap, self.interval_seconds * (2 ** min(failures, 20))))

    def after_sync(self, user_id: str, outcome: SyncOutcome, *, automatic: bool) -> None:
        """Schedule the next automatic sync after a sync finished (automatic or the button)."""

        if not self.enabled:
            return
        ctx = self._ctx
        now = ctx.clock()
        failures = ctx.storage.get_sync_state(user_id, now).auto_sync_failures
        if outcome.status == "error" and outcome.error not in RELINK_ERRORS:
            failures += 1
            delay = self.backoff_seconds(failures)
        elif outcome.status in ("partial", "queue_full"):
            failures = 0 if outcome.status == "partial" else failures
            delay = self.followup_seconds
        else:  # up to date, or waiting for a re-link (skipped until then; interval afterwards)
            failures = 0 if outcome.status != "error" else failures
            delay = self.interval_seconds
        ctx.storage.record_auto_sync_schedule(
            user_id, next_at=now + self._jittered(delay), failures=failures,
            ran_at=now if automatic else None, error=outcome.error if outcome.status == "error" else None,
        )

    def after_rejected(self, user_id: str, reason: str) -> None:
        """The sync did not start (already running / too soon / unlinked meanwhile): try later."""

        ctx = self._ctx
        now = ctx.clock()
        state = ctx.storage.get_sync_state(user_id, now)
        delay = self.followup_seconds if reason in ("already_running", "too_soon") else self.interval_seconds
        ctx.storage.record_auto_sync_schedule(user_id, next_at=now + self._jittered(delay),
                                              failures=state.auto_sync_failures)

    # View ---------------------------------------------------------------------------
    def next_at(self, user: User, state) -> datetime | None:
        """When the scheduler will (at the earliest) sync this user; None if it won't."""

        if not self.available or not user.auto_sync_enabled:
            return None
        if state.next_auto_sync_at is not None:
            return state.next_auto_sync_at
        if state.last_finished_at is None:
            return self._ctx.clock()
        return state.last_finished_at + timedelta(seconds=self.interval_seconds)
