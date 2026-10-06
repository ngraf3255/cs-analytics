"""Turn a parsed CS2 demo into the model's round features.

Feature definitions match the training data (``data/rounds.parquet`` /
``python/analysis.py``):
* opening kill = the first player death between the round's freeze end and
  its round_end (training data: opening_kill_tick is always >= freeze_end_tick
  and <= round_end_tick). Deaths after the previous round_end but before this
  round's freeze end (post-round "exit frags", warmup) are ignored; a real
  SourceTV demo has them, so counting them mislabels the opening kill;
* ``opening_kill_side`` = the killer's side; for ``world`` deaths (fall damage,
  etc.) the training data records the victim's side, so we do the same;
* ``opening_kill_seconds`` = (death tick - freeze-end tick) / 64;
* ``opening_weapon`` = the demo's weapon name without the ``weapon_`` prefix
  (e.g. ``ak47``, ``m4a1_silencer``, ``knife_karambit``);
* ``map_name`` = the demo header map name (e.g. ``de_mirage``);
* final score (:func:`final_score`) = rounds won by the team on each side at the
  end, read from the players' team round totals at the last kill plus the
  winners of the rounds that ended from then on (no extra parse pass; robust to
  knife/warmup rounds and side swaps, unlike counting winners by side);
* per-player rounds (:func:`extract_player_rounds`) = for every player
  (SteamID64) the side they played each round, read from their ``player_spawn``
  in that round (so the halftime side swap is just the next spawn), falling
  back to their side in a kill of the round; plus their kills / deaths, opening
  kill / death and whether they survived (see the function). Same single parse
  pass (``player_spawn`` is one more event in it);
* warmup / knife rounds (:func:`match_rounds`): rounds that ended at or before the
  last ``begin_new_match`` (the server's restart into the real match) are not part
  of the match. They are dropped before anything is extracted, so rounds,
  rounds_count, per-player rounds, the final score and every statistic built on
  them see the same rounds, numbered 1..N from the match start (seen on a real
  FACEIT demo: a knife round, then the restart, then 24 rounds).

``PARSE_VERSION`` (steamlink.storage.base) is stored with each match (``matches.parse_version``): when
what the parse extracts changes, it is bumped and matches parsed before are
flagged so a re-upload of the same demo re-parses and replaces them.

Match date: none is extracted. Checked on 4 real CS2 demos (Valve MM, FACEIT,
HLTV/ESL, the demoparser2 SourceTV fixture) with demoparser2 0.42: the header has
map, server name, build (patch_version) and a format GUID but no time; the
server cvars (``server_cvar``) carry no date (``steamworks_sessionid_server`` is
an opaque session id); events carry ticks only. So an uploaded match is dated by
when it was added (labelled "Added"), and Steam sync uses the Game Coordinator's
match time.

Values are stored as observed. Values the model doesn't know (new maps, other
knife skins, ...) get an unscored reason when the report is built; they are
never coerced into a known category.
"""

from __future__ import annotations

import json
import logging
import os
import re
import struct
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .storage.base import PARSE_VERSION, PlayerRoundRecord, RoundRecord  # noqa: F401 (re-exported)

logger = logging.getLogger(__name__)

CS2_TICKRATE = 64
TEAM_NUM_TO_SIDE = {2: "t", 3: "ct", "2": "t", "3": "ct", "T": "t", "CT": "ct", "t": "t", "ct": "ct"}
_OTHER_SIDE = {"ct": "t", "t": "ct"}


@dataclass(frozen=True)
class ParsedRound:
    number: int
    freeze_end_tick: int | None
    end_tick: int
    winner_side: str | None


@dataclass(frozen=True)
class ParsedDeath:
    tick: int
    attacker_side: str | None
    victim_side: str | None
    weapon: str | None
    # Rounds won so far by the attacker's / victim's team (before a round_end on this tick).
    attacker_score: int | None = None
    victim_score: int | None = None
    # SteamID64s (None for world / bots / unknown).
    attacker_steamid: str | None = None
    victim_steamid: str | None = None


