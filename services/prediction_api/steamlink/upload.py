"""Manual ``.dem`` / ``.dem.bz2`` upload: an alternative to Steam sync for
demos Valve no longer serves (or before the demo bot is configured)."""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass

from .demo_parser import DemoParseError, DemoParser, extract_rounds
from .storage.base import NewMatch, Storage, User
from .valve import DemoTooLarge, DemoUnavailable, decompress_bz2

CS2_DEMO_MAGIC = b"PBDEMS2\0"
BZIP2_MAGIC = b"BZh"

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


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def import_uploaded_demo(
    *, storage: Storage, parser: DemoParser, user: User, raw_path: str, workdir: str,
    max_compressed_bytes: int, max_demo_bytes: int, now,
) -> UploadResult:
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

    key = "upload:" + _sha256(demo_path)
    if not _parse_slot.acquire(blocking=False):
        raise UploadRejected("upload_busy", 429)
    try:
        parsed = parser.parse(demo_path)
    except DemoParseError:
        raise UploadRejected("demo_parse_failed") from None
    finally:
        _parse_slot.release()
    match = NewMatch(share_code=key, valve_match_id="upload", status="imported", status_reason=None,
                     map_name=parsed.map_name, rounds=tuple(extract_rounds(parsed)))
    match_id, created = storage.record_uploaded_match(user.id, match=match, now=now)
    return UploadResult(match_id, created)
