"""Automatic background sync (steamlink.autosync): selection, scheduling / backoff,
Valve 429 handling, opt-out, needs_relink skip, lease + per-user claim.
Runs on SQLite by default and on PostgreSQL with CSA_TEST_DATABASE_URL."""

import random
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest

import dbutil
from steamlink.autosync import LEASE_NAME, AutoSyncScheduler
from steamlink.config import ConfigError, Settings, load_settings
from steamlink.crypto import AuthCodeCipher
from steamlink.jobs import UploadJobWorker
from steamlink.storage.base import JOB_KIND_SYNC
from steamlink.sync import RELINK_ERRORS, SyncService
from steamlink.valve import NextCodeResult, UnconfiguredDemoLocator

from fakes import KEY, Clock, FakeFetcher, FakeHistory, FakeLocator, FakeParser, code, make_storage

AUTH = "AB12-CDE34-FG56"
INTERVAL = 1800


class PerUserHistory(FakeHistory):
    """FakeHistory plus per-user forced results (``forced_for[steam_id] = status``)."""

    def __init__(self, codes):
        super().__init__(codes, valid_auth=AUTH)
        self.forced_for = {}
        self.users = []

    def next_share_code(self, steam_id, auth_code, known_code):
        self.users.append(steam_id)
        if steam_id in self.forced_for:
            return NextCodeResult(self.forced_for[steam_id])
        return super().next_share_code(steam_id, auth_code, known_code)


@pytest.fixture()
def env(tmp_path):
    storage = make_storage(tmp_path)
    clock = Clock()
    cipher = AuthCodeCipher([KEY])
    history = PerUserHistory([code(i) for i in range(6)])
    settings = Settings(allowed_origins=[], upload_job_dir=str(tmp_path / "jobs"), upload_queue_max=10,
                        auto_sync_interval_seconds=INTERVAL, auto_sync_tick_seconds=60,
                        auto_sync_max_users_per_tick=5, auto_sync_max_backoff_seconds=6 * 3600)
    ctx = SimpleNamespace(storage=storage, clock=clock, settings=settings)
    ctx.sync = SyncService(storage=storage, history=history, locator=FakeLocator(), fetcher=FakeFetcher(),
                           parser=FakeParser(), cipher=cipher, clock=clock, max_matches=2, min_interval_seconds=30)
    ctx.jobs = UploadJobWorker(ctx)
    ctx.jobs.start = lambda: None  # tests drain jobs explicitly
    sched = AutoSyncScheduler(ctx, rng=random.Random(1))

    def add_user(n, *, cursor=0, link=True):
        user = storage.get_or_create_user(f"7656119800000{n:04d}", clock())
        if link:
            storage.set_match_access(user.id, ciphertext=cipher.encrypt(user.steam_id, AUTH), last4="FG56",
                                     cursor_share_code=code(cursor), now=clock())
        return user

    def state(user):
        return storage.get_sync_state(user.id, clock())

    def drain():
        while (job := storage.claim_next_upload_job(clock())) is not None:
            ctx.jobs.process(job)

    def tick(seconds=60):
        clock.advance(seconds)
        return sched.run_tick()

    return SimpleNamespace(storage=storage, clock=clock, history=history, settings=settings, ctx=ctx, sched=sched,
                           add_user=add_user, state=state, tick=tick, cipher=cipher, drain=drain)


def ids(users):
    return sorted(u.id for u in users)


