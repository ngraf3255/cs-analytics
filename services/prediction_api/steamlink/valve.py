"""Valve match-history and demo retrieval.

Security rules enforced here:
* the Steam Web API key, auth codes and share codes are sent only in the
  outbound query string and never logged (httpx request logging is silenced
  and exceptions are re-raised without the URL);
* demos are fetched only from Valve replay hosts, never from a client-supplied
  URL; redirects are not followed;
* compressed and decompressed sizes are capped and temp files are deleted in
  ``finally``.
"""

from __future__ import annotations

import bz2
import logging
import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator
from urllib.parse import urlsplit

import httpx

from .sharecode import ShareCode, is_valid_share_code

# httpx logs full request URLs (including query strings) at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

NEXT_CODE_URL = "https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1"
AUTH_CODE_RE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{5}-[A-Z0-9]{4}$")
REPLAY_HOST_RE = re.compile(r"^replay\d{1,4}\.valve\.net$")
REPLAY_PATH_RE = re.compile(r"^/730/\d{1,24}_\d{1,20}\.dem\.bz2$")  # match id is zero-padded (21 digits seen)


def is_valid_auth_code(code: str) -> bool:
    return bool(AUTH_CODE_RE.match(code or ""))


def normalize_auth_code(text: str | None) -> str:
    """What a user typed or pasted -> ``ABCD-EFGHI-JKLM`` when possible: spaces dropped,
    upper-cased, and the dashes put back if they were left out (13 letters/digits).
    Anything else is returned cleaned up but unchanged in shape (the format check rejects it)."""

    code = re.sub(r"\s+", "", text or "").upper()
    if re.fullmatch(r"[A-Z0-9]{13}", code):
        code = f"{code[:4]}-{code[4:9]}-{code[9:]}"
    return code


# --- GetNextMatchSharingCode -------------------------------------------------

@dataclass(frozen=True)
class NextCodeResult:
    # ok | no_new_match | invalid_auth_code | invalid_known_code | rate_limited | valve_error
    status: str
    next_code: str | None = None


class MatchHistoryClient(ABC):
    @abstractmethod
    def next_share_code(self, steam_id: str, auth_code: str, known_code: str) -> NextCodeResult: ...


class SteamWebMatchHistoryClient(MatchHistoryClient):
    """Calls ICSGOPlayers_730/GetNextMatchSharingCode.

    Status mapping follows Valve's published match-history docs:
    200 + code -> ok; 202 or ``n/a`` -> no newer match yet; 403 -> auth code
    invalid/revoked; 412 -> known code invalid, too old, or not this user's.
    TODO(verify-live): confirm these statuses with a real account before
    enabling in production (Valve's wiki was not reachable during development).
    """

    def __init__(self, api_key: str, http: httpx.Client):
        self._api_key = api_key
        self._http = http

    def next_share_code(self, steam_id: str, auth_code: str, known_code: str) -> NextCodeResult:
        params = {"key": self._api_key, "steamid": steam_id, "steamidkey": auth_code, "knowncode": known_code}
        try:
            response = self._http.get(NEXT_CODE_URL, params=params, follow_redirects=False)
        except httpx.HTTPError:
            return NextCodeResult("valve_error")  # deliberately drop the exception: it can contain the URL
        if response.status_code == 403:
            return NextCodeResult("invalid_auth_code")
        if response.status_code == 412:
            return NextCodeResult("invalid_known_code")
        if response.status_code == 429:
            return NextCodeResult("rate_limited")
        if response.status_code not in (200, 202):
            return NextCodeResult("valve_error")
        try:
            next_code = response.json()["result"]["nextcode"]
        except (ValueError, KeyError, TypeError):
            return NextCodeResult("valve_error")
        if response.status_code == 202 or next_code == "n/a":
            return NextCodeResult("no_new_match")
        if not isinstance(next_code, str) or not is_valid_share_code(next_code):
            return NextCodeResult("valve_error")
        return NextCodeResult("ok", next_code)


# --- Demo location -------------------------------------------------------------

class DemoNotReady(Exception):
    """Transient: try again later (a sync job fails with demo_not_ready; the next sync re-queues it)."""


class DemoUnavailable(Exception):
    """Permanent: the demo expired or no longer exists; record and move on."""


class DemoLocatorNotConfigured(Exception):
    """No way to resolve demo URLs is configured on this deployment."""


@dataclass(frozen=True)
class DemoInfo:
    url: str
    match_time: int | None = None  # unix seconds when the match was played, if the locator knows


