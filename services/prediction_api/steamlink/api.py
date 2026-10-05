"""FastAPI routes for Steam sign-in, match-history access, sync and reports.

All routes except ``GET /steam/status`` return 503 ``steam_sync_disabled``
unless DATABASE_URL and TOKEN_ENCRYPTION_KEYS are configured.
State-changing routes require the ``X-Requested-With: csa`` header (forces a
CORS preflight) and, if an Origin header is sent, an allowed origin.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import openid
from .config import Settings
from .crypto import AuthCodeCipher
from .jobs import UploadJobWorker
from .scoring import RoundScorer
from .sessions import LOGIN_STATE_COOKIE, CookieSigner
from .sharecode import is_valid_share_code
from .storage.base import JOB_KIND_SYNC, JOB_KIND_UPLOAD, Storage, User
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
    jobs: "UploadJobWorker | None" = None  # set by register()


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


def _sync_view(ctx: SteamContext, user: User, jobs: list | None = None) -> dict:
    state = ctx.storage.get_sync_state(user.id, ctx.clock())
    if jobs is None:
        jobs = ctx.storage.list_upload_jobs(user.id, limit=SYNC_JOBS_SHOWN, kind=JOB_KIND_SYNC)
    return {
        # running: the request is walking the history; matches it queued are in active_jobs
        "status": "running" if state.locked else state.status,
        "last_started_at": _iso(state.last_started_at),
        "last_finished_at": _iso(state.last_finished_at),
        "last_error": state.last_error,
        "last_imported_count": state.last_imported_count,  # matches queued by the last sync
        "active_jobs": sum(job.active for job in jobs),
    }


SYNC_JOBS_SHOWN = 10


def _match_view(match) -> dict:
    return {
        "id": match.id,
        "source": match.source,  # how the match arrived for this user: upload | steam_sync
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
    """Sync status plus the user's recent sync jobs (newest first): the UI polls this
    while matches download / parse in the background, and after a reload."""

    jobs = ctx.storage.list_upload_jobs(user.id, limit=SYNC_JOBS_SHOWN, kind=JOB_KIND_SYNC)
    if any(job.active for job in jobs):
        ctx.jobs.start()  # self-heal, as for uploads
    return {**_sync_view(ctx, user, jobs), "jobs": [_job_view(ctx, job) for job in jobs]}


@router.post("/steam/sync", dependencies=[Depends(_csrf)])
def post_sync(
    response: Response, user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx),
) -> dict:
    """Walk the share-code history from the cursor and queue one background job per
    new match (known matches are skipped without a download). Answers quickly:
    202 if any job is queued, else 200. Poll ``GET /steam/sync`` (all sync jobs) or
    ``GET /matches/upload/{job_id}`` for progress.

    ``status``: up_to_date | partial (more history; sync again) | queue_full (the
    server's job queue is full; sync again later) | error (see ``error``)."""

    try:
        outcome = ctx.sync.sync(user, max_active=ctx.settings.upload_queue_max, job_file=ctx.jobs.job_file)
    except SyncRejected as exc:
        status = {"not_linked": 409, "already_running": 409, "too_soon": 429}[exc.reason]
        raise HTTPException(status_code=status, detail=exc.reason) from None
    jobs = [job for job in (ctx.storage.get_upload_job(user.id, job_id) for job_id in outcome.job_ids) if job]
    if any(job.active for job in jobs):
        ctx.jobs.start()
        response.status_code = 202
    return {
        "status": outcome.status,
        "queued": outcome.queued,
        "skipped": outcome.skipped,
        "attached": outcome.attached,
        "processed": outcome.processed,
        "has_more": outcome.has_more,
        "error": outcome.error,
        "jobs": [_job_view(ctx, job) for job in jobs],
    }


@router.get("/matches")
def list_matches(
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx),
) -> dict:
    matches = ctx.storage.list_matches(user.id, limit=limit, offset=offset)
    return {"matches": [_match_view(m) for m in matches], "limit": limit, "offset": offset}


def _job_view(ctx: SteamContext, job) -> dict:
    match = None
    if job.match_id:
        found = ctx.storage.get_match(job.user_id, job.match_id)
        match = _match_view(found[0]) if found else None
    return {
        "id": job.id,
        "kind": job.kind,  # upload | steam_sync
        # steam_sync: the match being imported; upload: the share code the user gave, if any
        "share_code": job.share_code,
        "status": job.status,  # queued | processing | done | failed
        # while processing: [steam_sync: locating | downloading |] decompressing | hashing | parsing | storing
        "stage": job.stage,
        "progress": job.progress,  # 0..1 within the stage when known (downloading, decompressing), else null
        "queue_position": ctx.storage.upload_jobs_ahead(job) if job.status == "queued" else None,
        "error": job.error,
        "size_bytes": job.size_bytes,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "match": match,
        "created": job.match_created,  # False: the demo / match was already stored (dedupe)
        "attempts": job.attempts,
    }