def test_tick_syncs_due_linked_users_through_the_sync_button_path(env):
    a, b = env.add_user(1), env.add_user(2)
    env.add_user(3, link=False)  # never linked: nothing to sync
    result = env.tick()
    assert result.status == "ran" and sorted(result.synced) == ids([a, b])
    assert result.queued == 4  # 2 new matches each (max_matches=2), as POST /steam/sync would queue
    jobs = env.storage.list_upload_jobs(a.id, limit=10, kind=JOB_KIND_SYNC)
    assert {j.share_code for j in jobs} == {code(1), code(2)}
    assert env.storage.get_match_access(a.id).cursor_share_code == code(2)
    st = env.state(a)
    assert st.status == "ok" and st.last_synced_at == env.clock() and st.last_auto_sync_at == env.clock()
    # more history left (partial): again soon, not in a full interval
    assert timedelta(seconds=60) <= st.next_auto_sync_at - env.clock() <= timedelta(seconds=66)
    env.drain()
    assert {m.share_code for m in env.storage.list_matches(a.id, limit=10, offset=0)} == {code(1), code(2)}
    assert env.tick(30).synced == []  # not due yet
    assert sorted(env.tick(40).synced) == ids([a, b])  # follow-up: codes 3, 4
    assert env.storage.get_match_access(a.id).cursor_share_code == code(4)
    env.drain()
    assert sorted(env.tick(70).synced) == ids([a, b])  # code 5, then Valve says no newer match
    env.drain()
    st = env.state(a)
    assert env.storage.get_match_access(a.id).cursor_share_code == code(5)
    assert timedelta(seconds=INTERVAL) <= st.next_auto_sync_at - env.clock() <= timedelta(seconds=INTERVAL * 1.1)
    assert st.auto_sync_failures == 0 and st.last_auto_sync_error is None
    assert len(env.storage.list_matches(a.id, limit=10, offset=0)) == 5
    assert env.tick(INTERVAL - 100).synced == []
    assert sorted(env.tick(INTERVAL * 0.1 + 200).synced) == ids([a, b])


def test_recent_manual_sync_is_not_repeated_until_the_interval_passed(env):
    a = env.add_user(1, cursor=5)  # up to date
    env.clock.advance(60)
    outcome = env.ctx.sync.sync(a, max_active=10, job_file=env.ctx.jobs.job_file)  # the Sync button
    assert outcome.status == "up_to_date"
    assert env.tick(60).synced == []  # never scheduled, but synced a minute ago
    assert env.tick(INTERVAL).synced == [a.id]


def test_manual_sync_pushes_the_next_automatic_sync_out(env):
    a = env.add_user(1, cursor=5)
    assert env.tick().synced == [a.id]
    env.clock.advance(INTERVAL - 120)
    outcome = env.ctx.sync.sync(a, max_active=10, job_file=env.ctx.jobs.job_file)
    env.sched.after_sync(a.id, outcome, automatic=False)  # what POST /steam/sync does
    assert env.tick(300).synced == []  # the automatic one was due, but the user just synced
    st = env.state(a)
    assert st.last_auto_sync_at < st.last_synced_at
    assert env.tick(INTERVAL).synced == [a.id]


def test_opted_out_users_are_skipped_and_can_turn_it_back_on(env):
    a, b = env.add_user(1), env.add_user(2)
    env.storage.set_auto_sync_enabled(a.id, False, env.clock())
    assert env.tick().synced == [b.id]
    assert env.state(a).last_started_at is None  # never touched
    env.storage.set_auto_sync_enabled(a.id, True, env.clock())
    assert env.tick().synced == [a.id]


@pytest.mark.parametrize("error", sorted(RELINK_ERRORS))
def test_needs_relink_users_are_skipped_until_they_relink(env, error):
    a = env.add_user(1)
    env.history.forced_for[a.steam_id] = error if error != "credentials_unreadable" else "invalid_auth_code"
    if error == "credentials_unreadable":  # encryption key rotated away: the stored code can't be read
        env.storage.set_match_access(a.id, ciphertext="not-a-token", last4="FG56", cursor_share_code=code(0),
                                     now=env.clock())
        del env.history.forced_for[a.steam_id]
    assert env.tick().synced == [a.id]
    st = env.state(a)
    assert st.last_error == error and st.last_auto_sync_error == error
    assert st.auto_sync_failures == 0  # not a Valve problem: no backoff, just wait for new codes
    env.history.users.clear()
    for _ in range(3):
        assert env.tick(INTERVAL * 2).synced == []
    assert env.history.users == []  # Valve was not asked again
    # the user enters new codes -> picked up again
    env.history.forced_for.pop(a.steam_id, None)
    env.storage.set_match_access(a.id, ciphertext=env.cipher.encrypt(a.steam_id, AUTH), last4="FG56",
                                 cursor_share_code=code(0), now=env.clock() + timedelta(seconds=1))
    assert env.tick().synced == [a.id]
    assert env.state(a).last_error is None


