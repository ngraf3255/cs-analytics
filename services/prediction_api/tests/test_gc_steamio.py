"""SteamioGameCoordinator with a fake steam.py client (no network, no Steam account)."""

import asyncio

import pytest

pytest.importorskip("steam.ext.csgo")
from steam.ext.csgo.protobufs import cstrike  # noqa: E402

from steamlink.gc import GCAuthError, GCNotReady  # noqa: E402
from steamlink.gc_steamio import SteamioGameCoordinator  # noqa: E402

URL = "http://replay382.valve.net/730/003767418281950970048_1750155669.dem.bz2"


class FakeWS:
    def __init__(self, reply_matches):
        self.reply_matches = reply_matches
        self.sent = []
        self._waiters = []

    def gc_wait_for(self, cls, check):
        future = asyncio.get_running_loop().create_future()
        self._waiters.append((check, future))
        return future

    async def send_gc_message(self, msg):
        self.sent.append(msg)
        if self.reply_matches is None:
            return  # GC never answers
        reply = cstrike.MatchList(msgrequestid=cstrike.MatchListRequestFullGameInfo.MSG, matches=self.reply_matches)
        for check, future in self._waiters:
            if check(reply) and not future.done():
                future.set_result(reply)


class FakeState:
    def __init__(self, ws):
        self.ws = ws


class FakeClient:
    def __init__(self, reply_matches=None, login_error=None):
        self._state = FakeState(FakeWS(reply_matches))
        self.login_error = login_error
        self.login_kwargs = None
        self.shared_secret = None

    async def login(self, *args, **kwargs):
        self.login_kwargs = (args, kwargs)
        if self.login_error:
            raise self.login_error
        await asyncio.Event().wait()  # stay connected

    async def wait_until_gc_ready(self):
        return None


def gc_with(client, **kw):
    opts = dict(refresh_token="rt", ready_timeout=2, request_timeout=0.5, client_factory=lambda: client)
    opts.update(kw)
    return SteamioGameCoordinator(**opts)


def test_full_match_info_returns_round_urls_and_uses_refresh_token():
    match = cstrike.MatchInfo(matchid=42, matchtime=1700000000,
                              roundstatsall=[cstrike.MatchmakingServerRoundStats(map=""),
                                             cstrike.MatchmakingServerRoundStats(map=URL)])
    client = FakeClient(reply_matches=[match])
    gc = gc_with(client)
    try:
        result = gc.full_match_info(42, 7, 9)
    finally:
        gc.close()
    assert result.match_id == 42 and result.round_urls[-1] == URL
    assert client.login_kwargs == ((), {"refresh_token": "rt"})
    sent = client._state.ws.sent[0]
    assert (sent.matchid, sent.outcomeid, sent.token) == (42, 7, 9)


def test_empty_match_list_means_unknown_match():
    gc = gc_with(FakeClient(reply_matches=[]))
    try:
        assert gc.full_match_info(42, 7, 9) is None
    finally:
        gc.close()


def test_no_reply_times_out():
    from steamlink.gc import GCTimeout
    gc = gc_with(FakeClient(reply_matches=None))
    try:
        with pytest.raises(GCTimeout):
            gc.full_match_info(42, 7, 9)
    finally:
        gc.close()


def test_invalid_credentials_become_auth_error_and_stick():
    from steam.errors import InvalidCredentials
    client = FakeClient(login_error=InvalidCredentials.__new__(InvalidCredentials))
    gc = gc_with(client)
    with pytest.raises((GCAuthError, GCNotReady)):
        gc.full_match_info(42, 7, 9)
    with pytest.raises(GCAuthError):
        gc.full_match_info(42, 7, 9)


def test_requires_credentials():
    with pytest.raises(ValueError):
        SteamioGameCoordinator()