@router.post("/matches/upload", dependencies=[Depends(_csrf)])
async def upload_demo(
    request: Request, response: Response, share_code: str | None = None,
    user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx),
) -> dict:
    """Body: the raw ``.dem`` or ``.dem.bz2`` bytes (Content-Type: application/octet-stream).

    Optional ``?share_code=CSGO-...``: the match's sharing code. It lets a later
    Steam sync of the same match skip the download (dedupe by Valve match id);
    without it, dedupe relies on the demo's SHA-256.

    Returns ``{"job": ...}`` (see ``GET /matches/upload/{job_id}``): 202 with a
    queued job that a background worker parses (steamlink.jobs), or 200 with a
    finished job when the demo / share code is already stored (no parse).
    """

    import hashlib
    import uuid

    from .sharecode import decode
    from .storage.base import UNKNOWN_MATCH_ID, UploadJob
    from .upload import sniff_demo

    settings = ctx.settings
    share_code = (share_code or "").strip() or None
    if share_code and not is_valid_share_code(share_code):
        raise HTTPException(status_code=422, detail="invalid_share_code_format")
    # A plain .dem body is the demo itself, so it is also bound by the decompressed limit.
    limit = min(settings.upload_max_bytes, settings.demo_max_decompressed_bytes)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="demo_too_large")
    if len(ctx.storage.list_active_upload_jobs()) >= settings.upload_queue_max:
        raise HTTPException(status_code=429, detail="upload_queue_full")  # before reading the body

    job_id = uuid.uuid4().hex
    raw_path = ctx.jobs.job_file(job_id)
    queued = False
    try:
        written, head, hasher = 0, b"", hashlib.sha256()
        with open(raw_path, "wb") as out:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise HTTPException(status_code=413, detail="demo_too_large")
                if len(head) < 8:
                    head = (head + chunk)[:8]
                if hasher is not None:
                    # Hash a plain .dem while it arrives (saves a re-read on a slow CPU);
                    # an archive's hash is no dedupe key.
                    if len(head) >= 3 and sniff_demo(head) == "bz2":
                        hasher = None
                    else:
                        hasher.update(chunk)
                out.write(chunk)
        kind = sniff_demo(head)
        if kind is None:
            raise HTTPException(status_code=422, detail="not_a_cs2_demo")
        if kind == "bz2" and written > settings.demo_max_download_bytes:
            raise HTTPException(status_code=413, detail="demo_too_large")
        digest = hasher.hexdigest() if kind == "dem" and hasher is not None else None
        now = ctx.clock()
        valve_match_id = str(decode(share_code).match_id) if share_code else UNKNOWN_MATCH_ID
        known = None
        if digest or share_code:
            # Already stored (same .dem, by this or another user; or this user's match by share
            # code): added to the user's list if needed, no parse, finished job.
            known = ctx.storage.claim_known_match(
                user.id, share_code=share_code, valve_match_id=valve_match_id, demo_sha256=digest,
                share_code_verified=False, source="upload", now=now)
        if known is not None:
            record, added = known
            job = UploadJob(id=job_id, user_id=user.id, status="done", demo_path=raw_path, size_bytes=written,
                            created_at=now, updated_at=now, share_code=share_code, demo_sha256=digest,
                            match_id=record.id, match_created=added, finished_at=now)
            ctx.storage.create_upload_job(job)
            response.status_code = 200
            return {"job": _job_view(ctx, job)}
        job = UploadJob(id=job_id, user_id=user.id, status="queued", demo_path=raw_path, size_bytes=written,
                        created_at=now, updated_at=now, share_code=share_code, demo_sha256=digest)
        if not ctx.storage.create_upload_job(job, max_active=settings.upload_queue_max):
            raise HTTPException(status_code=429, detail="upload_queue_full")
        queued = True
    finally:
        if not queued:
            try:
                os.remove(raw_path)
            except OSError:
                pass
    ctx.jobs.start()
    response.status_code = 202
    return {"job": _job_view(ctx, ctx.storage.get_upload_job(user.id, job_id) or job)}


@router.get("/matches/upload")
def list_upload_jobs(
    limit: int = Query(10, ge=1, le=50), kind: str = Query(JOB_KIND_UPLOAD, pattern="^(upload|steam_sync|all)$"),
    user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx),
) -> dict:
    """The user's recent uploads (``?kind=steam_sync`` / ``all`` for sync jobs), newest
    first (lets the UI resume showing a running parse)."""

    jobs = ctx.storage.list_upload_jobs(user.id, limit=limit, kind=None if kind == "all" else kind)
    if any(job.active for job in jobs):
        ctx.jobs.start()  # self-heal: e.g. the worker stopped after a database outage
    return {"jobs": [_job_view(ctx, job) for job in jobs]}


@router.get("/matches/upload/{job_id}")
def get_upload_job(job_id: str, user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)) -> dict:
    job = ctx.storage.get_upload_job(user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="upload_job_not_found")
    if job.active:
        ctx.jobs.start()
    return {"job": _job_view(ctx, job)}


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
        parser=Demoparser2Parser(isolation=settings.demo_parse_isolation,
                                 timeout_seconds=settings.demo_parse_timeout_seconds,
                                 threads=settings.demo_parse_threads),
        cipher=cipher, clock=clock,
        max_matches=settings.sync_max_matches_per_request, lock_ttl_seconds=settings.sync_lock_ttl_seconds,
        min_interval_seconds=settings.sync_min_interval_seconds, max_job_attempts=settings.sync_job_max_attempts,
    )
    return SteamContext(settings=settings, storage=storage, signer=CookieSigner(settings.session_secret),
                        cipher=cipher, history=history, sync=sync, scorer=scorer, http=http, clock=clock)


def register(app: FastAPI, ctx: SteamContext | None) -> None:
    if ctx is not None and ctx.jobs is None:
        ctx.jobs = UploadJobWorker(ctx)
    app.state.steam = ctx
    app.include_router(router)


def start_background_work(app: FastAPI) -> None:
    """At app startup: resume / clean up upload and sync jobs a previous process left behind."""

    ctx = getattr(app.state, "steam", None)
    if ctx is not None and ctx.jobs is not None:
        ctx.jobs.start()
