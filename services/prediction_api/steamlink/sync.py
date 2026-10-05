"""User-triggered, bounded, idempotent match sync."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .crypto import AuthCodeCipher, DecryptionError
from .demo_parser import DemoParseError, DemoParser, extract_rounds
from .gc import DemoBotAuthFailed
from .sharecode import decode
from .storage.base import CursorConflict, NewMatch, Storage, User
from .upload import sha256_file
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


@dataclass(frozen=True)
class SyncOutcome:
    # up_to_date | partial | demo_not_ready | error
    status: str
    imported: int = 0
    processed: int = 0
    has_more: bool = False
    error: str | None = None


class SyncRejected(Exception):
    """Sync could not start: not_linked | already_running | too_soon."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SyncService:
    def __init__(
        self,
        *,
        storage: Storage,
        history: MatchHistoryClient,
        locator: DemoLocator,
        fetcher: DemoFetcher,
        parser: DemoParser,
        cipher: AuthCodeCipher,
        clock: Callable[[], datetime],
        max_matches: int = 1,
        lock_ttl_seconds: int = 900,
        min_interval_seconds: int = 30,
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

    def sync(self, user: User) -> SyncOutcome:
        now = self.clock()
        access = self.storage.get_match_access(user.id)
        if access is None:
            raise SyncRejected("not_linked")
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
            outcome = self._run(user)
        except Exception:
            log.exception("sync failed for user %s", user.id)  # no codes in this message
            raise
        finally:
            self.storage.release_sync_lock(
                user.id, token, self.clock(),
                status="error" if outcome.status == "error" else "ok",
                error=outcome.error, imported=outcome.imported,
            )
        return outcome

    def _run(self, user: User) -> SyncOutcome:
        imported = processed = 0
        for _ in range(self.max_matches):
            access = self.storage.get_match_access(user.id)
            if access is None:
                return SyncOutcome("error", imported, processed, error="not_linked")
            try:
                auth_code = self.cipher.decrypt(user.steam_id, access.auth_code_ciphertext)
            except DecryptionError:
                return SyncOutcome("error", imported, processed, error="credentials_unreadable")

            result = self.history.next_share_code(user.steam_id, auth_code, access.cursor_share_code)
            if result.status == "no_new_match":
                return SyncOutcome("up_to_date", imported, processed)
            if result.status != "ok" or not result.next_code:
                return SyncOutcome("error", imported, processed, error=result.status)

            share = decode(result.next_code)
            try:
                # Already stored (e.g. uploaded with this share code): don't download it again.
                if self.storage.skip_known_match(
                    user.id, expected_cursor=access.cursor_share_code, share_code=result.next_code,
                    valve_match_id=str(share.match_id), now=self.clock(),
                ):
                    processed += 1
                    continue
            except CursorConflict:
                return SyncOutcome("error", imported, processed, error="cursor_changed")
            match = self._import_one(result.next_code)
            if match is None:
                return SyncOutcome("error", imported, processed, error="demo_retrieval_not_configured")
            if match == "bot_auth_failed":
                return SyncOutcome("error", imported, processed, error="demo_bot_auth_failed")
            if match == "not_ready":
                return SyncOutcome("demo_not_ready", imported, processed)
            try:
                inserted = self.storage.record_match(
                    user.id, expected_cursor=access.cursor_share_code, match=match, now=self.clock()
                )
            except CursorConflict:
                return SyncOutcome("error", imported, processed, error="cursor_changed")
            processed += 1
            imported += int(inserted)
        return SyncOutcome("partial", imported, processed, has_more=True)

    def _import_one(self, share_code: str):
        """Return a NewMatch, ``"not_ready"``/``"bot_auth_failed"`` (cursor not advanced), or None (no locator)."""

        share = decode(share_code)
        base = dict(share_code=share_code, valve_match_id=f"{share.match_id}")
        try:
            url = self.locator.demo_url(share)
            with self.fetcher.fetch(url) as demo_path:
                # Cross-source dedupe key: same hash as a manual upload of this demo.
                base["demo_sha256"] = sha256_file(demo_path)
                parsed = self.parser.parse(demo_path)
        except DemoLocatorNotConfigured:
            return None
        except DemoBotAuthFailed:
            return "bot_auth_failed"
        except DemoNotReady:
            return "not_ready"
        except DemoUnavailable:
            return NewMatch(**base, status="unavailable", status_reason="demo_unavailable", map_name=None)
        except DemoTooLarge:
            return NewMatch(**base, status="unavailable", status_reason="demo_too_large", map_name=None)
        except DemoParseError:
            return NewMatch(**base, status="parse_failed", status_reason="parser_error", map_name=None)
        rounds = tuple(extract_rounds(parsed))
        return NewMatch(**base, status="imported", status_reason=None, map_name=parsed.map_name, rounds=rounds)
