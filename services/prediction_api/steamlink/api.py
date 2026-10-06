"""FastAPI routes for accounts (Steam or guest), demo upload, match reports and Steam sync.

Two feature levels (see steamlink.config): with DATABASE_URL + SESSION_SECRET the
account, upload and report routes work (guests via ``POST /auth/guest`` only with GUEST_UPLOADS=true; off by default); Steam
sign-in, match-history linking and sync additionally need TOKEN_ENCRYPTION_KEYS +
STEAM_WEB_API_KEY. A route whose feature is off returns 503 ``steam_sync_disabled``
(``GET /steam/status`` says which features are on).
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
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from . import openid
from .autosync import AutoSyncScheduler
from .analytics import RECENT_DEFAULT, SIDES, build_user_summary, match_date, match_result
from .config import Settings
from .crypto import AuthCodeCipher, DecryptionError
from . import export as tableau_export
from .jobs import UploadJobWorker
from .scoring import RoundScorer
from .sessions import LOGIN_STATE_COOKIE, CookieSigner
from .sharecode import extract_share_code, is_valid_share_code
from .storage.base import JOB_KIND_SYNC, JOB_KIND_UPLOAD, Storage, User
from .sync import RELINK_ERRORS, SyncRejected, SyncService, relink_needed  # noqa: F401
from .valve import MatchHistoryClient, is_valid_auth_code, normalize_auth_code

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
    cipher: AuthCodeCipher | None  # None: Steam off (no TOKEN_ENCRYPTION_KEYS / STEAM_WEB_API_KEY)
    history: MatchHistoryClient | None
    sync: SyncService
    scorer: RoundScorer
    http: httpx.Client
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    jobs: "UploadJobWorker | None" = None  # set by register()
    auto_sync: "AutoSyncScheduler | None" = None  # set by register() when Steam is on

    @property
    def steam_enabled(self) -> bool:
        return self.cipher is not None and self.history is not None and self.settings.steam_enabled

    @property
    def guest_uploads_enabled(self) -> bool:
        return self.settings.guest_uploads_enabled


GUEST_PREFIX = "guest:"  # users.steam_id of a guest account (never a real SteamID64)


def is_guest(user: User) -> bool:
    return user.steam_id.startswith(GUEST_PREFIX)


def _steam_id(user: User) -> str | None:
    """The user's SteamID64 for the personal ("you") analytics; None for guests."""
    return None if is_guest(user) else user.steam_id


def _ctx(request: Request) -> SteamContext:
    ctx = getattr(request.app.state, "steam", None)
    if ctx is None:
        raise HTTPException(status_code=503, detail="steam_sync_disabled")
    return ctx


def _steam(ctx: SteamContext = Depends(_ctx)) -> SteamContext:
    """Steam-only routes (sign-in, match history, sync): 503 while Steam is not configured."""
    if not ctx.steam_enabled:
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


def _steam_user(user: User = Depends(_current_user), ctx: SteamContext = Depends(_steam)) -> User:
    """A Steam-signed-in user on a server with Steam on (guests get 403 steam_sign_in_required)."""
    if is_guest(user):
        raise HTTPException(status_code=403, detail="steam_sign_in_required")
    return user


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _needs_relink(ctx: SteamContext, user: User, access) -> dict | None:
    """See :func:`steamlink.sync.relink_needed`."""

    return relink_needed(ctx.storage.get_sync_state(user.id, ctx.clock()), access)


def _match_access_view(ctx: SteamContext, user: User) -> dict:
    access = ctx.storage.get_match_access(user.id)
    if access is None:
        return {"linked": False, "auth_code_hint": None, "linked_at": None, "updated_at": None, "needs_relink": None,
                "awaiting_share_code": False}
    return {
        "linked": True,
        "auth_code_hint": f"****-*****-{access.auth_code_last4}",
        "linked_at": _iso(access.consented_at),
        "updated_at": _iso(access.updated_at),
        "needs_relink": _needs_relink(ctx, user, access),
        # Auth code saved, no share code yet (linked before a recent match): sync starts once
        # the user adds the share code of a match they played.
        "awaiting_share_code": access.cursor_share_code is None,
    }


