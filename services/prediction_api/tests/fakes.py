"""Shared fakes for Valve, demo retrieval and parsing (no network)."""

import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from steamlink import sharecode
from steamlink.demo_parser import DemoParser, ParsedDeath, ParsedDemo, ParsedRound
from steamlink.migrate import apply_migrations
from steamlink.storage.sql import SqlStorage

from dbutil import make_test_engine
from steamlink.valve import DemoLocator, MatchHistoryClient, NextCodeResult

KEY = Fernet.generate_key().decode()


def code(n: int) -> str:
    return sharecode.encode(sharecode.ShareCode(1000 + n, 2000 + n, 3000 + n))


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds=60):
        self.now += timedelta(seconds=seconds)


class FakeHistory(MatchHistoryClient):
    """Valve history as an ordered list of share codes; codes after the known one are 'new'."""

    def __init__(self, codes, valid_auth="AB12-CDE34-FG56"):
        self.codes = list(codes)
        self.valid_auth = valid_auth
        self.calls = []
        self.forced = None

    def next_share_code(self, steam_id, auth_code, known_code):
        self.calls.append(known_code)
        if self.forced:
            return NextCodeResult(self.forced)
        if auth_code != self.valid_auth:
            return NextCodeResult("invalid_auth_code")
        if known_code not in self.codes:
            return NextCodeResult("invalid_known_code")
        index = self.codes.index(known_code)
        if index + 1 >= len(self.codes):
            return NextCodeResult("no_new_match")
        return NextCodeResult("ok", self.codes[index + 1])


class FakeLocator(DemoLocator):
    def __init__(self):
        self.errors = {}

    def demo_url(self, share):
        if share.match_id in self.errors:
            raise self.errors[share.match_id]
        return f"http://replay1.valve.net/730/{share.match_id}_{share.outcome_id}.dem.bz2"


class FakeFetcher:
    """Yields a real temp file. Content defaults to a per-URL demo-like blob;
    set ``content[url]`` to simulate Valve serving specific bytes."""

    def __init__(self):
        self.fetched = []
        self.content = {}

    @contextmanager
    def fetch(self, url):
        self.fetched.append(url)
        fd, path = tempfile.mkstemp(suffix=".dem")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(self.content.get(url, b"PBDEMS2\0" + url.encode()))
            yield path
        finally:
            os.remove(path)


class FakeParser(DemoParser):
    def __init__(self, fail_after_calls=None):
        self.calls = 0
        self.fail_after_calls = fail_after_calls

    def parse(self, demo_path):
        self.calls += 1
        if self.fail_after_calls is not None and self.calls > self.fail_after_calls:
            raise RuntimeError("simulated crash mid-sync")
        return ParsedDemo(
            map_name="de_mirage",
            rounds=[ParsedRound(1, 1000, 8000, "t"), ParsedRound(2, 9000, 15000, "ct")],
            deaths=[ParsedDeath(2280, "t", "ct", "ak47"), ParsedDeath(9640, "ct", "t", "bayonet")],
        )


def make_storage(tmp_path):
    engine = make_test_engine(tmp_path, 'sync')
    apply_migrations(engine)
    return SqlStorage(engine)