class DemoLocator(ABC):
    # False for the placeholder below: sync then stops before queueing a download.
    configured: bool = True

    @abstractmethod
    def demo_url(self, share: ShareCode) -> str:
        """Return the Valve replay URL for a match or raise DemoNotReady/DemoUnavailable."""

    def demo_info(self, share: ShareCode) -> DemoInfo:
        """The replay URL plus the match time if known (the Game Coordinator sends it)."""

        return DemoInfo(self.demo_url(share))


class UnconfiguredDemoLocator(DemoLocator):
    """Placeholder until a Game Coordinator integration exists.

    TODO(gc-integration): A share code does not contain the replay server number.
    The demo URL (``http://replay<N>.valve.net/730/<matchid>_<outcomeid>.dem.bz2``)
    comes from the CS2 Game Coordinator (MatchListRequestFullGameInfo with the
    decoded match id, outcome id and token), which needs a logged-in Steam client
    session for a dedicated bot account. This can't be built or verified without a
    live account, so sync stops with ``demo_retrieval_not_configured`` and keeps the
    cursor unchanged until a real locator is plugged in.
    """

    configured = False

    def demo_url(self, share: ShareCode) -> str:
        raise DemoLocatorNotConfigured()


def check_replay_url(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or not REPLAY_HOST_RE.match(parts.hostname)
        or parts.port not in (None, 80, 443)
        or parts.username or parts.password
        or parts.query or parts.fragment
        or not REPLAY_PATH_RE.match(parts.path)
    ):
        raise ValueError("demo URL is not an allowed Valve replay URL")


# --- Demo download ---------------------------------------------------------------

class DemoTooLarge(Exception):
    pass


class DemoFetcher:
    def __init__(self, http: httpx.Client, *, max_download_bytes: int, max_decompressed_bytes: int):
        self._http = http
        self._max_download = max_download_bytes
        self._max_decompressed = max_decompressed_bytes

    @contextmanager
    def fetch(self, url: str) -> Iterator[str]:
        """Download + decompress to a random temp dir; yields the .dem path.

        The temp dir (compressed and decompressed files) is always removed.
        """

        check_replay_url(url)
        workdir = tempfile.mkdtemp(prefix="csa-demo-")
        try:
            compressed = os.path.join(workdir, "demo.dem.bz2")
            demo_path = os.path.join(workdir, "demo.dem")
            self._download(url, compressed)
            self._decompress(compressed, demo_path)
            os.remove(compressed)
            yield demo_path
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def download(self, url: str, dest: str, on_progress: Callable[[float], None] | None = None) -> None:
        """Download the (compressed) demo at a Valve replay URL to ``dest``.

        ``on_progress(fraction)`` is called as bytes arrive when the size is known.
        Raises DemoUnavailable (404), DemoNotReady (other status / network error)
        or DemoTooLarge; a partial ``dest`` is removed on error.
        """

        check_replay_url(url)
        try:
            self._download(url, dest, on_progress)
        except BaseException:
            try:
                os.remove(dest)
            except OSError:
                pass
            raise

    def _download(self, url: str, dest: str, on_progress: Callable[[float], None] | None = None) -> None:
        try:
            with self._http.stream("GET", url, follow_redirects=False) as response:
                if response.status_code == 404:
                    raise DemoUnavailable()
                if response.status_code != 200:
                    raise DemoNotReady()
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self._max_download:
                    raise DemoTooLarge()
                total = int(declared) if declared and declared.isdigit() else 0
                written = 0
                with open(dest, "wb") as out:
                    for chunk in response.iter_bytes():
                        written += len(chunk)
                        if written > self._max_download:
                            raise DemoTooLarge()
                        out.write(chunk)
                        if on_progress is not None and total:
                            on_progress(min(written / total, 1.0))
        except httpx.HTTPError:
            raise DemoNotReady() from None

    def _decompress(self, src: str, dest: str) -> None:
        decompress_bz2(src, dest, self._max_decompressed)


def decompress_bz2(src: str, dest: str, max_bytes: int, on_progress=None) -> None:
    """Stream-decompress ``src`` to ``dest``; raise DemoTooLarge past ``max_bytes``.

    ``on_progress(compressed_bytes_read)`` is called after each input chunk.
    """

    decompressor = bz2.BZ2Decompressor()
    written = 0
    read = 0
    try:
        with open(src, "rb") as inp, open(dest, "wb") as out:
            while not decompressor.eof:
                if decompressor.needs_input:
                    chunk = inp.read(1 << 20)
                    if not chunk:
                        break
                    read += len(chunk)
                    if on_progress is not None:
                        on_progress(read)
                else:
                    chunk = b""  # drain buffered output before feeding more input
                data = decompressor.decompress(chunk, max_length=1 << 20)
                written += len(data)
                if written > max_bytes:
                    raise DemoTooLarge()
                out.write(data)
    except OSError:
        raise DemoUnavailable() from None
    if not decompressor.eof:
        raise DemoUnavailable()