def _auto_sync_view(ctx: SteamContext, user: User, state=None) -> dict:
    """The user's automatic background sync (steamlink.autosync). ``enabled``: the user's
    toggle; ``active``: it will actually run for them; else ``paused_reason`` says why:
    turned_off | not_linked | needs_share_code | needs_relink | server_disabled |
    demo_retrieval_not_configured."""

    sched = ctx.auto_sync
    state = state or ctx.storage.get_sync_state(user.id, ctx.clock())
    access = ctx.storage.get_match_access(user.id)
    if sched is None or not sched.enabled:
        reason = "server_disabled"
    elif not sched.available:
        reason = "demo_retrieval_not_configured"
    elif not user.auto_sync_enabled:
        reason = "turned_off"
    elif access is None:
        reason = "not_linked"
    elif access.cursor_share_code is None:
        reason = "needs_share_code"
    elif relink_needed(state, access) is not None:
        reason = "needs_relink"
    else:
        reason = None
    return {
        "enabled": user.auto_sync_enabled,
        "active": reason is None,
        "paused_reason": reason,
        "interval_seconds": sched.interval_seconds if sched is not None and sched.enabled else None,
        # earliest time the next automatic sync runs (null while paused)
        "next_at": _iso(sched.next_at(user, state)) if reason is None else None,
        "last_run_at": _iso(state.last_auto_sync_at),
        "last_error": state.last_auto_sync_error,  # error of the last automatic run (null: OK)
        "failures": state.auto_sync_failures,  # consecutive failed syncs (backoff)
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
        "last_synced_at": _iso(state.last_synced_at),  # last sync (manual or automatic) that finished OK
        "last_error": state.last_error,
        "last_imported_count": state.last_imported_count,  # matches queued by the last sync
        "active_jobs": sum(job.active for job in jobs),
        "auto_sync": _auto_sync_view(ctx, user, state),
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
        "imported_at": _iso(match.imported_at),  # when it was added to this user's list
        # When the match was played if known (Steam sync: Game Coordinator), else imported_at:
        # date_source says which ("played" | "imported"); played_at is null when unknown.
        **match_date(match),
        # Final score: rounds won by the team on each side at the end (null if unknown).
        "score": None if match.score_ct is None or match.score_t is None else {"ct": match.score_ct, "t": match.score_t},
        "players_recorded": match.players_recorded,  # per-player sides/stats stored for this match
        # Parsed by an older parser (null: up to date). The demo is not kept on the server,
        # so the way to refresh it is to upload the same demo again (fix: "reupload").
        "outdated": None if match.outdated_reason is None else {"reason": match.outdated_reason, "fix": "reupload"},
    }


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name, path="/", domain=settings.session_cookie_domain,
        secure=settings.session_cookie_secure, httponly=True, samesite=settings.session_cookie_samesite,
    )


class AutoSyncInput(BaseModel):
    enabled: bool


class MatchAccessInput(BaseModel):
    # Empty while already linked: keep the stored Game Authentication Code and only replace the
    # share code (Leetify-style: the auth code is given once, a fresh share code when the old one expired).
    auth_code: str = ""
    # Optional: users who haven't played a match recently have no share code yet. Empty saves
    # the auth code alone (or keeps the stored share code) and sync waits for a share code.
    share_code: str = ""
    consent: bool


router = APIRouter()


@router.get("/steam/status")
def steam_status(request: Request) -> dict:
    """Which features this server has on. ``enabled``: Steam sign-in + sync (kept for older
    web builds); ``upload``: demo upload + match reports for signed-in users; ``guest``:
    ``POST /auth/guest`` works (upload without Steam)."""

    ctx = getattr(request.app.state, "steam", None)
    if ctx is None:
        return {"enabled": False, "steam": False, "upload": False, "guest": False}
    sched = ctx.auto_sync
    steam = ctx.steam_enabled
    guest = ctx.guest_uploads_enabled
    return {"enabled": steam, "steam": steam, "upload": steam or guest, "guest": guest, "auto_sync": {
        "enabled": sched is not None and sched.enabled, "available": sched is not None and sched.available,
        "interval_seconds": sched.interval_seconds if sched is not None and sched.enabled else None,
    }}