def test_valve_429_stops_the_tick_for_everyone_and_backs_that_user_off(env):
    users = [env.add_user(n) for n in range(1, 4)]
    env.clock.advance(1)
    order = [u for u, _ in env.storage.list_auto_sync_candidates(
        env.clock(), last_finished_before=env.clock() - timedelta(seconds=INTERVAL), limit=10, relink_errors=())]
    first = order[0]
    env.history.forced_for[first.steam_id] = "rate_limited"
    result = env.tick()
    assert result.synced == [first.id] and result.stopped == "rate_limited"
    assert env.history.users == [first.steam_id]  # nobody else was sent to Valve this tick
    st = env.state(first)
    assert st.auto_sync_failures == 1 and st.last_auto_sync_error == "rate_limited"
    assert timedelta(seconds=2 * INTERVAL) <= st.next_auto_sync_at - env.clock() <= timedelta(seconds=2.2 * INTERVAL)
    for u in users:
        if u.id != first.id:
            assert env.state(u).last_started_at is None
    # next tick: Valve answers again; the others are synced, the backed-off user waits
    del env.history.forced_for[first.steam_id]
    assert sorted(env.tick().synced) == sorted(u.id for u in users if u.id != first.id)


def test_repeated_failures_back_off_exponentially_up_to_the_cap_and_reset_on_success(env):
    a = env.add_user(1)
    env.history.forced_for[a.steam_id] = "valve_error"
    delays = []
    for _ in range(5):
        st = env.state(a)
        wait = (st.next_auto_sync_at - env.clock()).total_seconds() + 1 if st.next_auto_sync_at else 60
        assert env.tick(wait).synced == [a.id]
        st = env.state(a)
        delays.append((st.next_auto_sync_at - env.clock()).total_seconds())
        assert env.tick(1).synced == []  # not retried in between
    assert env.state(a).auto_sync_failures == 5
    for n, delay in enumerate(delays, start=1):
        base = min(INTERVAL * 2 ** n, 6 * 3600)
        assert base <= delay <= base * 1.1, (n, delay)
    del env.history.forced_for[a.steam_id]
    env.tick(delays[-1] + 1)
    st = env.state(a)
    assert st.auto_sync_failures == 0 and st.last_auto_sync_error is None


def test_a_crashing_sync_counts_as_a_failure(env):
    a = env.add_user(1)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    env.ctx.sync.history.next_share_code = boom
    assert env.tick().synced == [a.id]
    st = env.state(a)
    assert (st.status, st.last_auto_sync_error, st.auto_sync_failures) == ("error", "internal_error", 1)
    assert not st.locked  # the per-user sync lock was released


def test_per_tick_cap_takes_the_longest_waiting_first(env):
    users = [env.add_user(n) for n in range(1, 8)]
    env.settings = env.ctx.settings = Settings(**{**env.settings.__dict__, "auto_sync_max_users_per_tick": 3})
    for u in users:
        env.history.forced_for[u.steam_id] = "no_new_match"
    first = env.tick()
    assert len(first.synced) == 3
    second = env.tick()
    assert len(second.synced) == 3 and not set(first.synced) & set(second.synced)
    third = env.tick()
    assert len(third.synced) == 1 and set(first.synced + second.synced + third.synced) == {u.id for u in users}
    assert env.tick().synced == []


