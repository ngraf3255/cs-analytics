"""FastAPI routes for Steam sign-in, match-history access, sync and reports.

All routes except ``GET /steam/status`` return 503 ``steam_sync_disabled``
unless DATABASE_URL and TOKEN_ENCRYPTION_KEYS are configured.
State-changing routes require the ``X-Requested-With: csa`` header (forces a
CORS preflight) and, if an Origin header is sent, an allowed origin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import openid
from .config import Settings
from .crypto import AuthCodeCipher
from .scoring import RoundScorer
from .sessions import LOGIN_STATE_COOKIE, CookieSigner
from .sharecode import is_valid_share_code
from .storage.base import Storage, User
from .sync import SyncRejected, SyncService
from .valve import MatchHistoryClient, is_valid_auth_code

MODEL_NOTE = (
    "Retrospective estimate from a model trained on professional CS2 matches using only "
    "map, opening-kill side, timing and weapon. Not generated during the match and not "
    "calibrated for matchmaking skill levels."
)


@dataclass
class SteamContext:
    settings: Settings
    storage: Storage
    signer: CookieSigner
    cipher: AuthCodeCipher
    history: MatchHistoryClient
    sync: SyncService
    scorer: RoundScorer
    http: httpx.Client
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def _ctx(request: Request) -> SteamContext:
    ctx = getattr(request.app.state, "steam", None)
    if ctx is None:
        raise HTTPException(status_code=503, detail="steam_sync_disabled")
    return ctx


def _csrf(request: Request, ctx: SteamContext = Depends(_ctx)) -> None:
    if request.headers.get("x-requested-with") != "csa":
        raise HTTPException(status_code=403, detail="csrf_header_missing")
    origin = request.headers.get("origin")
    if origin is not None and origin not in ctx.settings.allowed_origins:
        raise HTTPException(status_code=403, detail="origin_not_allowed")


def _current_user(request: Request, ctx: SteamContext = Depends(_ctx)) -> User:
    token_hash = ctx.signer.session_token_hash(
        request.cookies.get(ctx.settings.session_cookie_name), ctx.settings.session_ttl_seconds
    )
    user = ctx.storage.get_session_user(token_hash, ctx.clock()) if token_hash else None
    if user is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    return user


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _match_access_view(ctx: SteamContext, user: User) -> dict:
    access = ctx.storage.get_match_access(user.id)
    if access is None:
        return {"linked": False, "auth_code_hint": None, "linked_at": None, "updated_at": None}
    return {
        "linked": True,
        "auth_code_hint": f"****-*****-{access.auth_code_last4}",
        "linked_at": _iso(access.consented_at),
        "updated_at": _iso(access.updated_at),
    }


def _sync_view(ctx: SteamContext, user: User) -> dict:
    state = ctx.storage.get_sync_state(user.id, ctx.clock())
    return {
        "status": "running" if state.locked else state.status,
        "last_started_at": _iso(state.last_started_at),
        "last_finished_at": _iso(state.last_finished_at),
        "last_error": state.last_error,
        "last_imported_count": state.last_imported_count,
    }


def _match_view(match) -> dict:
    return {
        "id": match.id,
        "source": match.source,  # how the match first arrived: upload | steam_sync
        "share_code": match.share_code if match.has_share_code else None,
        "status": match.status,
        "status_reason": match.status_reason,
        "map_name": match.map_name,
        "rounds_count": match.rounds_count,
        "imported_at": _iso(match.imported_at),
    }


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name, path="/", domain=settings.session_cookie_domain,
        secure=settings.session_cookie_secure, httponly=True, samesite=settings.session_cookie_samesite,
    )


class MatchAccessInput(BaseModel):
    auth_code: str
    share_code: str
    consent: bool


router = APIRouter()


@router.get("/steam/status")
def steam_status(request: Request) -> dict:
    return {"enabled": getattr(request.app.state, "steam", None) is not None}


@router.get("/auth/steam/login")
def steam_login(next: str | None = None, ctx: SteamContext = Depends(_ctx)) -> RedirectResponse:
    login_state, cookie = ctx.signer.new_login_state(next)
    url = openid.build_login_url(
        return_url=ctx.settings.openid_return_url, realm=ctx.settings.openid_realm, state=login_state.state
    )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        LOGIN_STATE_COOKIE, cookie, max_age=ctx.settings.login_state_ttl_seconds, path="/auth/steam",
        secure=ctx.settings.session_cookie_secure, httponly=True, samesite="lax",
    )
    return response


@router.get("/auth/steam/callback")
def steam_callback(request: Request, ctx: SteamContext = Depends(_ctx)) -> RedirectResponse:
    settings = ctx.settings
    login_state = ctx.signer.read_login_state(
        request.cookies.get(LOGIN_STATE_COOKIE), settings.login_state_ttl_seconds
    )
    try:
        if login_state is None:
            raise openid.OpenIDError("missing_login_state")
        steam_id = openid.verify_callback(
            dict(request.query_params), expected_state=login_state.state,
            return_url=settings.openid_return_url, http=ctx.http, now=ctx.clock(),
        )
    except openid.OpenIDError as exc:
        response = RedirectResponse(
            f"{settings.frontend_url}/?{urlencode({'steam_login': 'failed', 'reason': exc.reason})}", status_code=302
        )
        response.delete_cookie(LOGIN_STATE_COOKIE, path="/auth/steam")
        return response

    now = ctx.clock()
    user = ctx.storage.get_or_create_user(steam_id, now)
    cookie, token_hash = ctx.signer.new_session_cookie()
    ctx.storage.create_session(token_hash, user.id, now, now + timedelta(seconds=settings.session_ttl_seconds))
    response = RedirectResponse(f"{settings.frontend_url}{login_state.next_path}", status_code=302)
    response.delete_cookie(LOGIN_STATE_COOKIE, path="/auth/steam")
    response.set_cookie(
        settings.session_cookie_name, cookie, max_age=settings.session_ttl_seconds, path="/",
        domain=settings.session_cookie_domain, secure=settings.session_cookie_secure, httponly=True,
        samesite=settings.session_cookie_samesite,
    )
    return response


@router.post("/auth/logout", status_code=204, dependencies=[Depends(_csrf)])
def logout(request: Request, ctx: SteamContext = Depends(_ctx)) -> Response:
    token_hash = ctx.signer.session_token_hash(
        request.cookies.get(ctx.settings.session_cookie_name), ctx.settings.session_ttl_seconds
    )
    if token_hash:
        ctx.storage.delete_session(token_hash)
    response = Response(status_code=204)
    _clear_session_cookie(response, ctx.settings)
    return response


@router.get("/me")
def me(user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)) -> dict:
    return {
        "steam_id": user.steam_id,
        "created_at": _iso(user.created_at),
        "match_access": _match_access_view(ctx, user),
        "sync": _sync_view(ctx, user),
    }


@router.delete("/me", status_code=204, dependencies=[Depends(_csrf)])
def delete_me(user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)) -> Response:
    ctx.storage.delete_user(user.id)
    response = Response(status_code=204)
    _clear_session_cookie(response, ctx.settings)
    return response


@router.put("/steam/match-access", dependencies=[Depends(_csrf)])
def put_match_access(
    payload: MatchAccessInput, user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)
) -> dict:
    if not payload.consent:
        raise HTTPException(status_code=422, detail="consent_required")
    auth_code = payload.auth_code.strip().upper()
    share_code = payload.share_code.strip()
    if not is_valid_auth_code(auth_code):
        raise HTTPException(status_code=422, detail="invalid_auth_code_format")
    if not is_valid_share_code(share_code):
        raise HTTPException(status_code=422, detail="invalid_share_code_format")
    result = ctx.history.next_share_code(user.steam_id, auth_code, share_code)
    if result.status == "invalid_auth_code":
        raise HTTPException(status_code=422, detail="invalid_auth_code")
    if result.status == "invalid_known_code":
        raise HTTPException(status_code=422, detail="invalid_share_code")
    if result.status == "rate_limited":
        raise HTTPException(status_code=429, detail="valve_rate_limited")
    if result.status not in ("ok", "no_new_match"):
        raise HTTPException(status_code=502, detail="valve_unavailable")
    ctx.storage.set_match_access(
        user.id, ciphertext=ctx.cipher.encrypt(user.steam_id, auth_code), last4=auth_code[-4:],
        cursor_share_code=share_code, now=ctx.clock(),
    )
    return _match_access_view(ctx, user)


@router.delete("/steam/match-access", status_code=204, dependencies=[Depends(_csrf)])
def delete_match_access(user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)) -> Response:
    ctx.storage.delete_match_access(user.id)
    return Response(status_code=204)


@router.get("/steam/sync")
def get_sync(user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)) -> dict:
    return _sync_view(ctx, user)


@router.post("/steam/sync", dependencies=[Depends(_csrf)])
def post_sync(user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)) -> dict:
    try:
        outcome = ctx.sync.sync(user)
    except SyncRejected as exc:
        status = {"not_linked": 409, "already_running": 409, "too_soon": 429}[exc.reason]
        raise HTTPException(status_code=status, detail=exc.reason) from None
    return {
        "status": outcome.status,
        "imported": outcome.imported,
        "processed": outcome.processed,
        "has_more": outcome.has_more,
        "error": outcome.error,
    }


@router.get("/matches")
def list_matches(
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx),
) -> dict:
    matches = ctx.storage.list_matches(user.id, limit=limit, offset=offset)
    return {"matches": [_match_view(m) for m in matches], "limit": limit, "offset": offset}


@router.post("/matches/upload", dependencies=[Depends(_csrf)])
async def upload_demo(
    request: Request, share_code: str | None = None,
    user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx),
) -> dict:
    """Body: the raw ``.dem`` or ``.dem.bz2`` bytes (Content-Type: application/octet-stream).

    Optional ``?share_code=CSGO-...``: the match's sharing code. It lets a later
    Steam sync of the same match skip the download (dedupe by Valve match id);
    without it, dedupe relies on the demo's SHA-256.
    """

    import shutil
    import tempfile

    from .upload import UploadRejected, import_uploaded_demo

    share_code = (share_code or "").strip() or None
    if share_code and not is_valid_share_code(share_code):
        raise HTTPException(status_code=422, detail="invalid_share_code_format")
    # A plain .dem body is the demo itself, so it is also bound by the decompressed limit.
    limit = min(ctx.settings.upload_max_bytes, ctx.settings.demo_max_decompressed_bytes)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="demo_too_large")
    workdir = tempfile.mkdtemp(prefix="csa-upload-")
    try:
        raw_path = f"{workdir}/upload.bin"
        written = 0
        with open(raw_path, "wb") as out:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise HTTPException(status_code=413, detail="demo_too_large")
                out.write(chunk)
        if written == 0:
            raise HTTPException(status_code=422, detail="not_a_cs2_demo")
        try:
            result = await run_in_threadpool(
                import_uploaded_demo, storage=ctx.storage, parser=ctx.sync.parser, user=user, raw_path=raw_path,
                workdir=workdir, max_compressed_bytes=ctx.settings.demo_max_download_bytes,
                max_demo_bytes=ctx.settings.demo_max_decompressed_bytes, now=ctx.clock(), share_code=share_code,
            )
        except UploadRejected as exc:
            raise HTTPException(status_code=exc.status, detail=exc.reason) from None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    found = ctx.storage.get_match(user.id, result.match_id)
    assert found is not None
    return {"match": _match_view(found[0]), "created": result.created}


@router.get("/matches/{match_id}")
def match_report(match_id: str, user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)) -> dict:
    found = ctx.storage.get_match(user.id, match_id)
    if found is None:
        raise HTTPException(status_code=404, detail="match_not_found")
    match, rounds = found
    scores = ctx.scorer.score_rounds(match.map_name, rounds)
    round_views, scored, correct = [], 0, 0
    for rnd, score in zip(rounds, scores):
        prediction = None
        if score.unscored_reason is None:
            scored += 1
            correct += int(score.predicted_winner == rnd.winner_side)
            prediction = {
                "predicted_winner": score.predicted_winner,
                "probabilities": {"ct": score.probability_ct, "t": score.probability_t},
            }
        round_views.append({
            "round_number": rnd.round_number,
            "actual_winner": rnd.winner_side,
            "opening_kill": None if rnd.opening_kill_seconds is None and rnd.opening_weapon is None else {
                "side": rnd.opening_kill_side, "seconds": rnd.opening_kill_seconds, "weapon": rnd.opening_weapon,
            },
            "prediction": prediction,
            "unscored_reason": score.unscored_reason,
        })
    return {
        "match": _match_view(match),
        "model": {"calibrated_for_matchmaking": False, "note": MODEL_NOTE},
        "summary": {"rounds": len(rounds), "scored": scored, "correct_predictions": correct},
        "rounds": round_views,
    }


def build_demo_locator(settings: Settings):
    """GC-backed locator when bot credentials are set; otherwise the disabled stub."""

    from .valve import UnconfiguredDemoLocator

    if not settings.demo_bot_configured:
        return UnconfiguredDemoLocator()
    from .gc import GameCoordinatorDemoLocator
    from .gc_steamio import SteamioGameCoordinator

    return GameCoordinatorDemoLocator(SteamioGameCoordinator(
        refresh_token=settings.steam_bot_refresh_token,
        username=settings.steam_bot_username,
        password=settings.steam_bot_password,
        shared_secret=settings.steam_bot_shared_secret,
    ))


def build_steam_context(settings: Settings, scorer: RoundScorer) -> SteamContext:
    """Build production wiring. Creating the engine does not open a DB connection."""

    from .demo_parser import Demoparser2Parser
    from .storage.sql import SqlStorage, make_engine
    from .valve import DemoFetcher, SteamWebMatchHistoryClient

    http = httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=False)
    storage = SqlStorage(make_engine(settings.database_url))
    cipher = AuthCodeCipher(settings.token_encryption_keys)
    history = SteamWebMatchHistoryClient(settings.steam_web_api_key, http)
    clock = lambda: datetime.now(timezone.utc)  # noqa: E731
    sync = SyncService(
        storage=storage, history=history, locator=build_demo_locator(settings),
        fetcher=DemoFetcher(http, max_download_bytes=settings.demo_max_download_bytes,
                            max_decompressed_bytes=settings.demo_max_decompressed_bytes),
        parser=Demoparser2Parser(), cipher=cipher, clock=clock,
        max_matches=settings.sync_max_matches_per_request, lock_ttl_seconds=settings.sync_lock_ttl_seconds,
        min_interval_seconds=settings.sync_min_interval_seconds,
    )
    return SteamContext(settings=settings, storage=storage, signer=CookieSigner(settings.session_secret),
                        cipher=cipher, history=history, sync=sync, scorer=scorer, http=http, clock=clock)


def register(app: FastAPI, ctx: SteamContext | None) -> None:
    app.state.steam = ctx
    app.include_router(router)