@router.get("/auth/steam/login")
def steam_login(next: str | None = None, ctx: SteamContext = Depends(_steam)) -> RedirectResponse:
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
def steam_callback(request: Request, ctx: SteamContext = Depends(_steam)) -> RedirectResponse:
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


@router.post("/auth/guest", dependencies=[Depends(_csrf)])
def guest_login(request: Request, ctx: SteamContext = Depends(_ctx)) -> dict:
    """Start a guest session: upload demos and view their reports without Steam. The
    account lives as long as its session cookie (SESSION_TTL_SECONDS) on this browser.
    Already signed in (Steam or guest): keeps that session. 503 when GUEST_UPLOADS is off."""

    if not ctx.guest_uploads_enabled:
        raise HTTPException(status_code=503, detail="guest_uploads_disabled")
    settings = ctx.settings
    token_hash = ctx.signer.session_token_hash(request.cookies.get(settings.session_cookie_name),
                                               settings.session_ttl_seconds)
    existing = ctx.storage.get_session_user(token_hash, ctx.clock()) if token_hash else None
    if existing is not None:
        return me(existing, ctx)

    import uuid

    now = ctx.clock()
    user = ctx.storage.get_or_create_user(GUEST_PREFIX + uuid.uuid4().hex, now)
    cookie, token_hash = ctx.signer.new_session_cookie()
    ctx.storage.create_session(token_hash, user.id, now, now + timedelta(seconds=settings.session_ttl_seconds))
    response = JSONResponse(me(user, ctx))
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
    guest = is_guest(user)
    return {
        "steam_id": None if guest else user.steam_id,
        # "guest": a browser-only account from POST /auth/guest (upload + reports, no Steam)
        "account": "guest" if guest else "steam",
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
    payload: MatchAccessInput, user: User = Depends(_steam_user), ctx: SteamContext = Depends(_steam)
) -> dict:
    """Link (or re-link) match history. Accepts what users paste: the auth code in any case,
    with spaces or without dashes; the share code bare or inside CS2's steam:// share link.
    An empty ``auth_code`` while linked keeps the stored one (share code update only).
    An empty ``share_code`` saves the auth code without one (nobody needs a recent match to
    link; sync starts once a share code is added) or, if one is stored, keeps it.
    Each 422 ``detail`` names the field to fix (see the web's LinkForm)."""

    if not payload.consent:
        raise HTTPException(status_code=422, detail="consent_required")
    access = ctx.storage.get_match_access(user.id)
    share_given = bool(payload.share_code.strip())
    share_code = (extract_share_code(payload.share_code) or payload.share_code.strip()) if share_given else None
    if not payload.auth_code.strip():
        if access is None:
            raise HTTPException(status_code=422, detail="auth_code_required")
        if not share_given:
            raise HTTPException(status_code=422, detail="share_code_required")  # nothing to change
        try:
            auth_code = ctx.cipher.decrypt(user.steam_id, access.auth_code_ciphertext)
        except DecryptionError:
            raise HTTPException(status_code=422, detail="credentials_unreadable") from None
    else:
        auth_code = normalize_auth_code(payload.auth_code)
        if not is_valid_auth_code(auth_code):
            # e.g. the two codes pasted into each other's box
            detail = "auth_code_is_share_code" if extract_share_code(payload.auth_code) else "invalid_auth_code_format"
            raise HTTPException(status_code=422, detail=detail)
    if share_code is not None and not is_valid_share_code(share_code):
        detail = ("share_code_is_auth_code" if is_valid_auth_code(normalize_auth_code(payload.share_code))
                  else "invalid_share_code_format")
        raise HTTPException(status_code=422, detail=detail)
    if share_code is None and access is not None:
        share_code = access.cursor_share_code  # new auth code only: keep the stored share code (if any)
    if share_code is not None:
        # Valve checks both codes together; without any share code there is nothing to check
        # yet (the first sync after a share code is added does).
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
def delete_match_access(user: User = Depends(_steam_user), ctx: SteamContext = Depends(_steam)) -> Response:
    ctx.storage.delete_match_access(user.id)
    return Response(status_code=204)


