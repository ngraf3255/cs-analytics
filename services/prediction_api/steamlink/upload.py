"""Manual ``.dem`` / ``.dem.bz2`` upload: an alternative to Steam sync for
demos Valve no longer serves (or before the demo bot is configured)."""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass

from .demo_parser import DemoParseError, DemoParser, extract_rounds
from .sharecode import InvalidShareCode, decode
from .storage.base import UNKNOWN_MATCH_ID, UPLOAD_KEY_PREFIX, NewMatch, Storage, User
from .valve import DemoTooLarge, DemoUnavailable, decompress_bz2

CS2_DEMO_MAGIC = b"PBDEMS2\0"
BZIP2_MAGIC = b"BZh"
# Dedupe keys (see Storage): the SHA-256 of the decompressed .dem always, plus
# the Valve match id when the user supplies the match's share code. Without a
# share code, matches.share_code holds "upload:<sha256>". The same match later
# arriving via Steam sync is matched on the share code / match id, or on the
# demo hash once sync downloads it (Valve serves the same file the client saves).

# Parsing is memory-heavy; allow one upload parse per process at a time.
_parse_slot = threading.BoundedSemaphore(1)


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


def import_uploaded_demo(
    *, storage: Storage, parser: DemoParser, user: User, raw_path: str, workdir: str,
    max_compressed_bytes: int, max_demo_bytes: int, now, share_code: str | None = None,
) -> UploadResult:
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
        if os.path.getsize(raw_path) > max_compressed_bytes:
            raise UploadRejected("demo_too_large", 413)
        demo_path = os.path.join(workdir, "upload.dem")
        try:
            decompress_bz2(raw_path, demo_path, max_demo_bytes)
        except DemoTooLarge:
            raise UploadRejected("demo_too_large", 413) from None
        except DemoUnavailable:
            raise UploadRejected("not_a_cs2_demo") from None
        os.remove(raw_path)
        with open(demo_path, "rb") as fh:
            head = fh.read(8)
    if head != CS2_DEMO_MAGIC:
        raise UploadRejected("not_a_cs2_demo")

    digest = sha256_file(demo_path)
    existing = storage.find_match(user.id, share_code=share_code, valve_match_id=valve_match_id,
                                  demo_sha256=digest)
    if existing and existing.status == "imported":
        # Same demo (plain or .bz2), or the same match already synced from Steam: don't parse again.
        return UploadResult(existing.id, False)
    if not _parse_slot.acquire(blocking=False):
        raise UploadRejected("upload_busy", 429)
    try:
        parsed = parser.parse(demo_path)
    except DemoParseError:
        raise UploadRejected("demo_parse_failed") from None
    finally:
        _parse_slot.release()
    if not parsed.rounds:
        raise UploadRejected("demo_has_no_rounds")
    match = NewMatch(share_code=share_code or UPLOAD_KEY_PREFIX + digest, valve_match_id=valve_match_id,
                     status="imported", status_reason=None, map_name=parsed.map_name,
                     rounds=tuple(extract_rounds(parsed)), demo_sha256=digest, source="upload")
    match_id, created = storage.record_uploaded_match(user.id, match=match, now=now)
    return UploadResult(match_id, created)
