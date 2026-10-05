"""Real CS2 Game Coordinator client built on steam.py (``steamio`` on PyPI).

A dedicated bot account signs in once per process, on a background thread with
its own asyncio loop, preferably with a refresh token (see
``docs/steam-demo-bot.md``). Requests from sync threads are bridged with
``run_coroutine_threadsafe``.

Credentials are never logged: steam.py loggers are capped at WARNING and
errors are mapped to credential-free exceptions.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from typing import Any, Callable

from .gc import GameCoordinator, GCAuthError, GCMatch, GCNotReady, GCTimeout

logging.getLogger("steam").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


def _default_client_factory() -> Any:
    from steam.ext import csgo

    class _BotClient(csgo.Client):
        async def code(self) -> str:  # never block on stdin in a server process
            if self.shared_secret:
                return await super().code()
            raise GCAuthError()

    return _BotClient()


def _is_auth_error(exc: BaseException) -> bool:
    if isinstance(exc, GCAuthError):
        return True
    try:
        from steam.errors import AuthenticatorError, InvalidCredentials, LoginError
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, (InvalidCredentials, LoginError, AuthenticatorError))


class SteamioGameCoordinator(GameCoordinator):
    def __init__(
        self,
        *,
        refresh_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        shared_secret: str | None = None,
        ready_timeout: float = 60.0,
        request_timeout: float = 30.0,
        retry_backoff_seconds: float = 60.0,
        client_factory: Callable[[], Any] = _default_client_factory,
    ):
        if not refresh_token and not (username and password):
            raise ValueError("a refresh token or username+password is required")
        self._refresh_token = refresh_token
        self._username = username
        self._password = password
        self._shared_secret = shared_secret
        self._ready_timeout = ready_timeout
        self._request_timeout = request_timeout
        self._retry_backoff = retry_backoff_seconds
        self._factory = client_factory
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._login_task: concurrent.futures.Future | None = None
        self._auth_failed = False
        self._last_start = 0.0

    # --- lifecycle ---------------------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="steam-gc", daemon=True).start()
            self._loop = loop
        return self._loop

    def _ensure_started(self) -> None:
        with self._lock:
            if self._auth_failed:
                raise GCAuthError()
            running = self._login_task is not None and not self._login_task.done()
            if running:
                return
            if self._login_task is not None and time.monotonic() - self._last_start < self._retry_backoff:
                raise GCNotReady()  # recently dropped; don't hammer Steam with logins
            loop = self._ensure_loop()
            self._client = None
            self._last_start = time.monotonic()
            self._login_task = asyncio.run_coroutine_threadsafe(self._login(), loop)

    async def _login(self) -> None:
        client = self._factory()
        self._client = client
        try:
            if self._refresh_token:
                await client.login(refresh_token=self._refresh_token)
            else:
                await client.login(self._username, self._password, shared_secret=self._shared_secret)
        except BaseException as exc:  # login() only returns when the connection ends
            if _is_auth_error(exc):
                self._auth_failed = True
                log.error("steam demo bot login rejected; update bot credentials")
            elif not isinstance(exc, asyncio.CancelledError):
                log.warning("steam demo bot connection ended: %s", type(exc).__name__)
        finally:
            self._client = None

    def close(self) -> None:
        if self._loop is not None and self._login_task is not None:
            self._login_task.cancel()

    # --- requests ----------------------------------------------------------------
    def full_match_info(self, match_id: int, outcome_id: int, token: int) -> GCMatch | None:
        self._ensure_started()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self._request(match_id, outcome_id, token), self._loop)
        try:
            return future.result(timeout=self._ready_timeout + self._request_timeout + 5)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise GCTimeout() from None

    async def _wait_for_client(self) -> Any:
        deadline = time.monotonic() + self._ready_timeout
        while self._client is None:
            if self._auth_failed:
                raise GCAuthError()
            if time.monotonic() > deadline:
                raise GCNotReady()
            await asyncio.sleep(0.2)
        return self._client

    async def _request(self, match_id: int, outcome_id: int, token: int) -> GCMatch | None:
        client = await self._wait_for_client()
        try:
            await asyncio.wait_for(client.wait_until_gc_ready(), self._ready_timeout)
        except asyncio.TimeoutError:
            raise (GCAuthError() if self._auth_failed else GCNotReady()) from None

        from steam.ext.csgo.protobufs import cstrike

        request_msg = cstrike.MatchListRequestFullGameInfo.MSG
        reply = client._state.ws.gc_wait_for(  # same mechanism as steam.ext.csgo.Client.fetch_match
            cstrike.MatchList,
            check=lambda msg: msg.msgrequestid == request_msg
            and (not msg.matches or msg.matches[0].matchid == match_id),
        )
        await client._state.ws.send_gc_message(
            cstrike.MatchListRequestFullGameInfo(matchid=match_id, outcomeid=outcome_id, token=token)
        )
        try:
            msg = await asyncio.wait_for(reply, self._request_timeout)
        except asyncio.TimeoutError:
            raise GCTimeout() from None
        if not msg.matches:
            return None
        match = msg.matches[0]
        return GCMatch(
            match_id=match.matchid,
            match_time=match.matchtime or None,
            round_urls=tuple(stat.map for stat in match.roundstatsall),
        )
