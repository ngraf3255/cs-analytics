"""CS2/CS:GO match sharing code encode/decode.

A share code like ``CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xK`` packs 18 bytes:
match id (u64 LE), outcome/reservation id (u64 LE) and TV port (u16 LE),
written as a base-57 number with the least significant digit first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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


def is_valid_share_code(code: str) -> bool:
    return bool(SHARE_CODE_RE.match(code or ""))


def decode(code: str) -> ShareCode:
    if not is_valid_share_code(code):
        raise InvalidShareCode("not a match sharing code")
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
