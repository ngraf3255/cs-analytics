"""Shared matches: one ``matches`` row per real match, owned by every user who
imported it (``match_owners``), instead of one copy per user.

Why: dedupe (share code / Valve match id / demo SHA-256) used to be per user, so
a match another player had already imported was downloaded and parsed again
for each user. Now a known match is attached to the new user's list without a
download or parse.

Trust: a share code that came from Valve's match history (Steam sync) is
*verified* (``share_code_verified = 1``); one typed in with an upload is only a
hint. Matches are shared across users by demo SHA-256 (the server hashed the
bytes) or by a verified share code / Valve match id, never by an unverified
hint alone, so one user's mislabelled upload can't become another user's match.

``matches.user_id`` stays (NOT NULL; SQLite can't drop it) and now means "the
user who first imported the match"; deleting that user hands the row to
another owner (see SqlStorage.delete_user) or deletes it when nobody else owns it.

Backfill (in this transaction):
1. ``match_owners`` gets one row per existing match (its user, source, time).
2. Rows of different users that are the same match (same demo hash, or same
   verified share code / Valve match id; transitively) are merged into the
   earliest imported one: owners and upload jobs are re-pointed, the
   duplicates and their rounds deleted, missing keys filled in.
3. A share code still held by more than one row (unverified hints that
   contradict another row) stays only on the verified / earliest row; the
   others fall back to ``upload:<sha256>``.
4. The per-user unique indexes on demo hash / match id become global.
Runs on PostgreSQL and SQLite.
"""

from __future__ import annotations

from sqlalchemy import text

UPLOAD_PREFIX = "upload:"
UNKNOWN = "upload"


def upgrade(conn) -> None:
    conn.execute(text(
        "CREATE TABLE match_owners ("
        " user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
        " match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,"
        " source TEXT NOT NULL,"
        " added_at TIMESTAMPTZ NOT NULL,"
        " PRIMARY KEY (user_id, match_id))"
    ))
    conn.execute(text("CREATE INDEX match_owners_user_added_idx ON match_owners (user_id, added_at)"))
    conn.execute(text("CREATE INDEX match_owners_match_idx ON match_owners (match_id)"))
    conn.execute(text("ALTER TABLE matches ADD COLUMN share_code_verified INTEGER NOT NULL DEFAULT 0"))
    # Sync rows always carry the share code Valve's history returned.
    conn.execute(text("UPDATE matches SET share_code_verified = 1 WHERE source = 'steam_sync'"))
    conn.execute(text(
        "INSERT INTO match_owners (user_id, match_id, source, added_at)"
        " SELECT user_id, id, source, imported_at FROM matches"
    ))
    _merge_duplicates(conn)
    _drop_contradicted_codes(conn)
    conn.execute(text("DROP INDEX matches_user_demo_sha256_key"))
    conn.execute(text("DROP INDEX matches_user_valve_match_key"))
    conn.execute(text("CREATE UNIQUE INDEX matches_demo_sha256_key ON matches (demo_sha256)"))
    conn.execute(text("CREATE UNIQUE INDEX matches_share_code_key ON matches (share_code)"))
    conn.execute(text(
        "CREATE UNIQUE INDEX matches_valve_match_key ON matches (valve_match_id) WHERE valve_match_id <> 'upload'"
    ))


def _rows(conn) -> list:
    return conn.execute(text(
        "SELECT id, user_id, share_code, valve_match_id, demo_sha256, status, share_code_verified, imported_at"
        " FROM matches ORDER BY imported_at, id"
    )).mappings().all()


