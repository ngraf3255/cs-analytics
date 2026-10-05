"""Shared fakes for Valve, demo retrieval and parsing (no network)."""

import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from steamlink import sharecode
from steamlink.demo_parser import DemoParser, ParsedDeath, ParsedDemo, ParsedRound, ParsedSpawn
from steamlink.migrate import apply_migrations
from steamlink.storage.sql import SqlStorage

from dbutil import make_test_engine
from steamlink.valve import DemoInfo, DemoLocator, MatchHistoryClient, NextCodeResult

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
    """``match_times[match_id]``: the GC's match time (unix seconds) for that match."""

    def __init__(self):
        self.errors = {}
        self.match_times = {}

    def demo_url(self, share):
        if share.match_id in self.errors:
            raise self.errors[share.match_id]
        return f"http://replay1.valve.net/730/{share.match_id}_{share.outcome_id}.dem.bz2"

    def demo_info(self, share):
        return DemoInfo(self.demo_url(share), self.match_times.get(share.match_id))


class FakeFetcher:
    """Writes / yields a real file. Content defaults to a per-URL demo-like blob;
    set ``content[url]`` to simulate Valve serving specific bytes (plain .dem or
    .dem.bz2), ``files[url]`` to serve a local file, ``errors[url]`` to raise."""

    def __init__(self):
        self.fetched = []
        self.content = {}
        self.files = {}
        self.errors = {}

    def download(self, url, dest, on_progress=None):
        self.fetched.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url in self.files:
            import shutil
            shutil.copyfile(self.files[url], dest)
        else:
            with open(dest, "wb") as out:
                out.write(self.content.get(url, b"PBDEMS2\0" + url.encode()))
        if on_progress is not None:
            on_progress(1.0)

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
        return fake_demo()


# Players of fake_demo(): the test user (STEAM_ID in the API / sync tests) and an opponent.
FAKE_PLAYER = "76561198000000001"
FAKE_OPPONENT = "76561198000000077"


def fake_demo() -> ParsedDemo:
    """Two rounds, final score 1-1; FAKE_PLAYER is T in both: round 1 they get the opening
    kill and T wins, round 2 they are killed first (bayonet) and CT wins."""

    return ParsedDemo(
        map_name="de_mirage",
        rounds=[ParsedRound(1, 1000, 8000, "t"), ParsedRound(2, 9000, 15000, "ct")],
        deaths=[ParsedDeath(2280, "t", "ct", "ak47", 0, 0, FAKE_PLAYER, FAKE_OPPONENT),
                ParsedDeath(9640, "ct", "t", "bayonet", 0, 1, FAKE_OPPONENT, FAKE_PLAYER)],
        spawns=[ParsedSpawn(100, FAKE_PLAYER, "t"), ParsedSpawn(100, FAKE_OPPONENT, "ct"),
                ParsedSpawn(8500, FAKE_PLAYER, "t"), ParsedSpawn(8500, FAKE_OPPONENT, "ct")],
    )


def make_storage(tmp_path):
    engine = make_test_engine(tmp_path, 'sync')
    apply_migrations(engine)
    return SqlStorage(engine)
