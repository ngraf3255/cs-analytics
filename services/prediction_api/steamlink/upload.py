"""Manual ``.dem`` / ``.dem.bz2`` upload: an alternative to Steam sync for
demos Valve no longer serves (or before the demo bot is configured)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Callable

from .demo_parser import PARSE_SLOT, DemoParseError, DemoParser, extract_rounds
from .sharecode import InvalidShareCode, decode
from .storage.base import UNKNOWN_MATCH_ID, UPLOAD_KEY_PREFIX, NewMatch, Storage
from .valve import DemoTooLarge, DemoUnavailable, decompress_bz2

CS2_DEMO_MAGIC = b"PBDEMS2\0"
BZIP2_MAGIC = b"BZh"
# Dedupe keys (see Storage): the SHA-256 of the decompressed .dem always, plus
# the Valve match id when the user supplies the match's share code (a hint: it
# only links to matches already in the uploader's own list, never to another
# user's). Without a share code, matches.share_code holds "upload:<sha256>". The
# same match arriving via Steam sync (any user) is matched on the demo hash once
# sync downloads it (Valve serves the same file the client saves). Matches are
# shared across users, so a demo another user already imported is not parsed again.

# Parsing is memory-heavy; one parse per process at a time, shared with Steam
# sync. The background upload worker (steamlink.jobs) waits for the slot.
_parse_slot = PARSE_SLOT


class UploadRejected(Exception):
    def __init__(self, reason: str, status: int = 422):
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class UploadResult:
    match_id: str
    created: bool


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def sniff_demo(head: bytes) -> str | None:
    """'dem' or 'bz2' from a file's first bytes, None if it is neither."""

    if head.startswith(CS2_DEMO_MAGIC):
        return "dem"
    if head.startswith(BZIP2_MAGIC):
        return "bz2"
    return None


def _noop_stage(stage: str, progress: float | None = None) -> None:
    pass


def import_uploaded_demo(
    *, storage: Storage, parser: DemoParser, user_id: str, raw_path: str, workdir: str,
    max_compressed_bytes: int, max_demo_bytes: int, now, share_code: str | None = None,
    demo_sha256: str | None = None, wait_for_parse_slot: bool = False,
    on_stage: Callable[[str, float | None], None] = _noop_stage, source: str = "upload",
    share_code_verified: bool = False,
) -> UploadResult:
    """Decompress (if .bz2), hash, dedupe, parse and store one uploaded demo.

    ``demo_sha256``: the plain .dem's hash if the caller already computed it.
    ``wait_for_parse_slot``: the background worker waits for a running parse
    (e.g. a Steam sync) to finish; a direct caller gets ``upload_busy`` instead.
    ``on_stage(stage, progress)`` reports decompressing / hashing / parsing / storing.
    ``source``: ``"upload"``, or ``"steam_sync"`` when a sync job downloaded the demo.
    ``share_code_verified``: the share code came from Valve's match history (sync), not the user.
    ``UploadResult.created``: the match was newly added to this user's list (parsed now, or
    already stored for another user and shared without parsing again).
    """

    valve_match_id = UNKNOWN_MATCH_ID
    if share_code:
        try:
            valve_match_id = str(decode(share_code).match_id)
        except InvalidShareCode:
            raise UploadRejected("invalid_share_code_format") from None
    with open(raw_path, "rb") as fh:
        head = fh.read(8)
    demo_path = raw_path
    if head.startswith(BZIP2_MAGIC):
        compressed_size = os.path.getsize(raw_path)
        if compressed_size > max_compressed_bytes:
            raise UploadRejected("demo_too_large", 413)
        demo_path = os.path.join(workdir, "upload.dem")
        demo_sha256 = None  # a hash of the archive is not a dedupe key
        reported = [-1.0]

        def progress(read: int) -> None:
            fraction = round(read / compressed_size, 2) if compressed_size else None
            if fraction is not None and fraction - reported[0] >= 0.05:
                reported[0] = fraction
                on_stage("decompressing", fraction)

        on_stage("decompressing", 0.0)
        try:
            decompress_bz2(raw_path, demo_path, max_demo_bytes, on_progress=progress)
        except DemoTooLarge:
            raise UploadRejected("demo_too_large", 413) from None
        except DemoUnavailable:
            raise UploadRejected("not_a_cs2_demo") from None
        os.remove(raw_path)
        with open(demo_path, "rb") as fh:
            head = fh.read(8)
    if head != CS2_DEMO_MAGIC:
        raise UploadRejected("not_a_cs2_demo")

    if demo_sha256 is None:
        on_stage("hashing", None)
        demo_sha256 = sha256_file(demo_path)
    # Same demo (plain or .bz2), or the same match already imported by sync (by this or,
    # for a verified share code, any user): add it to the user's list, don't parse again.
    known = storage.claim_known_match(user_id, share_code=share_code, valve_match_id=valve_match_id,
                                      demo_sha256=demo_sha256, share_code_verified=share_code_verified,
                                      source=source, now=now)
    if known is not None:
        record, added = known
        return UploadResult(record.id, added)
    on_stage("parsing", None)
    if not _parse_slot.acquire(blocking=wait_for_parse_slot):
        raise UploadRejected("upload_busy", 429)
    try:
        parsed = parser.parse(demo_path)
    except DemoParseError:
        raise UploadRejected("demo_parse_failed") from None
    finally:
        _parse_slot.release()
    if not parsed.rounds:
        raise UploadRejected("demo_has_no_rounds")
    on_stage("storing", None)
    match = NewMatch(share_code=share_code or UPLOAD_KEY_PREFIX + demo_sha256, valve_match_id=valve_match_id,
                     status="imported", status_reason=None, map_name=parsed.map_name,
                     rounds=tuple(extract_rounds(parsed)), demo_sha256=demo_sha256, source=source,
                     share_code_verified=share_code_verified)
    match_id, created = storage.record_uploaded_match(user_id, match=match, now=now)
    return UploadResult(match_id, created)