def _merge_duplicates(conn) -> None:
    rows = _rows(conn)
    parent = {row["id"]: row["id"] for row in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first_by_key: dict[tuple, str] = {}
    for row in rows:
        keys = []
        if row["demo_sha256"]:
            keys.append(("sha", row["demo_sha256"]))
        if row["share_code_verified"]:
            if not row["share_code"].startswith(UPLOAD_PREFIX):
                keys.append(("code", row["share_code"]))
            if row["valve_match_id"] != UNKNOWN:
                keys.append(("vmid", row["valve_match_id"]))
        for key in keys:
            if key in first_by_key:
                a, b = find(first_by_key[key]), find(row["id"])
                if a != b:
                    parent[b] = a
            else:
                first_by_key[key] = row["id"]

    groups: dict[str, list] = {}
    for row in rows:  # rows are in import order, so each group is too
        groups.setdefault(find(row["id"]), []).append(row)
    for members in groups.values():
        if len(members) > 1:
            _merge_group(conn, members)


def _merge_group(conn, members: list) -> None:
    keep = next((m for m in members if m["status"] == "imported"), members[0])
    others = [m for m in members if m["id"] != keep["id"]]
    real = [m for m in members if not m["share_code"].startswith(UPLOAD_PREFIX)]
    code_row = next((m for m in real if m["share_code_verified"]), None) or (
        keep if keep in real else (real[0] if real else None))
    sha = keep["demo_sha256"] or next((m["demo_sha256"] for m in members if m["demo_sha256"]), None)
    for other in others:
        conn.execute(text(
            "UPDATE match_owners SET match_id = :keep WHERE match_id = :other"
            " AND user_id NOT IN (SELECT user_id FROM match_owners WHERE match_id = :keep)"
        ), {"keep": keep["id"], "other": other["id"]})
        conn.execute(text("DELETE FROM match_owners WHERE match_id = :other"), {"other": other["id"]})
        conn.execute(text("UPDATE upload_jobs SET match_id = :keep WHERE match_id = :other"),
                     {"keep": keep["id"], "other": other["id"]})
        conn.execute(text("DELETE FROM rounds WHERE match_id = :other"), {"other": other["id"]})
        conn.execute(text("DELETE FROM matches WHERE id = :other"), {"other": other["id"]})
    values = {"id": keep["id"], "sha": sha}
    if code_row is not None and code_row["id"] != keep["id"] and not _free_code(conn, keep, code_row):
        code_row = keep if keep in real else None
    if code_row is not None:
        values.update(code=code_row["share_code"], vmid=code_row["valve_match_id"],
                      verified=code_row["share_code_verified"])
    else:
        values.update(code=keep["share_code"] if not sha else UPLOAD_PREFIX + sha, vmid=keep["valve_match_id"],
                      verified=keep["share_code_verified"])
    conn.execute(text(
        "UPDATE matches SET share_code = :code, valve_match_id = :vmid, share_code_verified = :verified,"
        " demo_sha256 = :sha WHERE id = :id"
    ), values)


def _free_code(conn, keep, code_row) -> bool:
    """Make ``code_row``'s share code free for ``keep``: rows outside the group that
    hold it with an unverified hint lose it; a verified holder wins (returns False)."""

    holders = conn.execute(text(
        "SELECT id, demo_sha256, share_code_verified FROM matches WHERE id <> :keep"
        " AND (share_code = :code OR (valve_match_id = :vmid AND valve_match_id <> :unknown))"
    ), {"keep": keep["id"], "code": code_row["share_code"], "vmid": code_row["valve_match_id"],
        "unknown": UNKNOWN}).mappings().all()
    if any(h["share_code_verified"] for h in holders) and not code_row["share_code_verified"]:
        return False
    for holder in holders:
        conn.execute(text(
            "UPDATE matches SET share_code = :code, valve_match_id = :unknown, share_code_verified = 0 WHERE id = :id"
        ), {"code": UPLOAD_PREFIX + (holder["demo_sha256"] or holder["id"]), "unknown": UNKNOWN, "id": holder["id"]})
    return True


def _drop_contradicted_codes(conn) -> None:
    """A real share code / match id on more than one row: keep it on the verified
    (else the earliest) row; the others become plain uploads."""

    seen_codes: set[str] = set()
    seen_vmids: set[str] = set()
    rows = sorted(_rows(conn), key=lambda r: (not r["share_code_verified"],))  # stable: verified first
    for row in rows:
        code, vmid = row["share_code"], row["valve_match_id"]
        conflict = (not code.startswith(UPLOAD_PREFIX) and code in seen_codes) or (vmid != UNKNOWN and vmid in seen_vmids)
        if conflict:
            fallback = UPLOAD_PREFIX + (row["demo_sha256"] or row["id"])
            conn.execute(text(
                "UPDATE matches SET share_code = :code, valve_match_id = :unknown, share_code_verified = 0"
                " WHERE id = :id"
            ), {"code": fallback, "unknown": UNKNOWN, "id": row["id"]})
            continue
        if not code.startswith(UPLOAD_PREFIX):
            seen_codes.add(code)
        if vmid != UNKNOWN:
            seen_vmids.add(vmid)