@router.put("/steam/auto-sync", dependencies=[Depends(_csrf)])
def put_auto_sync(
    payload: AutoSyncInput, user: User = Depends(_steam_user), ctx: SteamContext = Depends(_steam),
) -> dict:
    """Turn automatic background sync on or off for the signed-in user (on by default).
    Returns the new ``auto_sync`` view (as in ``GET /steam/sync``)."""

    from dataclasses import replace

    ctx.storage.set_auto_sync_enabled(user.id, payload.enabled, ctx.clock())
    return _auto_sync_view(ctx, replace(user, auto_sync_enabled=payload.enabled))


@router.get("/steam/sync")
def get_sync(user: User = Depends(_steam_user), ctx: SteamContext = Depends(_steam)) -> dict:
    """Sync status plus the user's recent sync jobs (newest first): the UI polls this
    while matches download / parse in the background, and after a reload."""

    jobs = ctx.storage.list_upload_jobs(user.id, limit=SYNC_JOBS_SHOWN, kind=JOB_KIND_SYNC)
    if any(job.active for job in jobs):
        ctx.jobs.start()  # self-heal, as for uploads
    return {**_sync_view(ctx, user, jobs), "jobs": [_job_view(ctx, job) for job in jobs]}


@router.post("/steam/sync", dependencies=[Depends(_csrf)])
def post_sync(
    response: Response, user: User = Depends(_steam_user), ctx: SteamContext = Depends(_steam),
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
        status = {"not_linked": 409, "needs_share_code": 409, "already_running": 409, "too_soon": 429}[exc.reason]
        raise HTTPException(status_code=status, detail=exc.reason) from None
    if ctx.auto_sync is not None:
        try:
            ctx.auto_sync.after_sync(user.id, outcome, automatic=False)  # next automatic sync from now
        except Exception:  # scheduling is best effort; the sync itself succeeded
            import logging

            logging.getLogger(__name__).exception("could not schedule auto sync for user %s", user.id)
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
        # True: the match was stored by an older parser and this job re-parsed it (now up to date)
        "updated": bool(job.match_updated),
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
    share_code = extract_share_code(share_code) or (share_code or "").strip() or None
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
            # code): added to the user's list if needed, no parse, finished job. Unless it was
            # stored by an older parser (outdated): then the job parses it once more.
            known = ctx.storage.claim_known_match(
                user.id, share_code=share_code, valve_match_id=valve_match_id, demo_sha256=digest,
                share_code_verified=False, source="upload", now=now)
        if known is not None and known[0].outdated_reason is None:
            record, added = known
            job = UploadJob(id=job_id, user_id=user.id, status="done", demo_path=raw_path, size_bytes=written,
                            created_at=now, updated_at=now, share_code=share_code, demo_sha256=digest,
                            match_id=record.id, match_created=added, finished_at=now)
            ctx.storage.create_upload_job(job)
            response.status_code = 200
            return {"job": _job_view(ctx, job)}
        # An outdated known match was just added to the user's list here (claim above), so the
        # job's re-parse finds it already listed: remember that it is new to the user.
        job = UploadJob(id=job_id, user_id=user.id, status="queued", demo_path=raw_path, size_bytes=written,
                        created_at=now, updated_at=now, share_code=share_code, demo_sha256=digest,
                        match_created=True if known is not None and known[1] else None)
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


@router.get("/matches/summary")
def matches_summary(
    recent: int = Query(RECENT_DEFAULT, ge=1, le=50),
    user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx),
) -> dict:
    """Analytics across all the user's previous matches and rounds (see
    steamlink.analytics): totals, the round-win model's hit rate / Brier score /
    calibration over previous rounds, CT vs T round wins per map, opening-kill
    conversion and recent form (last ``recent`` matches vs the ones before).
    Registered before ``/matches/{match_id}`` so "summary" is not taken for an id."""

    summary = build_user_summary(ctx.storage, ctx.scorer, user.id, steam_id=_steam_id(user), recent=recent)
    summary["model"]["note"] = MODEL_NOTE
    return summary


