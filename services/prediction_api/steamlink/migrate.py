"""Apply SQL migrations from ``migrations/`` in order.

Usage (from services/prediction_api, with DATABASE_URL set)::

    python -m steamlink.migrate

Each file runs in its own transaction and is recorded in ``schema_migrations``.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def apply_migrations(engine: Engine, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        ))
        applied = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}
    newly_applied = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.stem in applied:
            continue
        with engine.begin() as conn:
            for statement in _statements(path.read_text()):
                conn.execute(text(statement))
            conn.execute(
                text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
                {"v": path.stem, "t": datetime.now(timezone.utc).isoformat()},
            )
        newly_applied.append(path.stem)
    return newly_applied


def main() -> int:  # pragma: no cover - CLI
    from .storage.sql import make_engine

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    applied = apply_migrations(make_engine(url))
    print("applied:", ", ".join(applied) if applied else "nothing (up to date)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