@dataclass(frozen=True)
class ParsedSpawn:
    tick: int
    steamid: str
    side: str  # ct | t


@dataclass(frozen=True)
class ParsedDemo:
    map_name: str | None
    rounds: list[ParsedRound] = field(default_factory=list)
    deaths: list[ParsedDeath] = field(default_factory=list)
    tickrate: int = CS2_TICKRATE
    spawns: list[ParsedSpawn] = field(default_factory=list)
    # Tick of the last begin_new_match before the last round_end (the restart into the real
    # match after warmup / a knife round), if any.
    match_start_tick: int | None = None
    # PacketEntities soft-skips from the patched demoparser2 (EntityNotFound / MalformedMessage).
    # Non-zero => parse is degraded (some player props / positions may be missing).
    packet_ents_skips: int = 0


class DemoParseError(Exception):
    """The demo could not be parsed. ``reason`` is the client-facing error code:

    * ``demo_parse_failed``: anything not classified below;
    * ``demo_truncated``: the file is cut off (the header points past its end, or the
      parser hit the end of the file early);
    * ``demo_format_unsupported``: the parser does not understand the demo's messages,
      e.g. a demo from a newer CS2 patch or a FACEIT server than the pinned demoparser2
      knows (``MalformedMessage``, ``EntityNotFound``, ``UnknownDemoCmd``, ...);
    * ``not_a_cs2_demo``: the parser says it is not a CS2 demo at all;
    * ``demo_parse_timeout``: the parse took longer than the configured timeout.
    """

    def __init__(self, message: str = "demo could not be parsed", reason: str = "demo_parse_failed"):
        super().__init__(message)
        self.reason = reason


DEMO_PARSE_REASONS = ("demo_parse_failed", "demo_truncated", "demo_format_unsupported", "not_a_cs2_demo",
                      "demo_parse_timeout")
CS2_DEMO_MAGIC = b"PBDEMS2\0"
_CS2_HEADER = struct.Struct("<8sii")  # magic, file-info offset, spawn-groups offset

# demoparser2 error names (its Rust ``DemoParserError`` variants, surfaced as ``Exception('<Name>')``)
# -> DemoParseError reasons. Unlisted names are ``demo_parse_failed``.
_PARSER_ERROR_REASONS = {
    "DemoEndsEarly": "demo_truncated",
    "MalformedMessage": "demo_format_unsupported",
    "EntityNotFound": "demo_format_unsupported",
    "UnknownDemoCmd": "demo_format_unsupported",
    "IllegalPathOp": "demo_format_unsupported",
    "UnknownPathOP": "demo_format_unsupported",
    "NoSendTableMessage": "demo_format_unsupported",
    "ClassMapperNotFoundFirstPass": "demo_format_unsupported",
    "ClassNotFound": "demo_format_unsupported",
    "ClsIdOutOfBounds": "demo_format_unsupported",
    "StringTableNotFound": "demo_format_unsupported",
    "UnknownEntityHandle": "demo_format_unsupported",
    "FieldNoDecoder": "demo_format_unsupported",
    "DecompressionFailure": "demo_format_unsupported",
    "ImpossibleCmd": "demo_format_unsupported",
    "Source1DemoError": "not_a_cs2_demo",
    "UnknownFile": "not_a_cs2_demo",
}
_PARSER_ERROR_NAME_RE = re.compile(r"\b(" + "|".join(sorted(_PARSER_ERROR_REASONS, key=len, reverse=True)) + r")\b")


def classify_parser_error(text: str) -> str:
    """The DemoParseError reason for a demoparser2 error message / repr (see ``_PARSER_ERROR_REASONS``)."""

    match = _PARSER_ERROR_NAME_RE.search(text or "")
    return _PARSER_ERROR_REASONS[match.group(1)] if match else "demo_parse_failed"


