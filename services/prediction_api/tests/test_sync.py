import pytest

from steamlink.crypto import AuthCodeCipher
from steamlink.demo_parser import DemoParseError
from steamlink.sync import SyncRejected, SyncService
from steamlink.valve import DemoLocatorNotConfigured, DemoNotReady, DemoUnavailable, UnconfiguredDemoLocator

from fakes import KEY, Clock, FakeFetcher, FakeHistory, FakeLocator, FakeParser, code, make_storage

STEAM_ID = "76561198000000001"
AUTH = "AB12-CDE34-FG56"


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

    def service(**kw):
        opts = dict(storage=storage, history=history, locator=locator, fetcher=fetcher, parser=parser,
                    cipher=cipher, clock=clock, max_matches=2, min_interval_seconds=30)
        opts.update(kw)
        return SyncService(**opts)

    return dict(storage=storage, clock=clock, user=user, history=history, locator=locator,
                fetcher=fetcher, parser=parser, service=service, cipher=cipher)


def cursor(env):
    return env["storage"].get_match_access(env["user"].id).cursor_share_code


def test_bounded_batches_advance_cursor_until_up_to_date(env):
    svc = env["service"]()
    first = svc.sync(env["user"])
    assert (first.status, first.imported, first.has_more) == ("partial", 2, True)
    assert cursor(env) == code(2)
    env["clock"].advance()
    second = svc.sync(env["user"])
    assert (second.status, second.imported) == ("partial", 2)
    env["clock"].advance()
    third = svc.sync(env["user"])
    assert (third.status, third.imported, third.has_more) == ("up_to_date", 0, False)
    assert cursor(env) == code(4)
    matches = env["storage"].list_matches(env["user"].id, limit=50, offset=0)
    assert sorted(m.share_code for m in matches) == sorted(code(i) for i in range(1, 5))
    assert env["storage"].get_sync_state(env["user"].id, env["clock"]()).status == "ok"


def test_crash_mid_batch_keeps_committed_progress_and_retry_has_no_duplicates(env):
    with pytest.raises(RuntimeError):
        env["service"](parser=FakeParser(fail_after_calls=1)).sync(env["user"])
    assert cursor(env) == code(1)  # first match committed with its cursor
    state = env["storage"].get_sync_state(env["user"].id, env["clock"]())
    assert not state.locked  # lock released in finally
    env["clock"].advance()
    out = env["service"](max_matches=10).sync(env["user"])
    assert out.status == "up_to_date"
    shares = [m.share_code for m in env["storage"].list_matches(env["user"].id, limit=50, offset=0)]
    assert len(shares) == len(set(shares)) == 4


def test_replaying_an_already_stored_share_code_does_not_duplicate(env):
    svc = env["service"](max_matches=1)
    svc.sync(env["user"])
    # Simulate a stale cursor (e.g. restored backup): rewind cursor, re-sync same code.
    env["storage"].set_match_access(env["user"].id, ciphertext=env["cipher"].encrypt(STEAM_ID, AUTH),
                                    last4="FG56", cursor_share_code=code(0), now=env["clock"]())
    env["clock"].advance()
    out = svc.sync(env["user"])
    assert (out.processed, out.imported) == (1, 0)
    assert len(env["storage"].list_matches(env["user"].id, limit=50, offset=0)) == 1


def test_lock_and_rate_limit(env):
    storage, user, clock = env["storage"], env["user"], env["clock"]
    assert storage.try_acquire_sync_lock(user.id, "other", clock(), 900)
    with pytest.raises(SyncRejected) as exc:
        env["service"]().sync(user)
    assert exc.value.reason == "already_running"
    storage.release_sync_lock(user.id, "other", clock(), status="ok", error=None, imported=0)
    with pytest.raises(SyncRejected) as exc:
        env["service"]().sync(user)
    assert exc.value.reason == "too_soon"


def test_not_linked(env):
    env["storage"].delete_match_access(env["user"].id)
    with pytest.raises(SyncRejected) as exc:
        env["service"]().sync(env["user"])
    assert exc.value.reason == "not_linked"


def test_transient_demo_not_ready_does_not_advance(env):
    from steamlink import sharecode
    env["locator"].errors[sharecode.decode(code(1)).match_id] = DemoNotReady()
    out = env["service"]().sync(env["user"])
    assert out.status == "demo_not_ready"
    assert cursor(env) == code(0)


def test_permanent_unavailable_demo_is_recorded_and_skipped(env):
    from steamlink import sharecode
    env["locator"].errors[sharecode.decode(code(1)).match_id] = DemoUnavailable()
    out = env["service"]().sync(env["user"])
    assert out.imported == 2
    matches = {m.share_code: m for m in env["storage"].list_matches(env["user"].id, limit=50, offset=0)}
    assert matches[code(1)].status == "unavailable"
    assert matches[code(2)].status == "imported"


def test_parse_failure_recorded(env):
    class Broken(FakeParser):
        def parse(self, demo_path):
            raise DemoParseError("bad")

    env["service"](parser=Broken(), max_matches=1).sync(env["user"])
    match = env["storage"].list_matches(env["user"].id, limit=50, offset=0)[0]
    assert (match.status, match.status_reason) == ("parse_failed", "parser_error")


def test_unconfigured_locator_stops_without_advancing(env):
    out = env["service"](locator=UnconfiguredDemoLocator()).sync(env["user"])
    assert (out.status, out.error) == ("error", "demo_retrieval_not_configured")
    assert cursor(env) == code(0)


@pytest.mark.parametrize("forced", ["invalid_auth_code", "invalid_known_code", "rate_limited", "valve_error"])
def test_valve_errors_surface_safely(env, forced):
    env["history"].forced = forced
    out = env["service"]().sync(env["user"])
    assert (out.status, out.error) == ("error", forced)
    state = env["storage"].get_sync_state(env["user"].id, env["clock"]())
    assert state.status == "error" and state.last_error == forced
    assert cursor(env) == code(0)