@router.get("/matches/export/{table}.csv")
def export_csv(table: str, user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)) -> Response:
    """Tableau-ready CSV of the signed-in user's own match list (steamlink.export):
    ``rounds`` (one row per round of every imported match) or ``matches`` (one row per
    match). Only matches in the user's list (uploads, Steam sync, shared matches) are
    included; ``you_*`` columns are the user's own side / stats. Streamed as UTF-8 CSV.
    Registered before ``/matches/{match_id}``."""

    if table not in tableau_export.TABLES:
        raise HTTPException(status_code=404, detail="export_not_found")
    columns, rows = tableau_export.TABLES[table]
    stamp = ctx.clock().astimezone(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        tableau_export.csv_chunks(columns, rows(ctx.storage, ctx.scorer, user.id, _steam_id(user))),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="cs2-{table}-{stamp}.csv"',
            "Cache-Control": "no-store",
            "X-Export-Version": str(tableau_export.EXPORT_VERSION),
        },
    )


@router.get("/matches/{match_id}")
def match_report(match_id: str, user: User = Depends(_current_user), ctx: SteamContext = Depends(_ctx)) -> dict:
    report = build_match_report(ctx.storage, ctx.scorer, user.id, match_id, steam_id=_steam_id(user))
    if report is None:
        raise HTTPException(status_code=404, detail="match_not_found")
    return report


def build_match_report(storage: Storage, scorer: RoundScorer, user_id: str, match_id: str, *,
                       steam_id: str | None = None) -> dict | None:
    """The per-round analytics report of ``GET /matches/{match_id}`` (None: not in the
    user's list). Also used by ``python -m steamlink.live_check``. ``steam_id``: the
    signed-in player, whose side / stats each round are added as ``you``."""

    found = storage.get_match(user_id, match_id)
    if found is None:
        return None
    match, rounds = found
    mine = {r.round_number: r for r in storage.get_player_rounds(match_id, steam_id)} if steam_id else {}
    scores = scorer.score_rounds(match.map_name, rounds)
    round_views, scored, correct = [], 0, 0
    for rnd, score in zip(rounds, scores):
        record = mine.get(rnd.round_number)
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
            # The signed-in player's round (null: not in this round / match, or not recorded).
            "you": None if record is None else {
                "side": record.side,
                "won": None if rnd.winner_side not in SIDES else rnd.winner_side == record.side,
                "kills": record.kills, "deaths": record.deaths, "opening_kill": record.opening_kill,
                "opening_death": record.opening_death, "survived": record.survived,
                "win_probability": None if prediction is None else prediction["probabilities"][record.side],
            },
        })
    report = {
        "match": _match_view(match),
        "model": {"calibrated_for_matchmaking": False, "note": MODEL_NOTE},
        "summary": {"rounds": len(rounds), "scored": scored, "correct_predictions": correct},
        "rounds": round_views,
    }
    if steam_id is not None:
        report["you"] = _you_in_match(match, rounds, [mine[n] for n in sorted(mine)], steam_id)
    return report


