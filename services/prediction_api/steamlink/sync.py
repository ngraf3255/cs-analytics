"""User-triggered, bounded, idempotent match sync.

``POST /steam/sync`` (:meth:`SyncService.sync`) only talks to Valve's
match-history API, so it answers in about a second: it walks the share-code
chain from the user's cursor and, for each new match, either

* skips it because it is already stored (upload with its share code, or an
  earlier sync): cursor advanced, nothing downloaded; or
* queues a ``steam_sync`` background job (``upload_jobs`` table, see
  :mod:`steamlink.jobs`) and advances the cursor in the same transaction.

The job worker then calls :meth:`SyncService.import_job`: locate the demo
(Game Coordinator), download it, decompress, hash, dedupe, parse (holding the
parse slot shared with uploads) and store it. Why: on Render's free plan
(0.1 CPU) one full-length demo takes ~1 minute and a ``.dem.bz2`` several
minutes, far too long for one request.

Once a match is queued the job owns it: transient failures (demo not ready on
Valve's side yet, demo bot can't sign in, unexpected error) leave the job
``failed`` with that error and the next sync queues it again, up to
``max_job_attempts`` runs. Permanent failures (demo expired, too large,
unparseable, or still not ready after the last attempt) store a stub match
(``unavailable`` / ``parse_failed``) that a manual upload can fill in later.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from .crypto import AuthCodeCipher, DecryptionError
from .demo_parser import DemoParser
from .gc import DemoBotAuthFailed
from .sharecode import decode
from .storage.base import JOB_KIND_SYNC, CursorConflict, NewMatch, Storage, UploadJob, User
from .upload import UploadRejected, UploadResult, import_uploaded_demo
from .valve import (
    DemoFetcher,
    DemoLocator,
    DemoLocatorNotConfigured,
    DemoNotReady,
    DemoTooLarge,
    DemoUnavailable,
    MatchHistoryClient,
)

log = logging.getLogger(__name__)

# Job errors after which the next sync queues the job again (bounded by max_job_attempts).
RETRYABLE_JOB_ERRORS = ("demo_not_ready", "demo_bot_auth_failed", "demo_retrieval_not_configured", "internal_error")

# Sync errors only new codes can fix -> which code the user has to replace. Shown as the
# API's needs_relink; the automatic sync (steamlink.autosync) skips such users until re-linked.
RELINK_ERRORS = {
    "invalid_auth_code": "auth_code",  # revoked on Valve's page or mistyped
    "credentials_unreadable": "auth_code",  # encryption key rotated away
    "invalid_known_code": "share_code",  # the cursor is too old (> ~30 days) or no longer valid
}


def relink_needed(state, access) -> dict | None:
    """``{"reason", "field"}`` when the last sync (``SyncState``) failed in a way only new
    codes fix and the codes (``MatchAccess``) were not updated since; else None."""

    field_ = RELINK_ERRORS.get(state.last_error or "")
    if field_ is None or state.locked or state.last_finished_at is None:
        return None
    if access.updated_at is not None and access.updated_at > state.last_finished_at:
        return None  # re-linked after that sync
    return {"reason": state.last_error, "field": field_}


# Import pipeline rejections -> the stub match a sync job stores instead (status, status_reason).
_STUB_FOR_REJECTION = {
    "demo_too_large": ("unavailable", "demo_too_large"),
    "not_a_cs2_demo": ("unavailable", "demo_unavailable"),
    "demo_parse_failed": ("parse_failed", "parser_error"),
    "demo_has_no_rounds": ("parse_failed", "demo_has_no_rounds"),
}


@dataclass(frozen=True)
class SyncOutcome:
    # up_to_date | partial | queue_full | error
    status: str
    queued: int = 0  # matches newly queued for download + parse by this request
    skipped: int = 0  # new share codes already in the user's list (no download)
    # new share codes already imported (e.g. by another player in the match): added to
    # the user's list right away, no download or parse
    attached: int = 0
    has_more: bool = False
    error: str | None = None
    # Jobs this request queued (new, re-queued after a transient failure, or already queued).
    job_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def processed(self) -> int:
        return self.queued + self.skipped + self.attached


class SyncRejected(Exception):
    """Sync could not start: not_linked | needs_share_code | already_running | too_soon."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SyncJobFailed(Exception):
    """A sync job failed transiently (``reason`` is in RETRYABLE_JOB_ERRORS); no match stored."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _played_at(match_time: int | None) -> datetime | None:
    """The Game Coordinator's match time (unix seconds) as a UTC datetime; None if unknown/implausible."""

    if not match_time or match_time < 1_300_000_000:  # before CS:GO existed: not a real match time
        return None
    try:
        return datetime.fromtimestamp(int(match_time), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _ordered_job_id() -> str:
    """Random id that sorts by creation time: the queue is ordered by (created_at, id),
    and the jobs of one sync request can share a timestamp."""

    return f"{time.time_ns():016x}{secrets.token_hex(8)}"


class SyncService:
    def __init__(
        self,
        *,
        storage: Storage,
        history: MatchHistoryClient | None,  # None: Steam off (upload-only server; sync() is never called)
        locator: DemoLocator,
        fetcher: DemoFetcher,
        parser: DemoParser,
        cipher: AuthCodeCipher | None,
        clock: Callable[[], datetime],
        max_matches: int = 3,
        lock_ttl_seconds: int = 900,
        min_interval_seconds: int = 30,
        max_job_attempts: int = 5,
        import_start_match: bool = False,
    ):
        self.storage = storage
        self.history = history
        self.locator = locator
        self.fetcher = fetcher
        self.parser = parser
        self.cipher = cipher
        self.clock = clock
        self.max_matches = max_matches
        self.lock_ttl_seconds = lock_ttl_seconds
        self.min_interval_seconds = min_interval_seconds
        self.max_job_attempts = max_job_attempts
        # Also import the match of the share code the user linked with (the cursor) when it
        # isn't in their list yet: users paste their latest match token and expect that match
        # (Leetify does the same). Off by default here; on in production (SYNC_IMPORT_START_MATCH).
        self.import_start_match = import_start_match

    # The request: walk the history, queue jobs ------------------------------------
    def sync(self, user: User, *, max_active: int, job_file: Callable[[str], str]) -> SyncOutcome:
        """``max_active``: the job queue cap (all users and kinds); ``job_file(job_id)``:
        where the worker will download that job's demo."""

        now = self.clock()
        access = self.storage.get_match_access(user.id)
        if access is None:
            raise SyncRejected("not_linked")
        if access.cursor_share_code is None:
            # Auth code saved without a share code (e.g. linked before their first match):
            # Valve's history API can only walk forward from a known code.
            raise SyncRejected("needs_share_code")
        state = self.storage.get_sync_state(user.id, now)
        if state.locked:
            raise SyncRejected("already_running")
        if state.last_finished_at and now - state.last_finished_at < timedelta(seconds=self.min_interval_seconds):
            raise SyncRejected("too_soon")
        token = secrets.token_urlsafe(16)
        if not self.storage.try_acquire_sync_lock(user.id, token, now, self.lock_ttl_seconds):
            raise SyncRejected("already_running")

        outcome = SyncOutcome("error", error="internal_error")
        try:
            outcome = self._run(user, max_active, job_file)
        except Exception:
            log.exception("sync failed for user %s", user.id)  # no codes in this message
            raise
        finally:
            self.storage.release_sync_lock(
                user.id, token, self.clock(),
                status="error" if outcome.status == "error" else "ok",
                error=outcome.error, imported=outcome.queued + outcome.attached,
            )
        return outcome

    def _run(self, user: User, max_active: int, job_file: Callable[[str], str]) -> SyncOutcome:
        job_ids = list(self.storage.requeue_sync_jobs(
            user.id, errors=RETRYABLE_JOB_ERRORS, max_attempts=self.max_job_attempts,
            max_active=max_active, now=self.clock()))
        queued = skipped = attached = 0

        def done(status: str, *, has_more: bool = False, error: str | None = None) -> SyncOutcome:
            return SyncOutcome(status, queued=queued, skipped=skipped, attached=attached, has_more=has_more,
                               error=error, job_ids=tuple(dict.fromkeys(job_ids)))

        if self.import_start_match:
            access = self.storage.get_match_access(user.id)
            # A sync job for the cursor (any state) means it was handled: imported, a stub, or
            # retried by requeue_sync_jobs above. Cursors moved by skip_known_match are 'owned'.
            if (access is not None and access.cursor_share_code is not None
                    and not self.storage.has_sync_job(user.id, access.cursor_share_code)):
                start = access.cursor_share_code
                try:
                    known = self.storage.skip_known_match(
                        user.id, expected_cursor=start, share_code=start,
                        valve_match_id=str(decode(start).match_id), now=self.clock())
                except CursorConflict:
                    return done("error", error="cursor_changed")
                if known == "added":
                    attached += 1
                elif known is None and self.locator.configured:  # not configured: tried again next sync
                    now = self.clock()
                    job_id = _ordered_job_id()
                    job = UploadJob(id=job_id, user_id=user.id, status="queued", demo_path=job_file(job_id),
                                    size_bytes=0, created_at=now, updated_at=now, share_code=start,
                                    kind=JOB_KIND_SYNC)
                    try:
                        status, queued_id = self.storage.enqueue_sync_job(
                            job, expected_cursor=start, max_active=max_active, now=now)
                    except CursorConflict:
                        return done("error", error="cursor_changed")
                    if status == "queue_full":
                        return done("queue_full", has_more=True)
                    job_ids.append(queued_id)
                    queued += status == "queued"

        for _ in range(self.max_matches):
            access = self.storage.get_match_access(user.id)
            if access is None:
                return done("error", error="not_linked")
            if access.cursor_share_code is None:  # codes replaced by an auth-only link meanwhile
                return done("error", error="needs_share_code")
            try:
                auth_code = self.cipher.decrypt(user.steam_id, access.auth_code_ciphertext)
            except DecryptionError:
                return done("error", error="credentials_unreadable")

            result = self.history.next_share_code(user.steam_id, auth_code, access.cursor_share_code)
            if result.status == "no_new_match":
                return done("up_to_date")
            if result.status != "ok" or not result.next_code:
                return done("error", error=result.status)

            share = decode(result.next_code)
            try:
                # Already stored (an earlier sync, an upload with this share code, or another
                # player's sync of the same match): don't download it again.
                known = self.storage.skip_known_match(
                    user.id, expected_cursor=access.cursor_share_code, share_code=result.next_code,
                    valve_match_id=str(share.match_id), now=self.clock(),
                )
                if known == "added":
                    attached += 1
                    continue
                if known == "owned":
                    skipped += 1
                    continue
            except CursorConflict:
                return done("error", error="cursor_changed")
            if not self.locator.configured:
                return done("error", error="demo_retrieval_not_configured")  # cursor stays put

            now = self.clock()
            job_id = _ordered_job_id()
            job = UploadJob(id=job_id, user_id=user.id, status="queued", demo_path=job_file(job_id), size_bytes=0,
                            created_at=now, updated_at=now, share_code=result.next_code, kind=JOB_KIND_SYNC)
            try:
                status, queued_id = self.storage.enqueue_sync_job(
                    job, expected_cursor=access.cursor_share_code, max_active=max_active, now=now)
            except CursorConflict:
                return done("error", error="cursor_changed")
            if status == "queue_full":
                return done("queue_full", has_more=True)
            job_ids.append(queued_id)
            queued += status == "queued"
        return done("partial", has_more=True)

    # The background job: download + parse one match ---------------------------------
    def import_job(
        self, job: UploadJob, *, workdir: str, max_compressed_bytes: int, max_demo_bytes: int,
        on_stage: Callable[[str, float | None], None],
    ) -> UploadResult:
        """Import the match of a ``steam_sync`` job. Downloads to ``job.demo_path``
        (the caller removes it and ``workdir``). Returns the stored match (possibly
        a stub); raises :class:`SyncJobFailed` for transient failures."""

        share = decode(job.share_code)
        base = dict(share_code=job.share_code, valve_match_id=str(share.match_id), source="steam_sync")
        # e.g. uploaded, or imported by another player, while the job was waiting
        known = self.storage.claim_known_match(
            job.user_id, share_code=job.share_code, valve_match_id=base["valve_match_id"], demo_sha256=None,
            share_code_verified=True, source="steam_sync", now=self.clock())
        if known is not None:
            return UploadResult(known[0].id, known[1])

        reported = [-1.0]

        def download_progress(fraction: float) -> None:
            if fraction - reported[0] >= 0.05 or fraction >= 1.0 > reported[0]:
                reported[0] = round(fraction, 2)
                on_stage("downloading", reported[0])

        try:
            on_stage("locating", None)
            info = self.locator.demo_info(share)
            on_stage("downloading", None)
            self.fetcher.download(info.url, job.demo_path, on_progress=download_progress)
        except DemoLocatorNotConfigured:
            raise SyncJobFailed("demo_retrieval_not_configured") from None
        except DemoBotAuthFailed:
            raise SyncJobFailed("demo_bot_auth_failed") from None
        except DemoNotReady:
            if job.attempts < self.max_job_attempts:
                raise SyncJobFailed("demo_not_ready") from None
            return self.record_stub(job, "unavailable", "demo_unavailable")  # gave up waiting
        except DemoUnavailable:
            return self.record_stub(job, "unavailable", "demo_unavailable")
        except DemoTooLarge:
            return self.record_stub(job, "unavailable", "demo_too_large")

        try:
            return import_uploaded_demo(
                storage=self.storage, parser=self.parser, user_id=job.user_id, raw_path=job.demo_path,
                workdir=workdir, max_compressed_bytes=max_compressed_bytes, max_demo_bytes=max_demo_bytes,
                now=self.clock(), share_code=job.share_code, wait_for_parse_slot=True, on_stage=on_stage,
                source="steam_sync", share_code_verified=True, played_at=_played_at(info.match_time),
                played_at_source="valve_gc",
            )
        except UploadRejected as exc:
            status, reason = _STUB_FOR_REJECTION.get(exc.reason, ("parse_failed", "parser_error"))
            return self.record_stub(job, status, reason)

    def record_stub(self, job: UploadJob, status: str, reason: str) -> UploadResult:
        """Store a not-imported match for this job's share code (deduped: never
        downgrades an imported match), so it shows up and an upload can fill it in."""

        share = decode(job.share_code)
        match = NewMatch(share_code=job.share_code, valve_match_id=str(share.match_id), status=status,
                         status_reason=reason, map_name=None, source="steam_sync", share_code_verified=True)
        match_id, created = self.storage.record_uploaded_match(job.user_id, match=match, now=self.clock())
        return UploadResult(match_id, created)