def test_locked_users_are_skipped(env):
    a, b = env.add_user(1), env.add_user(2)
    assert env.storage.try_acquire_sync_lock(a.id, "manual", env.clock() + timedelta(seconds=60), 900)
    assert env.tick().synced == [b.id]


def test_queue_full_stops_the_tick_and_leaves_a_slot_for_uploads(env):
    env.ctx.settings = Settings(**{**env.settings.__dict__, "upload_queue_max": 3})
    users = [env.add_user(1), env.add_user(2), env.add_user(3)]
    assert env.sched.max_active() == 2  # one of the 3 queue slots stays free for uploads
    env.clock.advance(1)
    order = [u for u, _ in env.storage.list_auto_sync_candidates(
        env.clock(), last_finished_before=env.clock() - timedelta(seconds=INTERVAL), limit=10, relink_errors=())]
    result = env.tick()
    # the first user's two new matches fill both slots; the second gets queue_full -> tick stops
    assert result.synced == [order[0].id, order[1].id] and result.stopped == "queue_full"
    assert len(env.storage.list_active_upload_jobs()) == 2
    second = env.state(order[1])
    assert env.storage.get_match_access(order[1].id).cursor_share_code == code(0)  # nothing skipped
    assert second.auto_sync_failures == 0
    assert timedelta(seconds=60) <= second.next_auto_sync_at - env.clock() <= timedelta(seconds=66)
    assert env.state(order[2]).last_started_at is None  # not even asked
    assert env.tick().status == "queue_busy"  # nothing to do until jobs finish
    env.drain()
    assert env.tick().status == "ran"
    assert len(users) == 3


def test_disabled_and_unconfigured_deployments_do_nothing(env):
    env.add_user(1)
    env.ctx.settings = Settings(**{**env.settings.__dict__, "auto_sync_interval_seconds": 0})
    assert env.tick().status == "disabled"
    env.sched.start()
    assert not env.sched.running  # no thread at all
    env.ctx.settings = env.settings
    env.ctx.sync.locator = UnconfiguredDemoLocator()  # no demo bot yet: sync could only fail
    assert env.tick().status == "not_configured" and env.history.users == []


def test_lease_lets_one_scheduler_tick_and_another_take_over_when_it_expires(env):
    a, b = env.add_user(1), env.add_user(2)
    other = AutoSyncScheduler(env.ctx, rng=random.Random(2))
    assert other.holder != env.sched.holder
    for u in (a, b):
        env.history.forced_for[u.steam_id] = "no_new_match"
    assert env.tick().status == "ran"
    assert other.run_tick().status == "not_leader"
    assert env.tick(60).status == "ran"  # the holder renews its own lease
    env.clock.advance(121)  # holder stopped ticking (crashed): its lease expires
    assert other.run_tick().status == "ran"
    assert env.sched.run_tick().status == "not_leader"
    other.stop()  # releases the lease
    assert env.sched.run_tick().status == "ran"


def test_claim_is_compare_and_set(env):
    a = env.add_user(1)
    now = env.clock()
    assert env.storage.claim_auto_sync(a.id, seen=None, hold_until=now + timedelta(seconds=900), now=now)
    assert not env.storage.claim_auto_sync(a.id, seen=None, hold_until=now + timedelta(seconds=900), now=now)
    held = env.state(a).next_auto_sync_at
    assert env.storage.claim_auto_sync(a.id, seen=held, hold_until=now + timedelta(seconds=5), now=now)


