"""CS2/CS:GO match sharing code encode/decode.

A share code like ``CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xK`` packs 18 bytes:
match id (u64 LE), outcome/reservation id (u64 LE) and TV port (u16 LE),
written as a base-57 number with the least significant digit first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

DICTIONARY = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"
_INDEX = {ch: i for i, ch in enumerate(DICTIONARY)}
SHARE_CODE_RE = re.compile(r"^CSGO(-[" + re.escape(DICTIONARY) + r"]{5}){5}$")


class InvalidShareCode(ValueError):
    pass


@dataclass(frozen=True)
class ShareCode:
    match_id: int
    outcome_id: int
    token: int


# A code inside pasted text, e.g. CS2's "copy share link":
# steam://rungame/730/76561202255233023/+csgo_download_match%20CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xK
_SHARE_CODE_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])CSGO(?:-[" + re.escape(DICTIONARY) + r"]{5}){5}(?![A-Za-z0-9])")


def is_valid_share_code(code: str) -> bool:
    """Format check plus range check (a well-formed code can still be out of range)."""

    if not SHARE_CODE_RE.match(code or ""):
        return False
    try:
        decode_unchecked(code)
    except InvalidShareCode:
        return False
    return True


def extract_share_code(text: str | None) -> str | None:
    """The one share code in what a user pasted: the bare code, or a steam:// share link
    (URL-encoded or not), with surrounding whitespace or quotes. None if there is no code
    or more than one distinct code. Share codes are case-sensitive, so nothing is re-cased."""

    found = set(_SHARE_CODE_IN_TEXT.findall(unquote((text or "").strip())))
    return found.pop() if len(found) == 1 else None


def decode(code: str) -> ShareCode:
    if not SHARE_CODE_RE.match(code or ""):
        raise InvalidShareCode("not a match sharing code")
    return decode_unchecked(code)


def decode_unchecked(code: str) -> ShareCode:
    chars = code[5:].replace("-", "")
    big = 0
    for ch in reversed(chars):
        big = big * len(DICTIONARY) + _INDEX[ch]
    if big >= 1 << 144:
        raise InvalidShareCode("share code out of range")
    raw = big.to_bytes(18, "big")
    return ShareCode(
        match_id=int.from_bytes(raw[0:8], "little"),
        outcome_id=int.from_bytes(raw[8:16], "little"),
        token=int.from_bytes(raw[16:18], "little"),
    )


def encode(share: ShareCode) -> str:
    raw = (
        share.match_id.to_bytes(8, "little")
        + share.outcome_id.to_bytes(8, "little")
        + share.token.to_bytes(2, "little")
    )
    big = int.from_bytes(raw, "big")
    chars = []
    for _ in range(25):
        big, rem = divmod(big, len(DICTIONARY))
        chars.append(DICTIONARY[rem])
    body = "".join(chars)
    return "CSGO-" + "-".join(body[i:i + 5] for i in range(0, 25, 5))
