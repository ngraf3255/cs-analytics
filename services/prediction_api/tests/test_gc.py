import pytest

from steamlink.gc import (
    DemoBotAuthFailed, GameCoordinator, GameCoordinatorDemoLocator, GCAuthError, GCMatch, GCNotReady, GCTimeout,
)
from steamlink.sharecode import ShareCode
from steamlink.valve import DemoNotReady, DemoUnavailable

SHARE = ShareCode(3767418281950970048, 1750155669, 55788)
URL = "http://replay382.valve.net/730/003767418281950970048_1750155669.dem.bz2"


class FakeGC(GameCoordinator):
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def full_match_info(self, match_id, outcome_id, token):
        self.calls.append((match_id, outcome_id, token))
        if self.error:
            raise self.error
        return self.result


def locator(gc, **kw):
    return GameCoordinatorDemoLocator(gc, min_interval_seconds=0, **kw)


def test_resolves_last_non_empty_round_url():
    gc = FakeGC(GCMatch(SHARE.match_id, 1, ("", URL, "")))
    assert locator(gc).demo_url(SHARE) == URL
    assert gc.calls == [(SHARE.match_id, SHARE.outcome_id, SHARE.token)]


@pytest.mark.parametrize("result", [
    None,
    GCMatch(SHARE.match_id, 1, ()),
    GCMatch(SHARE.match_id, 1, ("",)),
    GCMatch(999, 1, (URL,)),
    GCMatch(SHARE.match_id, 1, ("http://evil.example.com/730/1_2.dem.bz2",)),
])
def test_expired_unknown_or_unsafe_is_unavailable(result):
    with pytest.raises(DemoUnavailable):
        locator(FakeGC(result)).demo_url(SHARE)


@pytest.mark.parametrize("error", [GCNotReady(), GCTimeout()])
def test_gc_not_ready_or_timeout_is_transient(error):
    with pytest.raises(DemoNotReady):
        locator(FakeGC(error=error)).demo_url(SHARE)


def test_auth_failure_surfaces():
    with pytest.raises(DemoBotAuthFailed):
        locator(FakeGC(error=GCAuthError())).demo_url(SHARE)


def test_throttles_between_requests():
    now = [100.0]
    slept = []
    gc = FakeGC(GCMatch(SHARE.match_id, 1, (URL,)))
    loc = GameCoordinatorDemoLocator(gc, min_interval_seconds=2.0, clock=lambda: now[0],
                                     sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    loc.demo_url(SHARE)
    now[0] += 0.5
    loc.demo_url(SHARE)
    assert slept == [pytest.approx(1.5)]


def test_repeated_timeouts_for_same_match_become_unavailable():
    loc = GameCoordinatorDemoLocator(FakeGC(error=GCTimeout()), min_interval_seconds=0, max_timeouts_per_match=3)
    for _ in range(2):
        with pytest.raises(DemoNotReady):
            loc.demo_url(SHARE)
    with pytest.raises(DemoUnavailable):
        loc.demo_url(SHARE)
