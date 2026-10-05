"""Sync also imports the match of the share code the user linked with (SYNC_IMPORT_START_MATCH):
users paste their most recent match token and expect that match, not only newer ones."""

import os

from steamlink.config import Settings, load_settings
from steamlink.upload import import_uploaded_demo
from steamlink.valve import UnconfiguredDemoLocator

from fakes import code
from test_sync import DEMO, cursor, env  # noqa: F401 (fixture)


def start_on(env, **kw):
    return env.service(import_start_match=True, **kw)


def test_first_sync_imports_the_linked_match_too_and_only_once(env):
    first = env.sync(start_on(env))
    assert (first.status, first.queued, first.has_more) == ("partial", 3, True)  # start match + 2 newer
    assert [env.storage.get_upload_job(env.user.id, j).share_code for j in first.job_ids] == [code(0), code(1), code(2)]
    assert cursor(env) == code(2)
    env.drain()
    matches = env.matches()
    assert sorted(matches) == sorted([code(0), code(1), code(2)])
    assert matches[code(0)].status == "imported" and matches[code(0)].source == "steam_sync"

    second = env.sync()
    assert (second.queued, [env.storage.get_upload_job(env.user.id, j).share_code for j in second.job_ids]) == (
        2, [code(3), code(4)])
    env.drain()
    third = env.sync()
    assert (third.status, third.queued, third.attached) == ("up_to_date", 0, 0)
    assert len(env.fetcher.fetched) == 5  # every match downloaded exactly once


def test_linked_match_already_in_the_list_is_not_downloaded_again(env):
    raw = os.path.join(env.ctx.jobs.job_dir, "manual.bin")
    os.makedirs(env.ctx.jobs.job_dir, exist_ok=True)
    with open(raw, "wb") as fh:
        fh.write(DEMO)
    work = os.path.join(env.ctx.jobs.job_dir, "manual.work")
    os.makedirs(work)
    import_uploaded_demo(storage=env.storage, parser=env.parser, user_id=env.user.id, raw_path=raw, workdir=work,
                         max_compressed_bytes=1 << 20, max_demo_bytes=1 << 20, now=env.clock(), share_code=code(0))
    out = env.sync(start_on(env))
    assert (out.queued, out.attached, out.skipped) == (2, 0, 0)  # only the newer two
    assert code(0) not in env.jobs()


def test_no_demo_bot_yet_the_linked_match_is_tried_again_later(env):
    out = env.sync(start_on(env, locator=UnconfiguredDemoLocator()))
    assert out.error == "demo_retrieval_not_configured" and out.queued == 0
    assert env.jobs() == {} and cursor(env) == code(0)
    out = env.sync(start_on(env))  # bot configured now
    assert out.queued == 3 and code(0) in env.jobs()


def test_full_queue_keeps_the_cursor_and_the_next_sync_continues(env):
    out = env.sync(start_on(env), max_active=1)
    assert (out.status, out.queued, out.has_more) == ("queue_full", 1, True)
    assert list(env.jobs()) == [code(0)] and cursor(env) == code(0)
    env.drain()
    out = env.sync()
    assert out.queued == 2 and sorted(env.jobs()) == sorted([code(0), code(1), code(2)])


def test_setting_defaults_on_and_can_be_turned_off():
    assert Settings(allowed_origins=[]).sync_import_start_match is True
    assert load_settings({"SYNC_IMPORT_START_MATCH": "false"}).sync_import_start_match is False