@dataclass(frozen=True)
class DemoFileInfo:
    """What the first bytes of a demo file say, for logs and the pre-parse sanity check."""

    size: int
    magic: bytes
    fileinfo_offset: int | None  # CS2 only: where the trailing file-info block starts

    @property
    def problem(self) -> str | None:
        """``not_a_cs2_demo`` / ``demo_truncated`` if the file can't be a complete CS2 demo, else None.

        A complete CS2 demo's header points at its file-info block, ~20 bytes before the
        end of the file. A copy or download that stopped early still has the full header but
        the offset points past the end. (demoparser2 happily parses such a file up to the cut
        and returns a partial match, so this has to be checked before parsing.) An offset of
        0 (recording never finalized) is not rejected: the rounds up to that point are usable.
        """

        if not self.magic.startswith(CS2_DEMO_MAGIC):
            # A few bytes of a CS2 header and nothing else is a cut-off file too.
            return "demo_truncated" if self.magic and CS2_DEMO_MAGIC.startswith(self.magic) else "not_a_cs2_demo"
        if self.size < _CS2_HEADER.size:
            return "demo_truncated"
        if self.fileinfo_offset is not None and self.fileinfo_offset >= self.size:
            return "demo_truncated"
        return None

    def describe(self) -> str:
        return f"size={self.size} magic={self.magic!r} fileinfo_offset={self.fileinfo_offset}"


def inspect_demo_file(path: str) -> DemoFileInfo:
    """Read a demo's header (size, magic bytes, CS2 file-info offset). Raises OSError if unreadable."""

    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(_CS2_HEADER.size)
    offset = None
    if len(head) == _CS2_HEADER.size and head.startswith(CS2_DEMO_MAGIC):
        offset = _CS2_HEADER.unpack(head)[1]
    return DemoFileInfo(size=size, magic=head[:8], fileinfo_offset=offset)


def demoparser2_version() -> str:
    try:
        from importlib.metadata import version

        return version("demoparser2")
    except Exception:  # not installed (tests with a fake parser)
        return "unknown"


class DemoParser(ABC):
    @abstractmethod
    def parse(self, demo_path: str) -> ParsedDemo: ...


def normalize_steamid(value) -> str | None:
    """A SteamID64 as a string of digits, or None (missing, NaN, ``0`` for bots / world)."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        if value != value:
            return None
        value = int(value)
    text = str(value).strip()
    return text if text.isdigit() and int(text) > 0 else None


def normalize_weapon(weapon: str | None) -> str | None:
    if not weapon:
        return None
    weapon = weapon.strip().lower()
    return weapon[len("weapon_"):] if weapon.startswith("weapon_") else weapon


@dataclass(frozen=True)
class _Window:
    rnd: ParsedRound  # numbered from the match start
    start: int  # exclusive lower bound: the previous round_end (or just before the match start)
    live: int  # first tick of play: the freeze end, else start + 1


def _windows(demo: ParsedDemo) -> list[_Window]:
    """The match's rounds (see :func:`match_rounds`) with their tick windows."""

    ordered = sorted(demo.rounds, key=lambda r: r.end_tick)
    start_tick = demo.match_start_tick
    if start_tick is not None and ordered and start_tick >= ordered[-1].end_tick:
        start_tick = None  # a restart after the last round would drop everything: ignore it
    windows: list[_Window] = []
    previous_end = -1
    for rnd in ordered:
        start, previous_end = previous_end, rnd.end_tick
        if start_tick is not None:
            if rnd.end_tick <= start_tick:
                continue  # warmup / knife round before the restart into the match
            start = max(start, start_tick - 1)
        live = rnd.freeze_end_tick if rnd.freeze_end_tick is not None and rnd.freeze_end_tick > start else start + 1
        windows.append(_Window(ParsedRound(len(windows) + 1, rnd.freeze_end_tick, rnd.end_tick, rnd.winner_side),
                               start, live))
    return windows


