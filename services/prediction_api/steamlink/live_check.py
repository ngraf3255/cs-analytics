"""One-command live end-to-end check of the Steam match path.

Runs the whole chain the production API uses, step by step, and prints
PASS / FAIL / SKIP per step::

    1 config        env + inputs present and well-formed
    2 web_api_key   STEAM_WEB_API_KEY accepted by Steam (ISteamUser/GetPlayerSummaries)
    3 history       walk up to --walk share codes from --known-code (GetNextMatchSharingCode)
    4 gc_locate     demo bot signs in to the CS2 Game Coordinator, returns the newest match's replay URL
    5 download      fetch the .dem.bz2 from Valve's replay host (+ bz2 decompress), size and time
    6 parse_score   demoparser2 parse + model.pkl round scoring
    7 store_report  production sync -> background job -> storage, then the per-round report
                    (the same JSON as GET /matches/{id})

It stops at the first failing step (the rest are SKIP) with what is missing or
wrong. Exit code: 0 all passed, 1 a step failed, 2 a prerequisite is missing.

Live (from services/prediction_api; see docs/live-e2e-checklist.md)::

    export STEAM_WEB_API_KEY=... TOKEN_ENCRYPTION_KEYS=...
    export STEAM_BOT_REFRESH_TOKEN_FILE=~/.config/csa/steam-bot-refresh-token
    export CSA_LIVE_STEAM_ID=7656119... CSA_LIVE_KNOWN_CODE=CSGO-xxxxx-...
    python -m steamlink.live_check --auth-code-file ~/.config/csa/auth-code

Offline (fake Valve HTTP + fake Game Coordinator, real everything else)::

    python -m steamlink.live_check --fake --demo /path/to/match.dem.bz2

Inputs come from env / arguments only. Secrets (API key, auth code, bot
token) are never printed; share codes are shown masked. Storage defaults to a
throwaway SQLite file. ``--database-url`` stores into a real database instead:
the check then links the given Steam ID there for the run and restores that
user's previous link (or removes it) afterwards. ``DATABASE_URL`` from the
environment is deliberately ignored so the check can't touch a production
database by accident.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, TextIO

import httpx

from .config import ConfigError, Settings, load_settings
from .sharecode import ShareCode, decode, encode, is_valid_share_code
from .valve import is_valid_auth_code

CHECKLIST = "docs/live-e2e-checklist.md"
PLAYER_SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
STEAM_ID_RE_LEN = 17

STEPS = (
    ("config", "config + inputs"),
    ("web_api_key", "Steam Web API key"),
    ("history", "match history walk"),
    ("gc_locate", "GC demo URL (bot)"),
    ("download", "demo download"),
    ("parse_score", "parse + model score"),
    ("store_report", "store + round report"),
)

ENV_STEAM_ID = "CSA_LIVE_STEAM_ID"
ENV_AUTH_CODE = "CSA_LIVE_AUTH_CODE"
ENV_KNOWN_CODE = "CSA_LIVE_KNOWN_CODE"
ENV_DATABASE_URL = "CSA_LIVE_DATABASE_URL"
ENV_FAKE_DEMO = "CSA_LIVE_FAKE_DEMO"

# --- fake mode constants -------------------------------------------------------
FAKE_STEAM_ID = "76561198000000001"
FAKE_AUTH_CODE = "AAAA-BBBBB-CCCC"
FAKE_API_KEY = "fake-web-api-key"
FAKE_BASE_MATCH_ID = 3736456444682174484  # the MM demo's match id, as in csa-overnight/fake_sync_app.py


def fake_codes(count: int) -> list[str]:
    """The fake history: ``count + 1`` share codes, oldest first (index 0 is the known code)."""

    return [encode(ShareCode(FAKE_BASE_MATCH_ID + i, 1750155669 + i, 3000 + i)) for i in range(count + 1)]


class Missing(Exception):
    """A prerequisite is missing (exit 2)."""


class Failed(Exception):
    """A step ran and failed (exit 1)."""


def mask_code(code: str) -> str:
    """CSGO-AbCdE-...-xYz12: enough to tell codes apart, not enough to use one."""

    return f"{code[:10]}-...-{code[-5:]}" if is_valid_share_code(code) else "<invalid>"


# --- inputs ----------------------------------------------------------------------

@dataclass
class Inputs:
    fake: bool
    settings: Settings
    steam_id: str
    auth_code: str
    known_code: str
    walk: int
    database_url: str | None  # None: throwaway SQLite
    demo: str | None  # fake mode: the local demo the fake replay host serves
    model_path: Path
    gc_timeout: float
    report_json: str | None
    notes: list[str] = field(default_factory=list)


def _read_file(path: str, what: str) -> str:
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        raise Missing(f"{what}: file {path} could not be read") from None


def default_model_path(env: Mapping[str, str]) -> Path:
    root = Path(__file__).resolve().parents[3]  # repository root (model.pkl), as in main.py
    path = Path(env.get("MODEL_PATH") or root / "model.pkl")
    return path if path.is_absolute() else root / path


def resolve_inputs(args: argparse.Namespace, env: Mapping[str, str]) -> Inputs:
    """Collect every prerequisite; raise Missing listing all that are absent/invalid."""

    problems: list[str] = []
    notes: list[str] = []
    if args.fake:
        demo = args.demo or env.get(ENV_FAKE_DEMO) or env.get("CSA_TEST_DEMO")
        if not demo:
            problems.append(f"--fake needs a local CS2 demo: pass --demo PATH (.dem or .dem.bz2) or set {ENV_FAKE_DEMO}")
        elif not os.path.isfile(demo):
            problems.append(f"--demo {demo}: file not found")
        codes = fake_codes(args.walk)
        steam_id = args.steam_id or FAKE_STEAM_ID
        auth_code = args.auth_code or FAKE_AUTH_CODE
        known_code = args.known_code or codes[0]
        # Only parser tuning passes through; every Steam setting is fake.
        base = {k: v for k, v in env.items() if k.startswith("DEMO_")}
        base.update(STEAM_WEB_API_KEY=FAKE_API_KEY, STEAM_BOT_REFRESH_TOKEN="fake-refresh-token",
                    TOKEN_ENCRYPTION_KEYS=_new_fernet_key())
    else:
        demo = None
        base = dict(env)
        if not env.get("STEAM_WEB_API_KEY"):
            problems.append("STEAM_WEB_API_KEY is not set (create one at https://steamcommunity.com/dev/apikey; "
                            "domain: csgooner.com)")
        bot = (env.get("STEAM_BOT_REFRESH_TOKEN") or env.get("STEAM_BOT_REFRESH_TOKEN_FILE")
               or (env.get("STEAM_BOT_USERNAME") and env.get("STEAM_BOT_PASSWORD") and env.get("STEAM_BOT_SHARED_SECRET")))
        if not bot:
            problems.append("demo bot credentials are not set: STEAM_BOT_REFRESH_TOKEN or STEAM_BOT_REFRESH_TOKEN_FILE "
                            "(get one with: python -m steamlink.bot_login --out ~/.config/csa/steam-bot-refresh-token), "
                            "or STEAM_BOT_USERNAME + STEAM_BOT_PASSWORD + STEAM_BOT_SHARED_SECRET")
        if not env.get("TOKEN_ENCRYPTION_KEYS"):
            problems.append("TOKEN_ENCRYPTION_KEYS is not set (generate: python -c \"from cryptography.fernet import "
                            "Fernet; print(Fernet.generate_key().decode())\"; use the same value as on Render)")
        steam_id = args.steam_id or env.get(ENV_STEAM_ID) or ""
        if not steam_id:
            problems.append(f"Steam ID is not set: pass --steam-id or set {ENV_STEAM_ID} (SteamID64 of the player "
                            "whose matches to pull, 17 digits)")
        auth_code = ""
        if args.auth_code_file:
            try:
                auth_code = _read_file(args.auth_code_file, "--auth-code-file")
            except Missing as exc:
                problems.append(str(exc))
        elif env.get(ENV_AUTH_CODE):
            auth_code = env[ENV_AUTH_CODE].strip()
        elif args.auth_code:
            auth_code = args.auth_code
            notes.append("--auth-code on the command line ends up in shell history; prefer --auth-code-file")
        else:
            problems.append(f"game authentication code is not set: pass --auth-code-file PATH or set {ENV_AUTH_CODE} "
                            "(the player creates it at https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128)")
        known_code = args.known_code or env.get(ENV_KNOWN_CODE) or ""
        if not known_code:
            problems.append(f"known share code is not set: pass --known-code or set {ENV_KNOWN_CODE} (CS2 -> "
                            "Watch -> Your Matches -> a recent match -> copy share link; CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx)")

    auth_code = auth_code.strip().upper()
    known_code = known_code.strip()
    if steam_id and not (steam_id.isdigit() and len(steam_id) == STEAM_ID_RE_LEN):
        problems.append("Steam ID must be a 17-digit SteamID64 (e.g. 7656119xxxxxxxxxx)")
    if auth_code and not is_valid_auth_code(auth_code):
        problems.append("game authentication code has the wrong format (expected XXXX-XXXXX-XXXX)")
    if known_code and not is_valid_share_code(known_code):
        problems.append("known share code has the wrong format (expected CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx)")

    database_url = args.database_url or env.get(ENV_DATABASE_URL) or None
    if not args.fake and env.get("DATABASE_URL") and not database_url:
        notes.append("DATABASE_URL from the environment is ignored (throwaway SQLite instead); pass --database-url "
                     "to store into a real database")
    model_path = Path(args.model) if args.model else default_model_path(env)
    if not model_path.is_file():
        problems.append(f"model artifact not found at {model_path} (set MODEL_PATH or --model)")

    settings = None
    if not problems:
        # The API's own settings loader validates the bot/encryption/parser settings. The
        # web-session settings aren't used by the check; fill them so validation passes.
        overlay = dict(base)
        overlay["DATABASE_URL"] = database_url or "sqlite://"
        overlay.setdefault("SESSION_SECRET", secrets.token_urlsafe(32))
        overlay.setdefault("PUBLIC_API_URL", "http://localhost:8000")
        overlay.setdefault("FRONTEND_URL", "http://localhost:5173")
        try:
            settings = load_settings(overlay)
        except ConfigError as exc:
            problems.append(str(exc))
        else:
            from .crypto import AuthCodeCipher

            try:
                AuthCodeCipher(settings.token_encryption_keys)
            except ValueError as exc:
                problems.append(str(exc))
    if problems:
        raise Missing("\n".join(problems))
    return Inputs(fake=args.fake, settings=settings, steam_id=steam_id, auth_code=auth_code, known_code=known_code,
                  walk=args.walk, database_url=database_url, demo=demo, model_path=model_path,
                  gc_timeout=args.gc_timeout, report_json=args.report_json, notes=notes)


def _new_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


# --- fakes (offline mode) ----------------------------------------------------------

class _FileStream(httpx.SyncByteStream):
    def __init__(self, path: str):
        self._path = path

    def __iter__(self) -> Iterator[bytes]:
        with open(self._path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                yield chunk


class FakeValve:
    """Answers the three Valve HTTP endpoints the chain uses, from memory / a local file."""

    def __init__(self, codes: list[str], *, demo: str, steam_id: str = FAKE_STEAM_ID, auth_code: str = FAKE_AUTH_CODE,
                 api_key: str = FAKE_API_KEY):
        self.codes = codes
        self.demo = demo
        self.steam_id = steam_id
        self.auth_code = auth_code
        self.api_key = api_key
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append(f"{url.host}{url.path}")
        params = url.params
        if url.host == "api.steampowered.com":
            if params.get("key") != self.api_key:
                return httpx.Response(403, text="<html>Forbidden</html>")
            if url.path.startswith("/ISteamUser/GetPlayerSummaries"):
                players = [{"steamid": self.steam_id, "personaname": "fake player"}] \
                    if params.get("steamids") == self.steam_id else []
                return httpx.Response(200, json={"response": {"players": players}})
            if url.path.startswith("/ICSGOPlayers_730/GetNextMatchSharingCode"):
                if params.get("steamid") != self.steam_id or params.get("steamidkey") != self.auth_code:
                    return httpx.Response(403, text="Forbidden")
                known = params.get("knowncode")
                if known not in self.codes:
                    return httpx.Response(412, text="Precondition Failed")
                index = self.codes.index(known)
                if index + 1 >= len(self.codes):
                    return httpx.Response(202, json={"result": {"nextcode": "n/a"}})
                return httpx.Response(200, json={"result": {"nextcode": self.codes[index + 1]}})
        if url.host.endswith(".valve.net") and url.path.startswith("/730/"):
            return httpx.Response(200, headers={"content-length": str(os.path.getsize(self.demo))},
                                  stream=_FileStream(self.demo))
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=False)


def fake_game_coordinator(codes: list[str]):
    from .gc import GameCoordinator, GCMatch

    known = {decode(c).match_id: decode(c) for c in codes}

    class FakeGameCoordinator(GameCoordinator):
        def full_match_info(self, match_id: int, outcome_id: int, token: int):
            share = known.get(match_id)
            if share is None or share.outcome_id != outcome_id:
                return None
            url = f"http://replay1.valve.net/730/{match_id:021d}_{outcome_id}.dem.bz2"
            return GCMatch(match_id=match_id, match_time=int(time.time()) - 3600, round_urls=("", "", url))

        def close(self) -> None:
            pass

    return FakeGameCoordinator()


# --- output ------------------------------------------------------------------------

class Reporter:
    def __init__(self, out: TextIO):
        self.out = out
        self.results: dict[str, str] = {}
        self._pending: list[str] = []

    def line(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def detail(self, text: str) -> None:
        """A detail line of the running step, printed under its result line."""

        self._pending.append(text)

    def running(self, index: int, key: str) -> None:
        self.line(f"[{index}/{len(STEPS)}] {dict(STEPS)[key]:<22} ...")

    def result(self, index: int, key: str, status: str, message: str) -> None:
        self.results[key] = status
        label = dict(STEPS)[key]
        lines = message.splitlines() or [""]
        self.line(f"[{index}/{len(STEPS)}] {label:<22} {status:<4}  {lines[0]}")
        for extra in lines[1:] + self._pending:
            self.line(f"      {extra}")
        self._pending = []


def _fmt_mb(size: int) -> str:
    return f"{size / 1e6:.1f} MB"


# --- the steps ---------------------------------------------------------------------

@dataclass
class Run:
    inputs: Inputs
    http: httpx.Client
    make_gc: Callable[[], Any]
    parser: Any
    workdir: str
    r: Reporter
    codes: list[str] = field(default_factory=list)
    demo_url: str | None = None
    compressed_path: str | None = None
    demo_path: str | None = None
    parsed: Any = None
    report: dict | None = None
    summary: dict | None = None  # GET /matches/summary for the checked user (all their stored matches)


def step_web_api_key(run: Run) -> str:
    inp = run.inputs
    try:
        response = run.http.get(PLAYER_SUMMARIES_URL, params={"key": inp.settings.steam_web_api_key,
                                                              "steamids": inp.steam_id})
    except httpx.HTTPError as exc:
        raise Failed(f"could not reach api.steampowered.com ({type(exc).__name__})") from None
    if response.status_code in (401, 403):
        raise Failed(f"Steam rejected STEAM_WEB_API_KEY (HTTP {response.status_code}); check the key at "
                     "https://steamcommunity.com/dev/apikey")
    if response.status_code == 429:
        raise Failed("Steam Web API rate limit (HTTP 429); wait a few minutes and run again")
    if response.status_code != 200:
        raise Failed(f"unexpected Steam Web API answer (HTTP {response.status_code})")
    try:
        players = response.json()["response"]["players"]
    except (ValueError, KeyError, TypeError):
        raise Failed("unexpected Steam Web API answer (not the GetPlayerSummaries JSON)") from None
    if not players:
        raise Failed(f"key accepted, but Steam ID {inp.steam_id} was not found (wrong SteamID64?)")
    return f"key accepted; Steam ID {inp.steam_id} is {players[0].get('personaname', '?')!r}"


_HISTORY_ERRORS = {
    "invalid_auth_code": "Valve rejected the game authentication code (HTTP 403): wrong code, revoked, or not this "
                         "Steam ID's code. The player creates a new one on the Steam help page (see " + CHECKLIST + ")",
    "invalid_known_code": "Valve rejected the known share code (HTTP 412): not one of this player's matches, or too "
                          "old. Use the share code of a match the player played recently",
    "rate_limited": "Valve rate limit on GetNextMatchSharingCode (HTTP 429); wait and run again",
    "valve_error": "GetNextMatchSharingCode failed (network error, HTTP 5xx, or an unexpected answer)",
}


def step_history(run: Run) -> str:
    from .valve import SteamWebMatchHistoryClient

    inp = run.inputs
    client = SteamWebMatchHistoryClient(inp.settings.steam_web_api_key, run.http)
    codes = [inp.known_code]
    final = "ok"
    for _ in range(inp.walk):
        result = client.next_share_code(inp.steam_id, inp.auth_code, codes[-1])
        if result.status == "no_new_match":
            final = "no_new_match"
            break
        if result.status != "ok" or not result.next_code:
            where = "the known code" if len(codes) == 1 else f"code #{len(codes) - 1}"
            raise Failed(f"{_HISTORY_ERRORS.get(result.status, result.status)} (asking for the match after {where})")
        codes.append(result.next_code)
        run.r.detail(f"#{len(codes) - 1} {mask_code(result.next_code)} (match {decode(result.next_code).match_id})")
    run.codes = codes
    new = len(codes) - 1
    tail = "Valve says no newer match (202 / n/a)" if final == "no_new_match" else f"stopped after --walk {inp.walk}"
    if new == 0:
        return f"auth code + known code accepted; no newer match than the known code ({tail}); using the known code"
    return f"{new} newer share code(s) walked; {tail}; newest: {mask_code(codes[-1])}"


def step_gc_locate(run: Run) -> str:
    from .gc import GCAuthError, GCNotReady, GCTimeout, demo_url_from_match
    from .valve import check_replay_url

    share = decode(run.codes[-1])
    try:
        gc = run.make_gc()
    except ImportError:
        raise Failed("steam.py is not installed (pip install -r requirements.txt)") from None
    started = time.monotonic()
    try:
        match = gc.full_match_info(share.match_id, share.outcome_id, share.token)
    except GCAuthError:
        raise Failed("Steam rejected the demo bot's login (refresh token invalid/expired, or wrong password / "
                     "shared secret). Get a new token: python -m steamlink.bot_login --out FILE") from None
    except GCNotReady:
        raise Failed(f"the demo bot did not reach the CS2 Game Coordinator within {run.inputs.gc_timeout:.0f}s "
                     "(Steam down, Steam Guard prompt, or the bot has no CS2 license: sign in once and add CS2)") from None
    except GCTimeout:
        raise Failed("the Game Coordinator did not answer for this match (throttled, or the match is too old); "
                     "retry in a minute or use a more recent known code") from None
    finally:
        close = getattr(gc, "close", None)
        if close:
            close()
    elapsed = time.monotonic() - started
    if match is None or match.match_id != share.match_id:
        raise Failed("the Game Coordinator knows no such match (demo expired?); use a more recent match")
    url = demo_url_from_match(match)
    if not url:
        raise Failed("the Game Coordinator has the match but no demo URL (not recorded or expired)")
    try:
        check_replay_url(url)
    except ValueError:
        raise Failed("the Game Coordinator returned a URL that is not a Valve replay URL; not following it") from None
    run.demo_url = url
    when = (datetime.fromtimestamp(match.match_time, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if match.match_time else "unknown time")
    return f"{url} (match played {when}; GC answered in {elapsed:.1f}s)"


def step_download(run: Run) -> str:
    from .upload import sniff_demo
    from .valve import DemoFetcher, DemoNotReady, DemoTooLarge, DemoUnavailable, decompress_bz2

    settings = run.inputs.settings
    fetcher = DemoFetcher(run.http, max_download_bytes=settings.demo_max_download_bytes,
                          max_decompressed_bytes=settings.demo_max_decompressed_bytes)
    compressed = os.path.join(run.workdir, "download.dem.bz2")
    started = time.monotonic()
    try:
        fetcher.download(run.demo_url, compressed)
    except DemoUnavailable:
        raise Failed("the replay host answered 404: the demo expired or was never uploaded") from None
    except DemoNotReady:
        raise Failed("the replay host failed (HTTP error / network); the demo may not be ready yet, retry later") from None
    except DemoTooLarge:
        raise Failed(f"the download is larger than DEMO_MAX_DOWNLOAD_BYTES ({_fmt_mb(settings.demo_max_download_bytes)})") from None
    download_s = time.monotonic() - started
    size = os.path.getsize(compressed)
    with open(compressed, "rb") as fh:
        kind = sniff_demo(fh.read(8))
    msg = f"{_fmt_mb(size)} in {download_s:.1f}s ({size / 1e6 / max(download_s, 1e-6):.1f} MB/s)"
    if kind == "bz2":
        demo_path = os.path.join(run.workdir, "demo.dem")
        started = time.monotonic()
        try:
            decompress_bz2(compressed, demo_path, settings.demo_max_decompressed_bytes)
        except DemoTooLarge:
            raise Failed(f"decompressed demo exceeds DEMO_MAX_DECOMPRESSED_BYTES "
                         f"({_fmt_mb(settings.demo_max_decompressed_bytes)})") from None
        except DemoUnavailable:
            raise Failed("the download is not a valid .bz2 archive") from None
        os.remove(compressed)
        msg += f"; bz2 -> {_fmt_mb(os.path.getsize(demo_path))} .dem in {time.monotonic() - started:.1f}s"
    elif kind == "dem":
        demo_path = compressed
        msg += " (served uncompressed)"
    else:
        raise Failed("the download is neither a CS2 .dem nor a .bz2 archive")
    with open(demo_path, "rb") as fh:
        if sniff_demo(fh.read(8)) != "dem":
            raise Failed("the decompressed file is not a CS2 demo (PBDEMS2 header missing)")
    run.demo_path = demo_path
    return msg


def load_scorer(model_path: Path):
    import joblib

    from .scoring import RoundScorer

    saved = joblib.load(model_path)
    return RoundScorer(saved["model"], [str(v) for v in saved["map_options"]], [str(v) for v in saved["weapon_options"]])


def step_parse_score(run: Run, scorer) -> str:
    from .demo_parser import DemoParseError, extract_rounds

    started = time.monotonic()
    try:
        parsed = run.parser.parse(run.demo_path)
    except DemoParseError as exc:
        raise Failed(f"demoparser2 could not parse the demo: {exc}") from None
    parse_s = time.monotonic() - started
    if not parsed.rounds:
        raise Failed("the demo parsed but has no rounds")
    rounds = extract_rounds(parsed)
    scores = scorer.score_rounds(parsed.map_name, rounds)
    scored = [s for s in scores if s.unscored_reason is None]
    run.parsed = parsed
    msg = f"{parsed.map_name}, {len(rounds)} rounds parsed in {parse_s:.1f}s; model scored {len(scored)}/{len(rounds)}"
    if not scored:
        reasons = sorted({s.unscored_reason for s in scores})
        msg += f"\nWARNING: no round could be scored ({', '.join(reasons)})"
    return msg


class _PreParsed:
    """Hands the parse from step 6 to the import pipeline (no second parse)."""

    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = 0

    def parse(self, demo_path: str):
        self.calls += 1
        return self.parsed


class _CachedLocator:
    configured = True

    def __init__(self, url: str):
        self.url = url

    def demo_url(self, share):
        return self.url


class _LocalFetcher:
    """The sync job's download step, served from step 5's file (hard link when possible)."""

    def __init__(self, path: str):
        self.path = path

    def download(self, url, dest, on_progress=None):
        try:
            os.link(self.path, dest)
        except OSError:
            shutil.copyfile(self.path, dest)
        if on_progress is not None:
            on_progress(1.0)


def step_store_report(run: Run, scorer) -> str:
    from .api import SteamContext, build_match_report
    from .crypto import AuthCodeCipher
    from .jobs import UploadJobWorker
    from .migrate import apply_migrations
    from .sessions import CookieSigner
    from .storage.base import JOB_KIND_SYNC, UploadJob
    from .storage.sql import SqlStorage, make_engine
    from .sync import SyncService, _ordered_job_id
    from .valve import SteamWebMatchHistoryClient

    inp = run.inputs
    url = inp.database_url or f"sqlite:///{os.path.join(run.workdir, 'live_check.db')}"
    engine = make_engine(url)
    try:
        apply_migrations(engine)
        storage = SqlStorage(engine)
        cipher = AuthCodeCipher(inp.settings.token_encryption_keys)
        clock = lambda: datetime.now(timezone.utc)  # noqa: E731
        now = clock()
        user = storage.get_or_create_user(inp.steam_id, now)
        previous = storage.get_match_access(user.id)
        newest = run.codes[-1]
        cursor = run.codes[-2] if len(run.codes) > 1 else newest
        storage.set_match_access(user.id, ciphertext=cipher.encrypt(inp.steam_id, inp.auth_code),
                                 last4=inp.auth_code[-4:], cursor_share_code=cursor, now=now)
        history = SteamWebMatchHistoryClient(inp.settings.steam_web_api_key, run.http)
        sync = SyncService(storage=storage, history=history, locator=_CachedLocator(run.demo_url),
                           fetcher=_LocalFetcher(run.demo_path), parser=_PreParsed(run.parsed), cipher=cipher,
                           clock=clock, max_matches=1, min_interval_seconds=0,
                           max_job_attempts=inp.settings.sync_job_max_attempts)
        settings = inp.settings
        job_dir = os.path.join(run.workdir, "jobs")
        ctx = SteamContext(settings=replace(settings, upload_job_dir=job_dir), storage=storage,
                           signer=CookieSigner(secrets.token_urlsafe(32)), cipher=cipher, history=history, sync=sync,
                           scorer=scorer, http=run.http, clock=clock)
        worker = UploadJobWorker(ctx)
        try:
            job_id = None
            share = decode(newest)
            if len(run.codes) > 1:
                # The real POST /steam/sync path: one more history call from the cursor, which
                # queues a steam_sync job for the newest match (or skips it if already stored).
                outcome = sync.sync(user, max_active=1000, job_file=worker.job_file)
                run.r.detail(f"sync: status={outcome.status} queued={outcome.queued} skipped={outcome.skipped} "
                             f"attached={outcome.attached}" + (f" error={outcome.error}" if outcome.error else ""))
                if outcome.status == "error":
                    raise Failed(f"POST /steam/sync logic failed: {outcome.error}")
                for queued_id in outcome.job_ids:
                    queued_job = storage.get_upload_job(user.id, queued_id)
                    if queued_job and queued_job.share_code == newest:
                        job_id = queued_id
                    elif queued_job:
                        run.r.detail(f"note: the sync also re-queued an earlier failed job {queued_id}; the API's "
                                     "worker runs it")
            started = time.monotonic()
            match_id = created = None
            if job_id is None:
                existing = storage.find_match(user.id, share_code=newest, valve_match_id=str(share.match_id))
                if existing is not None:
                    match_id, created = existing.id, False  # already in this user's list: nothing to import
                    run.r.detail("the match is already in this user's list in this database (no job needed)")
                else:
                    # The known code is the newest match: queue the same steam_sync job directly.
                    cursor_now = storage.get_match_access(user.id).cursor_share_code
                    status, job_id = storage.enqueue_sync_job(
                        UploadJob(id=(new_id := _ordered_job_id()), user_id=user.id, status="queued",
                                  demo_path=worker.job_file(new_id), size_bytes=0,
                                  created_at=clock(), updated_at=clock(), share_code=newest, kind=JOB_KIND_SYNC),
                        expected_cursor=cursor_now, max_active=1000, now=clock())
                    if status != "queued":
                        raise Failed(f"could not queue the sync job ({status}): one for this match is already "
                                     "active in this database (is the API's worker running it?)")
            if job_id is not None:
                job = storage.get_upload_job(user.id, job_id)
                if job.status != "queued":
                    raise Failed(f"the sync job for this match is already {job.status} in this database "
                                 "(is the API's worker running it?)")
                job = replace(job, demo_path=worker.job_file(job.id))
                worker.process(job)  # the background job: locate -> download -> hash -> dedupe -> parse -> store
                job = storage.get_upload_job(user.id, job_id)
                if job.status != "done" or not job.match_id:
                    raise Failed(f"the sync job ended {job.status} with error {job.error}")
                match_id, created = job.match_id, job.match_created
            report = build_match_report(storage, scorer, user.id, match_id)
            if report is None:
                raise Failed("the match was stored but is not in the user's list")
            match = report["match"]
            if match["status"] != "imported":
                raise Failed(f"the match was stored as {match['status']} ({match['status_reason']})")
            run.report = report
            from .analytics import build_user_summary

            run.summary = build_user_summary(storage, scorer, user.id)
            summary = report["summary"]
            backend = "PostgreSQL" if engine.dialect.name == "postgresql" else "SQLite"
            return (f"match {match_id} stored in {backend} ({'new' if created else 'already stored'}, "
                    f"job {time.monotonic() - started:.1f}s); report: {summary['rounds']} rounds, "
                    f"{summary['scored']} scored, {summary['correct_predictions']} predicted correctly")
        finally:
            # Leave a real database's link as it was before the check.
            if inp.database_url:
                if previous is None:
                    storage.delete_match_access(user.id)
                else:
                    storage.set_match_access(user.id, ciphertext=previous.auth_code_ciphertext,
                                             last4=previous.auth_code_last4,
                                             cursor_share_code=previous.cursor_share_code, now=clock())
    finally:
        engine.dispose()


def print_report(r: Reporter, report: dict) -> None:
    match, summary = report["match"], report["summary"]
    score = match.get("score")
    r.line()
    r.line(f"Round report ({match['map_name']}, final score "
           f"{'CT %d - T %d' % (score['ct'], score['t']) if score else 'unknown'}, source {match['source']}):")
    r.line(f"  {'rnd':>3}  {'winner':<6} {'opening kill':<28} {'predicted':<9} {'P(ct)':>6} {'P(t)':>6}  note")
    for rnd in report["rounds"]:
        ok = rnd["opening_kill"]
        seconds = f"{ok['seconds']:.1f}s" if ok and ok["seconds"] is not None else "?s"
        kill = f"{ok['side'] or '?'} {seconds} {ok['weapon'] or '?'}" if ok else "-"
        pred = rnd["prediction"]
        if pred:
            mark = "ok" if pred["predicted_winner"] == rnd["actual_winner"] else "miss"
            r.line(f"  {rnd['round_number']:>3}  {rnd['actual_winner'] or '?':<6} {kill:<28} "
                   f"{pred['predicted_winner']:<9} {pred['probabilities']['ct']:>6.2f} {pred['probabilities']['t']:>6.2f}  {mark}")
        else:
            r.line(f"  {rnd['round_number']:>3}  {rnd['actual_winner'] or '?':<6} {kill:<28} {'-':<9} {'':>6} {'':>6}  "
                   f"{rnd['unscored_reason']}")
    accuracy = (f"{summary['correct_predictions'] / summary['scored']:.0%}" if summary["scored"] else "n/a")
    r.line(f"  {summary['rounds']} rounds, {summary['scored']} scored, {summary['correct_predictions']} correct "
           f"({accuracy}). {report['model']['note']}")


def print_summary(r: Reporter, summary: dict) -> None:
    """One paragraph of GET /matches/summary (cross-match analytics) for the checked user."""

    def pct(value):
        return "n/a" if value is None else f"{value:.0%}"

    totals, pred, sides = summary["totals"], summary["prediction"], summary["sides"]
    brier = "n/a" if pred["brier_score"] is None else f"{pred['brier_score']:.3f}"
    r.line()
    r.line(f"Previous matches (GET /matches/summary): {totals['imported_matches']} imported of {totals['matches']}, "
           f"{totals['rounds']} rounds, {totals['scored_rounds']} scored; model hit rate {pct(pred['hit_rate'])} "
           f"(opening-kill baseline {pct(pred['opening_kill_baseline_hit_rate'])}), Brier {brier} "
           f"(coin flip {summary['model']['coin_flip_brier_score']}); CT side won {pct(sides['ct_win_rate'])} "
           f"of {sides['rounds_with_winner']} rounds")


# --- main --------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m steamlink.live_check",
                                description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fake", action="store_true", help="offline: fake Valve HTTP + fake Game Coordinator, local --demo")
    p.add_argument("--demo", help=f"fake mode: local .dem / .dem.bz2 the fake replay host serves (or {ENV_FAKE_DEMO})")
    p.add_argument("--steam-id", help=f"SteamID64 of the player (or {ENV_STEAM_ID})")
    p.add_argument("--auth-code-file", help="file holding the player's game authentication code (preferred)")
    p.add_argument("--auth-code", help=f"the game authentication code itself (lands in shell history; or {ENV_AUTH_CODE})")
    p.add_argument("--known-code", help=f"a share code of one of the player's recent matches (or {ENV_KNOWN_CODE})")
    p.add_argument("--walk", type=int, default=3, help="max newer share codes to walk (default 3)")
    p.add_argument("--database-url", help=f"store into this database instead of a throwaway SQLite file (or {ENV_DATABASE_URL})")
    p.add_argument("--model", help="model artifact (default: MODEL_PATH or <repo>/model.pkl)")
    p.add_argument("--gc-timeout", type=float, default=60.0, help="seconds for the bot to reach the GC (default 60)")
    p.add_argument("--report-json", help="also write the full report JSON (GET /matches/{id} format) here")
    p.add_argument("--keep-workdir", action="store_true", help="keep the temp dir (downloaded demo, SQLite db)")
    p.add_argument("--debug", action="store_true",
                   help="re-raise unexpected errors with a traceback (may print URLs; don't paste it publicly)")
    return p


def run_check(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None, out: TextIO | None = None,
              parser=None, http: httpx.Client | None = None, make_gc: Callable[[], Any] | None = None) -> int:
    """Entry point; ``parser`` / ``http`` / ``make_gc`` let tests swap in doubles."""

    args = build_arg_parser().parse_args(argv)
    env = os.environ if env is None else env
    r = Reporter(out or sys.stdout)
    if args.walk < 1 or args.walk > 50:
        r.line("--walk must be between 1 and 50")
        return 2
    r.line(f"cs-analytics live E2E check ({'FAKE: offline Valve + GC doubles' if args.fake else 'LIVE: real Steam'})")

    def skip_rest(start: int, reason: str) -> None:
        for index, (key, _) in enumerate(STEPS[start:], start + 1):
            r.result(index, key, "SKIP", reason)

    try:
        inputs = resolve_inputs(args, env)
    except Missing as exc:
        r.result(1, "config", "FAIL", "missing prerequisite(s):\n" + "\n".join(f"- {p}" for p in str(exc).splitlines()))
        skip_rest(1, "blocked by step 1")
        r.line(f"\nRESULT: BLOCKED at step 1 (config). What's still needed: {CHECKLIST}")
        return 2
    s = inputs.settings
    bot = "fake" if inputs.fake else ("refresh token" if s.steam_bot_refresh_token else "username + shared secret")
    db = inputs.database_url.split("@")[-1] if inputs.database_url else "throwaway SQLite"
    r.result(1, "config", "PASS", f"steam id {inputs.steam_id}, auth code ****-*****-{inputs.auth_code[-4:]}, known code "
             f"{mask_code(inputs.known_code)}, bot: {bot}, db: {db}, parse: {s.demo_parse_isolation}"
             + "".join(f"\nnote: {n}" for n in inputs.notes))

    fake_valve = None
    if inputs.fake:
        codes = fake_codes(inputs.walk)
        fake_valve = FakeValve(codes, demo=inputs.demo)
        http = http or fake_valve.client()
        make_gc = make_gc or (lambda: fake_game_coordinator(codes))
    else:
        http = http or httpx.Client(timeout=s.http_timeout_seconds, follow_redirects=False)
        if make_gc is None:
            def make_gc():
                from .gc_steamio import SteamioGameCoordinator

                return SteamioGameCoordinator(refresh_token=s.steam_bot_refresh_token, username=s.steam_bot_username,
                                              password=s.steam_bot_password, shared_secret=s.steam_bot_shared_secret,
                                              ready_timeout=inputs.gc_timeout)
    if parser is None:
        from .demo_parser import Demoparser2Parser

        parser = Demoparser2Parser(isolation=s.demo_parse_isolation, timeout_seconds=s.demo_parse_timeout_seconds,
                                   threads=s.demo_parse_threads)
    scorer = load_scorer(inputs.model_path)
    workdir = tempfile.mkdtemp(prefix="csa-live-check-")
    run = Run(inputs=inputs, http=http, make_gc=make_gc, parser=parser, workdir=workdir, r=r)
    steps = (
        (2, "web_api_key", lambda: step_web_api_key(run)),
        (3, "history", lambda: step_history(run)),
        (4, "gc_locate", lambda: step_gc_locate(run)),
        (5, "download", lambda: step_download(run)),
        (6, "parse_score", lambda: step_parse_score(run, scorer)),
        (7, "store_report", lambda: step_store_report(run, scorer)),
    )
    total_started = time.monotonic()
    try:
        for index, key, fn in steps:
            if not inputs.fake and key in ("gc_locate", "download", "parse_score", "store_report"):
                r.running(index, key)  # can take a while live (GC login, big download, parse)
            try:
                message = fn()
            except Failed as exc:
                r.result(index, key, "FAIL", str(exc))
                skip_rest(index, f"blocked by step {index}")
                r.line(f"\nRESULT: FAIL at step {index} ({key}) after {time.monotonic() - total_started:.0f}s. "
                       f"See {CHECKLIST}")
                return 1
            except Exception as exc:  # unexpected: show the type only (messages may carry URLs with secrets)
                if args.debug:
                    raise
                r.result(index, key, "FAIL", f"unexpected {type(exc).__name__} (rerun with the API's logs / a debugger)")
                skip_rest(index, f"blocked by step {index}")
                r.line(f"\nRESULT: FAIL at step {index} ({key})")
                return 1
            r.result(index, key, "PASS", message)
        print_report(r, run.report)
        if run.summary is not None:
            print_summary(r, run.summary)
        if inputs.report_json:
            with open(inputs.report_json, "w", encoding="utf-8") as fh:
                json.dump(run.report, fh, indent=2)
            r.line(f"\nFull report JSON: {inputs.report_json}")
        mode = "FAKE (offline doubles for Valve + GC)" if inputs.fake else "LIVE"
        r.line(f"\nRESULT: PASS ({len(STEPS)}/{len(STEPS)} steps, {mode}, {time.monotonic() - total_started:.0f}s)")
        return 0
    finally:
        if args.keep_workdir:
            r.line(f"workdir kept: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:  # pragma: no cover
    return run_check()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