@pytest.mark.skipif(not dbutil.postgres_url(), reason="concurrency race only meaningful on PostgreSQL")
def test_concurrent_schedulers_never_sync_a_user_twice(env, tmp_path):
    """Several API processes: concurrent ticks (lease bypassed by expiring it before each
    tick) and concurrent claims of the same users: every user is synced exactly once."""

    users = [env.add_user(n) for n in range(1, 9)]
    for u in users:
        env.history.forced_for[u.steam_id] = "no_new_match"
    env.ctx.settings = Settings(**{**env.settings.__dict__, "auto_sync_max_users_per_tick": 20})
    schedulers = [AutoSyncScheduler(env.ctx, rng=random.Random(i)) for i in range(6)]
    env.clock.advance(60)
    barrier = threading.Barrier(len(schedulers))
    results, errors = [], []

    def run(sched):
        try:
            barrier.wait()
            env.storage.release_lease(LEASE_NAME, "nobody")
            results.append(sched.run_tick())
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(s,)) for s in schedulers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert sum(r.status == "ran" for r in results) == 1  # exactly one took the lease
    synced = [uid for r in results for uid in r.synced]
    assert sorted(synced) == ids(users)

    # claims racing on the same user (lease holders overlapping after a long tick)
    a = users[0]
    st = env.state(a)
    now = st.next_auto_sync_at + timedelta(seconds=1)
    barrier2 = threading.Barrier(8)
    wins = []

    def claim():
        barrier2.wait()
        wins.append(env.storage.claim_auto_sync(a.id, seen=st.next_auto_sync_at,
                                                hold_until=now + timedelta(seconds=900), now=now))

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(wins) == [False] * 7 + [True]


def test_lease_acquire_race_has_one_winner(env):
    if not dbutil.postgres_url():
        pytest.skip("lease race only meaningful on PostgreSQL")
    now = env.clock()
    barrier = threading.Barrier(8)
    wins = []

    def take(i):
        barrier.wait()
        wins.append(env.storage.try_acquire_lease("race", f"h{i}", now, 60))

    threads = [threading.Thread(target=take, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(wins) == [False] * 7 + [True]


def test_scheduler_thread_starts_ticks_and_stops(env):
    a = env.add_user(1)
    env.history.forced_for[a.steam_id] = "no_new_match"
    env.ctx.settings = Settings(**{**env.settings.__dict__, "auto_sync_tick_seconds": 0.05})
    ticked = threading.Event()
    original = env.sched.run_tick

    def run_tick():
        result = original()
        ticked.set()
        return result

    env.sched.run_tick = run_tick
    env.sched.start()
    assert env.sched.running
    assert ticked.wait(5)
    env.sched.stop()
    assert not env.sched.running
    assert env.state(a).last_auto_sync_at is not None


def test_settings_from_env():
    s = load_settings({"AUTO_SYNC_INTERVAL_SECONDS": "0"})
    assert s.auto_sync_interval_seconds == 0
    d = load_settings({})
    assert (d.auto_sync_interval_seconds, d.auto_sync_tick_seconds, d.auto_sync_max_users_per_tick) == (1800, 60, 5)
    base = {"DATABASE_URL": "sqlite://", "TOKEN_ENCRYPTION_KEYS": KEY, "SESSION_SECRET": "s" * 40,
            "PUBLIC_API_URL": "https://api.example.com", "FRONTEND_URL": "https://example.com",
            "STEAM_WEB_API_KEY": "k"}
    for bad in ({"AUTO_SYNC_INTERVAL_SECONDS": "-1"}, {"AUTO_SYNC_INTERVAL_SECONDS": "10"},
                {"AUTO_SYNC_TICK_SECONDS": "1"}, {"AUTO_SYNC_MAX_USERS_PER_TICK": "0"},
                {"AUTO_SYNC_MAX_BACKOFF_SECONDS": "100"}):
        with pytest.raises(ConfigError):
            load_settings({**base, **bad})
    assert load_settings({**base, "AUTO_SYNC_INTERVAL_SECONDS": "0"}).auto_sync_interval_seconds == 0


def test_deleting_a_user_removes_their_schedule(env):
    a = env.add_user(1, cursor=5)
    env.tick()
    env.storage.delete_user(a.id)
    assert env.tick(INTERVAL * 2).synced == []
    assert env.storage.get_sync_state(a.id, env.clock()).next_auto_sync_at is None