def match_rounds(demo: ParsedDemo) -> list[ParsedRound]:
    """The rounds of the match itself, in order and numbered 1..N: rounds that ended at
    or before the last ``begin_new_match`` (warmup, a knife round, a restarted start) are
    left out. Every extractor below works on these rounds."""

    return [w.rnd for w in _windows(demo)]


def extract_rounds(demo: ParsedDemo) -> list[RoundRecord]:
    records: list[RoundRecord] = []
    deaths = sorted(demo.deaths, key=lambda d: d.tick)
    for window in _windows(demo):
        rnd = window.rnd
        in_round = [d for d in deaths if window.start < d.tick <= rnd.end_tick]
        if rnd.freeze_end_tick is not None:
            in_round = [d for d in in_round if d.tick >= rnd.freeze_end_tick]
        first = in_round[0] if in_round else None
        reason = None
        side = seconds = weapon = None
        if first is None:
            reason = "no_opening_kill"
        else:
            weapon = normalize_weapon(first.weapon)
            side = first.attacker_side
            if side is None and weapon == "world":
                side = first.victim_side
            if rnd.freeze_end_tick is None:
                reason = "freeze_end_missing"
            else:
                seconds = round((first.tick - rnd.freeze_end_tick) / demo.tickrate, 6)
                if seconds < 0:
                    reason = "opening_kill_before_freeze_end"
            if reason is None and side not in ("ct", "t"):
                reason = "opening_kill_side_unknown"
            if reason is None and not weapon:
                reason = "opening_weapon_unknown"
        if reason is None and rnd.winner_side not in ("ct", "t"):
            reason = "winner_unknown"
        records.append(RoundRecord(
            round_number=rnd.number,
            winner_side=rnd.winner_side if rnd.winner_side in ("ct", "t") else None,
            opening_kill_side=side if side in ("ct", "t") else None,
            opening_kill_seconds=seconds,
            opening_weapon=weapon,
            unscored_reason=reason,
        ))
    return records


