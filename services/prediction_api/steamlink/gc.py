"""Resolve a match's demo URL through the CS2 Game Coordinator.

A share code decodes to (match id, outcome id, token). The GC answers a
``MatchListRequestFullGameInfo`` with a ``MatchList`` whose last
``roundstatsall`` entry carries the replay URL in its ``map`` field, e.g.
``http://replay382.valve.net/730/003767418281950970048_1750155669.dem.bz2``.

The GC transport sits behind :class:`GameCoordinator` so the locator logic is
unit-testable; :mod:`steamlink.gc_steamio` is the real implementation.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from .sharecode import ShareCode
from .valve import DemoInfo, DemoLocator, DemoNotReady, DemoUnavailable, check_replay_url


class GCNotReady(Exception):
    """Bot isn't connected to the GC yet (or lost its session). Transient."""


class GCTimeout(Exception):
    """GC did not answer in time. Transient (also what GC throttling looks like)."""


class GCAuthError(Exception):
    """Bot credentials are invalid/expired (e.g. refresh token revoked)."""


class DemoBotAuthFailed(Exception):
    """Raised to sync: the demo bot can't sign in; an operator must fix credentials."""


@dataclass(frozen=True)
class GCMatch:
    match_id: int
    match_time: int | None
    round_urls: tuple[str, ...]  # the ``map`` field of every roundstatsall entry, in order


class GameCoordinator(ABC):
    @abstractmethod
    def full_match_info(self, match_id: int, outcome_id: int, token: int) -> GCMatch | None:
        """Return the GC's match info, or None if the GC knows no such match (expired)."""


def demo_url_from_match(match: GCMatch) -> str | None:
    for url in reversed(match.round_urls):
        if url:
            return url
    return None


class GameCoordinatorDemoLocator(DemoLocator):
    def __init__(
        self,
        gc: GameCoordinator,
        *,
        min_interval_seconds: float = 2.0,
        max_timeouts_per_match: int = 3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._gc = gc
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_request: float | None = None
        # The GC may simply not answer for expired matches. After repeated
        # timeouts for the same match (across syncs), treat it as unavailable so
        # one dead match can't block the cursor forever.
        self._max_timeouts = max_timeouts_per_match
        self._timeouts: dict[int, int] = {}

    def _throttle(self) -> None:
        # Be gentle with the GC: at most one request per min_interval across all users.
        if self._last_request is not None:
            wait = self._min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def demo_url(self, share: ShareCode) -> str:
        return self.demo_info(share).url

    def demo_info(self, share: ShareCode) -> DemoInfo:
        with self._lock:
            self._throttle()
            try:
                match = self._gc.full_match_info(share.match_id, share.outcome_id, share.token)
            except GCTimeout:
                count = self._timeouts.get(share.match_id, 0) + 1
                self._timeouts[share.match_id] = count
                if count >= self._max_timeouts:
                    self._timeouts.pop(share.match_id, None)
                    raise DemoUnavailable() from None
                raise DemoNotReady() from None
            except GCNotReady:
                raise DemoNotReady() from None
            except GCAuthError:
                raise DemoBotAuthFailed() from None
            self._timeouts.pop(share.match_id, None)
        if match is None or match.match_id != share.match_id:
            raise DemoUnavailable()
        url = demo_url_from_match(match)
        if not url:
            raise DemoUnavailable()  # match known but demo expired/not recorded
        try:
            check_replay_url(url)
        except ValueError:
            raise DemoUnavailable() from None  # never follow a non-Valve URL
        return DemoInfo(url, match.match_time if match.match_time and match.match_time > 0 else None)
