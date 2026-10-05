"""Matches are shared across users: a match another user already imported (same
demo hash, or same share code from Valve's history) is added to the next user's
list without downloading or parsing it again, by Steam sync and by upload.
A share code typed in with an upload is only a hint and never attaches (or
hijacks) another user's match.
Runs on SQLite by default and on PostgreSQL with CSA_TEST_DATABASE_URL.
"""

import shutil
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from steamlink import sharecode
from steamlink.config import Settings
from steamlink.crypto import AuthCodeCipher
from steamlink.jobs import UploadJobWorker
from steamlink.migrate import MIGRATIONS_DIR, apply_migrations
from steamlink.storage.sql import SqlStorage, match_owners, matches, rounds
from steamlink.sync import SyncService
from steamlink.upload import import_uploaded_demo

from dbutil import make_test_engine
from fakes import KEY, Clock, FakeFetcher, FakeHistory, FakeLocator, FakeParser, code, make_storage
from test_api_steam import OCTET, make_client, upload_and_wait

AUTH = "AB12-CDE34-FG56"
ALICE, BOB = "76561198000000001", "76561198000000002"


def demo(n):
    return b"PBDEMS2\0" + bytes([n]) * 2048


def demo_url(n):
    share = sharecode.decode(code(n))
    return f"http://replay1.valve.net/730/{share.match_id}_{share.outcome_id}.dem.bz2"


@pytest.fixture()
def env(tmp_path):
    storage, clock, cipher = make_storage(tmp_path), Clock(), AuthCodeCipher([KEY])
    fetcher, parser = FakeFetcher(), FakeParser()
    for n in range(1, 6):
        fetcher.content[demo_url(n)] = demo(n)
    ctx = SimpleNamespace(storage=storage, clock=clock,
                          settings=Settings(allowed_origins=[], upload_job_dir=str(tmp_path / "jobs")))
    ctx.jobs = UploadJobWorker(ctx)
    users = {}

    def user(steam_id, history_len=3):
        """A linked user whose match history is code(0) -> ... -> code(history_len - 1)."""
        u = storage.get_or_create_user(steam_id, clock())
        storage.set_match_access(u.id, ciphertext=cipher.encrypt(steam_id, AUTH), last4="FG56",
                                 cursor_share_code=code(0), now=clock())
        users[u.id] = SyncService(storage=storage, history=FakeHistory([code(i) for i in range(history_len)]),
                                  locator=FakeLocator(), fetcher=fetcher, parser=parser, cipher=cipher, clock=clock,
                                  max_matches=5, min_interval_seconds=0)
        return u

    def run_sync(u):
        clock.advance(60)
        sync = ctx.sync = users[u.id]
        outcome = sync.sync(u, max_active=10, job_file=ctx.jobs.job_file)
        while (job := storage.claim_next_upload_job(clock())) is not None:
            ctx.jobs.process(job)
        return outcome

    def upload(u, data, share_code=None):
        raw, work = tmp_path / "up.bin", tmp_path / "work"
        raw.write_bytes(data)
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        clock.advance(1)
        return import_uploaded_demo(storage=storage, parser=parser, user_id=u.id, raw_path=str(raw),
                                    workdir=str(work), max_compressed_bytes=1 << 20, max_demo_bytes=1 << 20,
                                    now=clock(), share_code=share_code)

    def listed(u):
        return {m.id: m for m in storage.list_matches(u.id, limit=50, offset=0)}

    return SimpleNamespace(storage=storage, clock=clock, fetcher=fetcher, parser=parser, user=user,
                           sync=run_sync, upload=upload, listed=listed)