def extract_player_rounds(demo: ParsedDemo) -> list[PlayerRoundRecord]:
    """One record per (round, player with a known side), in round order.

    * side: the side the player was on in the round. Kills of the round (after
      freeze end) are authoritative for the players in them; everyone else gets
      the side of their last ``player_spawn`` in the round (after the previous
      round_end). At halftime the teams switch without a new spawn event (seen
      on real demos), so when the kills contradict most spawns of the round, the
      spawn sides of that round are swapped. Players with neither get no record
      for that round (e.g. they disconnected: seen on real MM demos), except in a
      round with no ``player_spawn`` at all (the recording started after the
      spawns, seen on a real HLTV demo): there the players of the next round are
      taken to have played it too, on the same side unless most players who are
      in both rounds switched sides;
    * only the rounds of the match (:func:`match_rounds`): warmup / knife rounds
      before the last ``begin_new_match`` get no records;
    * kills: enemies killed (teamkills, suicides and world deaths are not kills);
      deaths: any death. Both counted from the round's freeze end up to the next
      round's freeze end, so post-round "exit frags" count for the round that
      just ended (like the in-game scoreboard); the last round stops at its
      round_end (the server kills everyone after the match);
    * opening_kill / opening_death: the player got / suffered the round's
      opening kill (the same death :func:`extract_rounds` uses);
    * survived: not killed before the round ended.
    """

    deaths = sorted(demo.deaths, key=lambda d: d.tick)
    spawns = sorted(demo.spawns, key=lambda s: s.tick)
    windows = _windows(demo)
    round_sides: list[dict[str, str]] = []
    no_spawns: list[bool] = []
    for window in windows:
        rnd = window.rnd
        seen: dict[str, str] = {}
        for death in deaths:
            if window.live <= death.tick <= rnd.end_tick:
                for steamid, side in ((death.attacker_steamid, death.attacker_side),
                                      (death.victim_steamid, death.victim_side)):
                    if steamid and side in ("ct", "t"):
                        seen.setdefault(steamid, side)
        spawned: dict[str, str] = {}
        for spawn in spawns:
            if window.start < spawn.tick <= rnd.end_tick and spawn.side in ("ct", "t"):
                spawned[spawn.steamid] = spawn.side  # the last spawn of the round wins
        both = [sid for sid in spawned if sid in seen]
        if sum(spawned[sid] != seen[sid] for sid in both) * 2 > len(both):
            spawned = {sid: _OTHER_SIDE[side] for sid, side in spawned.items()}  # stale: teams switched
        round_sides.append({**spawned, **seen})
        no_spawns.append(not spawned)
    for index in range(len(windows) - 2, -1, -1):  # backwards: a run of such rounds fills from the end
        if not no_spawns[index]:
            continue
        sides, after = round_sides[index], round_sides[index + 1]
        common = [sid for sid in sides if sid in after]
        switched = sum(sides[sid] != after[sid] for sid in common) * 2 > len(common) if common else False
        for sid, side in after.items():
            sides.setdefault(sid, _OTHER_SIDE[side] if switched else side)

    records: list[PlayerRoundRecord] = []
    for index, window in enumerate(windows):
        rnd, sides = window.rnd, round_sides[index]
        if not sides:
            continue
        # Stats window: up to the next round's live start (exit frags), the last round up to its end.
        stats_end = windows[index + 1].live if index + 1 < len(windows) else rnd.end_tick + 1
        live = [d for d in deaths if window.live <= d.tick <= rnd.end_tick]
        counted = [d for d in deaths if window.live <= d.tick < stats_end]
        opening = live[0] if live else None
        for steamid, side in sides.items():
            kills = sum(1 for d in counted if d.attacker_steamid == steamid and d.victim_steamid != steamid
                        and d.attacker_side in ("ct", "t") and d.victim_side in ("ct", "t")
                        and d.attacker_side != d.victim_side)
            opening_kill = (opening is not None and opening.attacker_steamid == steamid
                            and opening.victim_steamid != steamid and opening.attacker_side != opening.victim_side)
            records.append(PlayerRoundRecord(
                round_number=rnd.number, steam_id=steamid, side=side, kills=kills,
                deaths=sum(1 for d in counted if d.victim_steamid == steamid),
                opening_kill=bool(opening_kill),
                opening_death=opening is not None and opening.victim_steamid == steamid,
                survived=not any(d.victim_steamid == steamid for d in live),
            ))
    return records


def final_score(demo: ParsedDemo) -> tuple[int, int] | None:
    """``(ct, t)``: rounds won by the team on the CT / T side at the end of the demo,
    or None if the demo doesn't say (no cross-team kill with team totals)."""

    played = match_rounds(demo)
    if not played:
        return None
    last_end = played[-1].end_tick
    anchor = None
    for death in sorted(demo.deaths, key=lambda d: d.tick):
        if death.tick > last_end:
            break
        if ({death.attacker_side, death.victim_side} == {"ct", "t"}
                and death.attacker_score is not None and death.victim_score is not None):
            anchor = death
    if anchor is None:
        return None
    score = {anchor.attacker_side: anchor.attacker_score, anchor.victim_side: anchor.victim_score}
    later = [r for r in played if r.end_tick >= anchor.tick]  # not counted yet at the anchor kill
    if len(later) > 3 or any(r.winner_side not in ("ct", "t") for r in later):
        return None  # e.g. a side swap could hide in a long stretch without kills
    for rnd in later:
        score[rnd.winner_side] += 1
    return score["ct"], score["t"]


