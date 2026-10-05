"""Test database selection: SQLite by default, PostgreSQL when opted in.

Set ``CSA_TEST_DATABASE_URL`` to a PostgreSQL URL (a throwaway database you
own) to run the whole suite against PostgreSQL::

    CSA_TEST_DATABASE_URL=postgresql://csa:pw@127.0.0.1:5432/csa_test pytest

Each test gets its own fresh schema (``csa_t_<random>``, via ``search_path``)
so migrations run from scratch every time; schemas are dropped after the test.
``DATABASE_URL`` itself is still removed by conftest so a developer's real
database is never touched by accident.
"""

from __future__ import annotations

import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from steamlink.storage.sql import make_engine

TEST_DB_ENV = "CSA_TEST_DATABASE_URL"
_engines: list[Engine] = []
_schemas: list[str] = []


def postgres_url() -> str | None:
    return os.environ.get(TEST_DB_ENV) or None


def make_test_engine(tmp_path, name: str = "test") -> Engine:
    url = postgres_url()
    if not url:
        engine = make_engine(f"sqlite:///{tmp_path / (name + '.db')}")
    else:
        schema = "csa_t_" + uuid.uuid4().hex[:16]
        admin = make_engine(url)
        try:
            with admin.begin() as conn:
                conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        finally:
            admin.dispose()
        _schemas.append(schema)
        sep = "&" if "?" in url else "?"
        engine = make_engine(f"{url}{sep}options=-csearch_path%3D{schema}")
    _engines.append(engine)
    return engine


def cleanup() -> None:
    while _engines:
        _engines.pop().dispose()
    url = postgres_url()
    if url and _schemas:
        admin = make_engine(url)
        try:
            with admin.begin() as conn:
                while _schemas:
                    conn.execute(text(f'DROP SCHEMA IF EXISTS "{_schemas.pop()}" CASCADE'))
        finally:
            admin.dispose()