def test_sync_attaches_a_match_another_user_imported_without_downloading_it(env):
    alice, bob = env.user(ALICE, history_len=2), env.user(BOB, history_len=3)
    env.sync(alice)  # code(1)
    alice_match = next(iter(env.listed(alice).values()))
    assert env.parser.calls == 1 and env.fetcher.fetched == [demo_url(1)]

    outcome = env.sync(bob)  # code(1) (Alice has it) + code(2) (new)
    assert (outcome.status, outcome.attached, outcome.queued, outcome.skipped) == ("up_to_date", 1, 1, 0)
    assert env.fetcher.fetched == [demo_url(1), demo_url(2)]  # code(1) not downloaded again
    assert env.parser.calls == 2  # ... nor parsed again
    bobs = env.listed(bob)
    assert len(bobs) == 2 and alice_match.id in bobs
    assert bobs[alice_match.id].source == "steam_sync" and bobs[alice_match.id].rounds_count == 2
    assert env.storage.get_match(bob.id, alice_match.id)[1]  # Bob can open the report
    assert list(env.listed(alice)) == [alice_match.id]  # Alice's list is unchanged
    jobs = env.storage.list_upload_jobs(bob.id, limit=10, kind="steam_sync")
    assert [(j.share_code, j.match_created) for j in jobs] == [(code(2), True)]

    # Syncing again: nothing new for either.
    env.clock.advance(60)
    assert env.sync(bob).status == "up_to_date" and env.parser.calls == 2


def test_sync_job_attaches_when_another_user_imported_the_match_while_it_waited(env):
    alice, bob = env.user(ALICE, history_len=2), env.user(BOB, history_len=2)
    env.clock.advance(60)
    ctx_sync = env.storage  # queue Bob's job but don't run it yet
    bob_sync = SyncService(storage=ctx_sync, history=FakeHistory([code(0), code(1)]), locator=FakeLocator(),
                           fetcher=env.fetcher, parser=env.parser, cipher=AuthCodeCipher([KEY]), clock=env.clock,
                           max_matches=5, min_interval_seconds=0)
    assert bob_sync.sync(bob, max_active=10, job_file=lambda j: f"/tmp/{j}.upload").queued == 1
    bob_job = env.storage.claim_next_upload_job(env.clock())
    env.storage.update_upload_job(bob_job.id, env.clock(), status="queued")
    env.upload(alice, demo(1), share_code=None)  # Alice uploads the same demo first (no share code)
    env.sync(bob)  # runs Bob's job: downloads, hash matches Alice's upload -> shared, not parsed
    assert env.parser.calls == 1
    (bob_match,) = env.listed(bob).values()
    (alice_match,) = env.listed(alice).values()
    assert bob_match.id == alice_match.id and bob_match.share_code == code(1) and bob_match.share_code_verified


def test_upload_of_a_demo_another_user_imported_is_added_to_their_list(env):
    alice, bob = env.user(ALICE), env.user(BOB)
    first = env.upload(alice, demo(7))
    assert first.created and env.parser.calls == 1
    shared = env.upload(bob, demo(7))
    assert (shared.match_id, shared.created) == (first.match_id, True)  # new for Bob, not parsed again
    assert env.parser.calls == 1
    assert env.listed(bob)[first.match_id].source == "upload"
    again = env.upload(bob, demo(7))
    assert (again.match_id, again.created) == (first.match_id, False)  # already in Bob's list
    import bz2
    assert env.upload(bob, bz2.compress(demo(7))).match_id == first.match_id


def test_synced_demo_uploaded_by_another_user_is_shared(env):
    alice, bob = env.user(ALICE, history_len=2), env.user(BOB)
    env.sync(alice)
    (alice_match,) = env.listed(alice).values()
    shared = env.upload(bob, demo(1))  # same bytes Valve served Alice
    assert (shared.match_id, shared.created) == (alice_match.id, True) and env.parser.calls == 1


def test_upload_share_code_hint_never_attaches_another_users_match(env):
    alice, bob = env.user(ALICE, history_len=2), env.user(BOB)
    env.sync(alice)  # code(1) = demo(1), verified
    (alice_match,) = env.listed(alice).values()
    # Bob uploads a different demo claiming it is code(1): parsed as its own match, hint dropped.
    other = env.upload(bob, demo(9), share_code=code(1))
    assert other.created and other.match_id != alice_match.id and env.parser.calls == 2
    bobs = env.listed(bob)
    assert list(bobs) == [other.match_id] and not bobs[other.match_id].has_share_code
    assert env.listed(alice)[alice_match.id].share_code == code(1)