# Parsing is the memory-heavy step (demoparser2 memory-maps the whole .dem and
# builds per-event frames). Upload and Steam sync share this one slot so a
# 512 MB instance never parses two demos at once.
PARSE_SLOT = threading.BoundedSemaphore(1)

PARSE_ISOLATION_MODES = ("subprocess", "inprocess")
_EVENTS = ("round_end", "round_freeze_end", "player_death", "player_spawn", "begin_new_match")
_DEATH_COLUMNS = ("tick", "attacker_team_num", "user_team_num", "weapon", "attacker_team_rounds_total",
                  "user_team_rounds_total", "attacker_steamid", "user_steamid")
_SPAWN_COLUMNS = ("tick", "user_steamid", "user_team_num")
_INT_COLUMNS = ("winner", "attacker_team_num", "user_team_num", "attacker_team_rounds_total", "user_team_rounds_total")


class Demoparser2Parser(DemoParser):
    """Adapter for the pinned ``demoparser2`` package.

    By default the parse runs in a short-lived child process
    (``python -m steamlink.parse_worker``): the parser's heap and the
    memory-mapped demo are returned to the OS when the child exits instead of
    staying in the API process, and if a pathological demo exhausts memory the
    kernel kills the child (the upload fails with ``demo_parse_failed``), not
    the API. ``isolation="inprocess"`` parses in the calling process.

    One ``parse_events`` pass reads all three events (three ``parse_event``
    calls would each re-read the whole demo); only the needed columns are kept.

    Before parsing, the file's header is checked (:class:`DemoFileInfo`): a cut-off
    file is rejected as ``demo_truncated`` instead of being parsed into a partial match.
    Parse failures carry a reason (see :class:`DemoParseError`); the worker's full
    traceback, the file's size / magic bytes and the demoparser2 version are logged.

    TODO(verify-live): event/column names below follow demoparser2 0.42 docs and
    must be checked against a current matchmaking demo before production use.
    """

    def __init__(self, *, isolation: str = "subprocess", timeout_seconds: float = 600.0, threads: int = 2):
        if isolation not in PARSE_ISOLATION_MODES:
            raise ValueError(f"isolation must be one of {PARSE_ISOLATION_MODES}")
        self.isolation = isolation
        self.timeout_seconds = timeout_seconds
        self.threads = threads

    def parse(self, demo_path: str) -> ParsedDemo:
        try:
            info = inspect_demo_file(demo_path)
        except OSError as exc:
            raise DemoParseError(f"demo file unreadable: {exc!r}") from exc
        if info.problem:
            logger.warning("demo rejected before parsing: %s (%s)", info.problem, info.describe())
            raise DemoParseError(f"demo rejected before parsing: {info.problem}", info.problem)
        logger.info("parsing demo (%s, demoparser2=%s)", info.describe(), demoparser2_version())
        if self.isolation == "inprocess":
            return parse_in_process(demo_path)
        return self._parse_in_subprocess(demo_path)

    def _parse_in_subprocess(self, demo_path: str) -> ParsedDemo:
        package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (package_root, env.get("PYTHONPATH")) if p)
        # demoparser2 uses a rayon pool sized to the host's CPU count by default;
        # Render's 0.1-0.5 CPU instances gain nothing from more threads, and each
        # thread (and glibc malloc arena) adds heap.
        env.setdefault("RAYON_NUM_THREADS", str(self.threads))
        env.setdefault("MALLOC_ARENA_MAX", "2")
        try:
            done = subprocess.run(
                [sys.executable, "-m", "steamlink.parse_worker", os.path.abspath(demo_path)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=self.timeout_seconds, env=env,
                cwd=package_root, check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("demo parse timed out after %ss (%s)", self.timeout_seconds, _describe_file(demo_path))
            raise DemoParseError("demo parse timed out", "demo_parse_timeout") from None
        except OSError as exc:
            raise DemoParseError("demo parser could not be started") from exc
        if done.returncode != 0:
            # -9 (SIGKILL) is usually the kernel OOM killer.
            stderr = done.stderr.decode("utf-8", "replace")
            reason = _worker_reason(stderr)
            logger.warning(
                "demo parse worker exited with %s (reason=%s, demoparser2=%s, %s):\n%s",
                done.returncode, reason, demoparser2_version(), _describe_file(demo_path),
                stderr[-_MAX_LOGGED_STDERR:],
            )
            raise DemoParseError(f"demo could not be parsed ({reason})", reason)
        try:
            return parsed_demo_from_json(json.loads(done.stdout))
        except (ValueError, KeyError, TypeError) as exc:
            raise DemoParseError("demo parser returned invalid output") from exc


# The worker's last stderr line names the DemoParseError reason for the parent process.
WORKER_REASON_PREFIX = "DEMO_PARSE_REASON="
_MAX_LOGGED_STDERR = 8000


def _worker_reason(stderr: str) -> str:
    for line in reversed(stderr.splitlines()):
        if line.startswith(WORKER_REASON_PREFIX):
            reason = line[len(WORKER_REASON_PREFIX):].strip()
            return reason if reason in DEMO_PARSE_REASONS else "demo_parse_failed"
    return "demo_parse_failed"  # killed (e.g. OOM, -9) before it could say


def _describe_file(path: str) -> str:
    try:
        return inspect_demo_file(path).describe()
    except OSError as exc:
        return f"unreadable: {exc!r}"


def parse_in_process(demo_path: str) -> ParsedDemo:
    header: dict = {}
    packet_ents_skips = 0
    try:
        from demoparser2 import DemoParser as _Parser

        # Patched wheel (vendor/patches): process-global soft-skip counters. PARSE_SLOT
        # serializes parses and we reset here, so counts are effectively per-parse
        # (subprocess isolation also gives a fresh process). True instance-local
        # counters need a demoparser2 wheel change — deferred.
        try:
            from demoparser2 import reset_packet_ents_skips, packet_ents_skips as _packet_ents_skips
            reset_packet_ents_skips()
        except ImportError:  # unpatched PyPI wheel
            _packet_ents_skips = None

        parser = _Parser(demo_path)
        header = parser.parse_header() or {}
        frames = dict(parser.parse_events(list(_EVENTS), player=["team_num", "team_rounds_total"]))
        if _packet_ents_skips is not None:
            counts = _packet_ents_skips() or {}
            packet_ents_skips = int(counts.get("total") or 0)
            if packet_ents_skips:
                logger.warning(
                    "demo parse degraded: PacketEntities soft-skips=%s (entity_not_found=%s malformed_message=%s) path=%s",
                    packet_ents_skips, counts.get("entity_not_found"), counts.get("malformed_message"), demo_path,
                )
        del parser
        round_end = _rows(frames.pop("round_end", None), ("tick", "winner"))
        freeze_ticks = sorted(int(t) for t in _column(frames.pop("round_freeze_end", None), "tick"))
        deaths = _rows(frames.pop("player_death", None), _DEATH_COLUMNS)
        spawn_rows = _rows(frames.pop("player_spawn", None), _SPAWN_COLUMNS)
        match_starts = [int(t) for t in _column(frames.pop("begin_new_match", None), "tick")]
        frames.clear()
    except Exception as exc:  # parser raises a variety of native errors
        detail = f"demo could not be parsed: {exc!r}"
        if header:  # the header parsed: say which CS2 build recorded the demo
            detail += f" (patch_version={header.get('patch_version')}, map={header.get('map_name')})"
        raise DemoParseError(detail, classify_parser_error(repr(exc))) from exc

    rounds: list[ParsedRound] = []
    previous_end = -1
    for index, row in enumerate(round_end, start=1):
        end_tick = int(row["tick"])
        freezes = [t for t in freeze_ticks if previous_end < t <= end_tick]
        rounds.append(ParsedRound(
            number=index,
            freeze_end_tick=freezes[-1] if freezes else None,
            end_tick=end_tick,
            winner_side=TEAM_NUM_TO_SIDE.get(row.get("winner")),
        ))
        previous_end = end_tick
    return ParsedDemo(
        map_name=header.get("map_name") or None,
        rounds=rounds,
        deaths=[
            ParsedDeath(
                tick=int(row["tick"]),
                attacker_side=TEAM_NUM_TO_SIDE.get(row.get("attacker_team_num")),
                victim_side=TEAM_NUM_TO_SIDE.get(row.get("user_team_num")),
                weapon=row.get("weapon"),
                attacker_score=row.get("attacker_team_rounds_total"),
                victim_score=row.get("user_team_rounds_total"),
                attacker_steamid=normalize_steamid(row.get("attacker_steamid")),
                victim_steamid=normalize_steamid(row.get("user_steamid")),
            )
            for row in deaths
        ],
        spawns=[
            ParsedSpawn(tick=int(row["tick"]), steamid=steamid, side=side)
            for row in spawn_rows
            if (steamid := normalize_steamid(row.get("user_steamid")))
            and (side := TEAM_NUM_TO_SIDE.get(row.get("user_team_num")))
        ],
        match_start_tick=_last_match_start(match_starts, rounds),
        packet_ents_skips=packet_ents_skips,
    )


def _last_match_start(match_starts: list[int], rounds: list[ParsedRound]) -> int | None:
    """The last begin_new_match before the last round_end (a later one would leave no rounds)."""

    last_end = max((r.end_tick for r in rounds), default=None)
    before = [t for t in match_starts if last_end is None or t < last_end]
    return max(before) if before else None


def parsed_demo_to_json(demo: ParsedDemo) -> dict:
    return {
        "map_name": demo.map_name,
        "tickrate": demo.tickrate,
        "rounds": [[r.number, r.freeze_end_tick, r.end_tick, r.winner_side] for r in demo.rounds],
        "deaths": [[d.tick, d.attacker_side, d.victim_side, d.weapon, d.attacker_score, d.victim_score,
                    d.attacker_steamid, d.victim_steamid] for d in demo.deaths],
        "spawns": [[s.tick, s.steamid, s.side] for s in demo.spawns],
        "match_start_tick": demo.match_start_tick,
        "packet_ents_skips": demo.packet_ents_skips,
    }


def parsed_demo_from_json(data: dict) -> ParsedDemo:
    return ParsedDemo(
        map_name=data["map_name"],
        tickrate=int(data["tickrate"]),
        rounds=[ParsedRound(number=int(n), freeze_end_tick=None if f is None else int(f), end_tick=int(e),
                            winner_side=w) for n, f, e, w in data["rounds"]],
        deaths=[ParsedDeath(int(d[0]), d[1], d[2], d[3], *(None if x is None else int(x) for x in d[4:6]),
                            *(d[6:8] if len(d) >= 8 else (None, None)))
                for d in data["deaths"]],
        spawns=[ParsedSpawn(int(t), str(sid), side) for t, sid, side in data.get("spawns", [])],
        match_start_tick=None if data.get("match_start_tick") is None else int(data["match_start_tick"]),
        packet_ents_skips=int(data.get("packet_ents_skips") or 0),
    )


def _rows(frame, columns: tuple[str, ...]) -> list[dict]:
    if frame is None or len(frame) == 0:
        return []
    frame = frame[[c for c in columns if c in frame.columns]]
    records = frame.to_dict("records")
    for record in records:  # pandas gives floats for nullable ints (NaN = missing)
        for key in _INT_COLUMNS:
            value = record.get(key)
            if isinstance(value, float):
                record[key] = int(value) if value == value else None
    return records


def _column(frame, name: str) -> list:
    if frame is None or len(frame) == 0 or name not in frame:
        return []
    return list(frame[name])