def _you_in_match(match, rounds, mine, steam_id: str) -> dict:
    """The signed-in player's summary of one match. ``status``: ``in_match``,
    ``not_in_match`` (e.g. an uploaded pro demo) or ``unknown`` (parsed before
    per-player rounds were recorded: re-upload the demo to fill it in)."""

    if not mine:
        return {"status": "not_in_match" if match.players_recorded else "unknown", "steam_id": steam_id}
    winners = {r.round_number: r.winner_side for r in rounds}
    decided = [r for r in mine if winners.get(r.round_number) in SIDES]
    won = sum(1 for r in decided if winners[r.round_number] == r.side)
    kills, deaths = sum(r.kills for r in mine), sum(r.deaths for r in mine)
    return {
        "status": "in_match", "steam_id": steam_id, "first_side": mine[0].side, "last_side": mine[-1].side,
        "rounds": len(mine), "won": won, "win_rate": round(won / len(decided), 4) if decided else None,
        "kills": kills, "deaths": deaths,
        "kd": round(kills / deaths, 2) if deaths else (float(kills) if kills else None),
        "opening_kills": sum(r.opening_kill for r in mine), "opening_deaths": sum(r.opening_death for r in mine),
        "survived": sum(r.survived for r in mine),
        **match_result(match, mine),
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
    """Build production wiring (needs ``settings.demo_upload_enabled``). The Steam pieces
    (auth-code cipher, Valve match-history client) are only built when ``settings.steam_enabled``;
    without them the upload / report routes work and the Steam routes answer 503.
    Creating the engine does not open a DB connection."""

    from .demo_parser import Demoparser2Parser
    from .storage.sql import SqlStorage, make_engine
    from .valve import DemoFetcher, SteamWebMatchHistoryClient, UnconfiguredDemoLocator

    http = httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=False)
    storage = SqlStorage(make_engine(settings.database_url))
    cipher = AuthCodeCipher(settings.token_encryption_keys) if settings.steam_enabled else None
    history = SteamWebMatchHistoryClient(settings.steam_web_api_key, http) if settings.steam_enabled else None
    clock = lambda: datetime.now(timezone.utc)  # noqa: E731
    sync = SyncService(
        storage=storage, history=history,
        locator=build_demo_locator(settings) if settings.steam_enabled else UnconfiguredDemoLocator(),
        fetcher=DemoFetcher(http, max_download_bytes=settings.demo_max_download_bytes,
                            max_decompressed_bytes=settings.demo_max_decompressed_bytes),
        parser=Demoparser2Parser(isolation=settings.demo_parse_isolation,
                                 timeout_seconds=settings.demo_parse_timeout_seconds,
                                 threads=settings.demo_parse_threads),
        cipher=cipher, clock=clock,
        max_matches=settings.sync_max_matches_per_request, lock_ttl_seconds=settings.sync_lock_ttl_seconds,
        min_interval_seconds=settings.sync_min_interval_seconds, max_job_attempts=settings.sync_job_max_attempts,
        import_start_match=settings.sync_import_start_match,
    )
    return SteamContext(settings=settings, storage=storage, signer=CookieSigner(settings.session_secret),
                        cipher=cipher, history=history, sync=sync, scorer=scorer, http=http, clock=clock)


def register(app: FastAPI, ctx: SteamContext | None) -> None:
    if ctx is not None and ctx.jobs is None:
        ctx.jobs = UploadJobWorker(ctx)
    if ctx is not None and ctx.auto_sync is None and ctx.steam_enabled:
        ctx.auto_sync = AutoSyncScheduler(ctx)
    app.state.steam = ctx
    app.include_router(router)


def start_background_work(app: FastAPI) -> None:
    """At app startup: resume / clean up upload and sync jobs a previous process left behind,
    and start the automatic sync scheduler (steamlink.autosync; no-op if AUTO_SYNC_INTERVAL_SECONDS=0)."""

    ctx = getattr(app.state, "steam", None)
    if ctx is not None and ctx.jobs is not None:
        ctx.jobs.start()
    if ctx is not None and ctx.auto_sync is not None:
        ctx.auto_sync.start()


def stop_background_work(app: FastAPI) -> None:
    """At app shutdown: stop the scheduler and release its lease (another instance takes over)."""

    ctx = getattr(app.state, "steam", None)
    if ctx is not None and ctx.auto_sync is not None:
        ctx.auto_sync.stop()