def test_verified_share_code_wins_over_a_contradicting_upload_hint(env):
    alice, bob = env.user(ALICE, history_len=4), env.user(BOB)
    wrong = env.upload(bob, demo(9), share_code=code(3))  # unverified claim on code(3)
    assert env.listed(bob)[wrong.match_id].share_code == code(3)
    env.sync(alice)  # code(1..3); Valve serves demo(3) for code(3), not Bob's demo(9)
    alices = {m.share_code: m for m in env.listed(alice).values()}
    assert set(alices) == {code(1), code(2), code(3)} and alices[code(3)].id != wrong.match_id
    assert alices[code(3)].share_code_verified
    assert not env.listed(bob)[wrong.match_id].has_share_code  # Bob's wrong hint was dropped


def test_correct_upload_hint_is_confirmed_by_sync_without_parsing_again(env):
    alice, bob = env.user(ALICE, history_len=2), env.user(BOB)
    up = env.upload(bob, demo(1), share_code=code(1))  # right claim, unverified
    env.sync(alice)  # Alice's sync downloads it (the hint can't be trusted), the hash matches
    assert env.fetcher.fetched == [demo_url(1)] and env.parser.calls == 1
    (alice_match,) = env.listed(alice).values()
    assert alice_match.id == up.match_id and alice_match.share_code_verified


def test_delete_user_keeps_shared_matches_for_the_other_owners(env):
    alice, bob = env.user(ALICE, history_len=2), env.user(BOB, history_len=2)
    env.sync(alice)
    env.sync(bob)
    (match_id,) = env.listed(bob)
    env.storage.delete_user(alice.id)
    assert list(env.listed(bob)) == [match_id] and env.storage.get_match(bob.id, match_id)[1]
    with env.storage.engine.begin() as conn:
        assert conn.execute(select(matches.c.user_id).where(matches.c.id == match_id)).scalar_one() == bob.id
    env.storage.delete_user(bob.id)
    with env.storage.engine.begin() as conn:
        assert conn.execute(select(matches)).all() == [] and conn.execute(select(rounds)).all() == []
        assert conn.execute(select(match_owners)).all() == []


def test_api_upload_and_sync_share_matches_between_users(tmp_path):
    client_a, ctx = make_client(tmp_path)
    ctx.sync.min_interval_seconds = 0

    def session(steam_id):
        u = ctx.storage.get_or_create_user(steam_id, ctx.clock())
        cookie, token_hash = ctx.signer.new_session_cookie()
        ctx.storage.create_session(token_hash, u.id, ctx.clock(), ctx.clock() + timedelta(days=1))
        client = TestClient(client_a.app, base_url="https://api.example.com", follow_redirects=False)
        client.cookies.set(ctx.settings.session_cookie_name, cookie)
        return client

    alice, bob = session(ALICE), session(BOB)
    response, job = upload_and_wait(alice, ctx, demo(5))
    assert response.status_code == 202 and job["created"] is True
    # Plain .dem another user already imported: answered at once, added to Bob's list.
    response, shared = upload_and_wait(bob, ctx, demo(5))
    assert response.status_code == 200 and shared["created"] is True and shared["match"]["id"] == job["match"]["id"]
    assert shared["match"]["score"] == {"ct": 1, "t": 1}  # from the (fake) parse, shared too
    assert [m["id"] for m in bob.get("/matches").json()["matches"]] == [job["match"]["id"]]
    assert bob.get(f"/matches/{job['match']['id']}").status_code == 200
    response, again = upload_and_wait(bob, ctx, demo(5))
    assert response.status_code == 200 and again["created"] is False

    # Sync: Alice links + syncs code(1..3); Bob's sync then attaches all three without a download.
    H = {"X-Requested-With": "csa"}
    for client in (alice, bob):
        assert client.put("/steam/match-access", headers=H,
                          json={"auth_code": AUTH, "share_code": code(0), "consent": True}).status_code == 200
    assert alice.post("/steam/sync", headers=H).status_code == 202 and ctx.jobs.wait_idle(60)
    fetched = len(ctx.sync.fetcher.fetched)
    ctx.clock.advance(120)
    body = bob.post("/steam/sync", headers=H).json()
    assert (body["status"], body["attached"], body["queued"], body["skipped"]) == ("up_to_date", 3, 0, 0)
    assert len(ctx.sync.fetcher.fetched) == fetched
    assert len(bob.get("/matches").json()["matches"]) == 4


