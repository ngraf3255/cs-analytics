"""Apply SQL migrations from ``migrations/`` in order.

Usage (from services/prediction_api, with DATABASE_URL set)::

    python -m steamlink.migrate                  # fails if DATABASE_URL is unset
    python -m steamlink.migrate --if-configured  # no-op if DATABASE_URL is unset

Migrations are ``NNNN_name.sql`` files (portable SQL statements) or
``NNNN_name.py`` modules with an ``upgrade(conn)`` function (for backfills that
are awkward in portable SQL; ``conn`` is a SQLAlchemy Connection). Each file
runs in its own transaction and is recorded in ``schema_migrations``.
On PostgreSQL a session advisory lock serialises concurrent runners (e.g. two
instances starting at once), so a migration is never applied twice.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


# Arbitrary constant key for pg_advisory_lock ("csa" + migrations).
_PG_LOCK_KEY = 0x637361_6D6967


@contextmanager
def _migration_lock(engine: Engine):
    if engine.dialect.name != "postgresql":
        yield
        return
    with engine.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _PG_LOCK_KEY})
        lock_conn.commit()
        try:
            yield
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PG_LOCK_KEY})
            lock_conn.commit()


def _load_module(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"csa_migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_migrations(engine: Engine, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    with _migration_lock(engine):
        return _apply(engine, migrations_dir)


def _apply(engine: Engine, migrations_dir: Path) -> list[str]:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        ))
        applied = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}
    newly_applied = []
    paths = sorted(p for p in migrations_dir.iterdir() if p.suffix in (".sql", ".py") and p.stem[:4].isdigit())
    for path in paths:
        if path.stem in applied:
            continue
        with engine.begin() as conn:
            if path.suffix == ".py":
                _load_module(path).upgrade(conn)
            else:
                for statement in _statements(path.read_text()):
                    conn.execute(text(statement))
            conn.execute(
                text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
                {"v": path.stem, "t": datetime.now(timezone.utc).isoformat()},
            )
        newly_applied.append(path.stem)
    return newly_applied


def main(argv: list[str] | None = None) -> int:
    """``--if-configured``: exit 0 without doing anything when DATABASE_URL is
    unset (used in the Render start command, so the API still boots with Steam
    features disabled)."""

    from .storage.sql import make_engine

    args = sys.argv[1:] if argv is None else argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        if "--if-configured" in args:
            print("DATABASE_URL is not set; skipping migrations")
            return 0
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    applied = apply_migrations(make_engine(url))
    print("applied:", ", ".join(applied) if applied else "nothing (up to date)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