def test_migration_merges_per_user_copies_into_shared_matches(tmp_path):
    old = tmp_path / "old_migrations"
    old.mkdir()
    for name in ("0001_steam_sync.sql", "0002_cross_source_dedupe.sql", "0003_upload_jobs.sql",
                 "0004_sync_jobs.sql"):
        shutil.copy(MIGRATIONS_DIR / name, old)
    engine = make_test_engine(tmp_path, "shared")
    apply_migrations(engine, old)
    t0 = "2026-10-04 12:00:00+00"
    with engine.begin() as conn:
        for uid in ("ua", "ub", "uc"):
            conn.execute(text("INSERT INTO users (id, steam_id, created_at, updated_at) VALUES (:u, :u, :t, :t)"),
                         {"u": uid, "t": t0})

        def match(mid, uid, share, vmid, sha, source, minute, status="imported"):
            conn.execute(text(
                "INSERT INTO matches (id, user_id, share_code, valve_match_id, status, map_name, rounds_count,"
                " imported_at, source, demo_sha256) VALUES (:id, :u, :s, :v, :st, 'de_nuke', 1, :t, :src, :h)"),
                {"id": mid, "u": uid, "s": share, "v": vmid, "st": status, "src": source, "h": sha,
                 "t": f"2026-10-04 12:{minute:02d}:00+00"})
            if status == "imported":
                conn.execute(text("INSERT INTO rounds (match_id, round_number, winner_side) VALUES (:m, 1, 'ct')"),
                             {"m": mid})

        # The same synced match for ua and ub (verified code, same hash) and for uc as an upload (hash only).
        match("a1", "ua", code(1), "1001", "h1", "steam_sync", 1)
        match("b1", "ub", code(1), "1001", "h1", "steam_sync", 2)
        match("c1", "uc", "upload:h1", "upload", "h1", "upload", 3)
        # ub synced code(2) but Valve had no demo; uc synced it later (imported): merged, keeps the import.
        match("b2", "ub", code(2), "1002", None, "steam_sync", 4, status="unavailable")
        match("c2", "uc", code(2), "1002", "h2", "steam_sync", 5)
        # uc's upload hint claims code(3), which ua's sync has with a different demo: hint dropped.
        match("a3", "ua", code(3), "1003", "h3", "steam_sync", 6)
        match("c3", "uc", code(3), "1003", "h9", "upload", 7)
        conn.execute(text(
            "INSERT INTO upload_jobs (id, user_id, status, demo_path, size_bytes, match_id, created_at, updated_at,"
            " kind) VALUES ('j', 'ub', 'done', '/x', 1, 'b1', :t, :t, 'steam_sync')"), {"t": t0})
    assert apply_migrations(engine) == ["0005_shared_matches"] + [
        p.stem for p in sorted(MIGRATIONS_DIR.iterdir()) if p.stem[:4] > "0005" and p.suffix in (".sql", ".py")]
    storage = SqlStorage(engine)

    def ids(uid):
        return {m.share_code: m for m in storage.list_matches(uid, limit=50, offset=0)}

    assert set(ids("ua")) == {code(1), code(3)}
    assert ids("ua")[code(1)].id == ids("ub")[code(1)].id == "a1"
    assert {m.id for m in ids("uc").values()} == {"a1", "c2", "c3"}
    assert ids("ub")[code(2)].id == "c2" and ids("ub")[code(2)].status == "imported"
    assert ids("uc")["upload:h9"].id == "c3" and not ids("uc")["upload:h9"].has_share_code
    assert ids("uc")[code(1)].source == "upload"  # each owner keeps how it arrived for them
    with engine.begin() as conn:
        assert {r[0] for r in conn.execute(text("SELECT id FROM matches"))} == {"a1", "c2", "a3", "c3"}
        assert conn.execute(text("SELECT COUNT(*) FROM rounds WHERE match_id IN ('b1', 'c1')")).scalar_one() == 0
        assert conn.execute(text("SELECT match_id FROM upload_jobs WHERE id = 'j'")).scalar_one() == "a1"
    assert storage.get_upload_job("ub", "j").match_id == "a1"
    # Global unique keys now back up the cross-user dedupe.
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO matches (id, user_id, share_code, valve_match_id, status, rounds_count, imported_at,"
                " source, demo_sha256) VALUES ('dup', 'ua', 'upload:x', 'upload', 'imported', 0, :t, 'upload', 'h1')"),
                {"t": t0})
