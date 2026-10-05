import os
import sys
from pathlib import Path

# Make the service directory importable and make sure tests never pick up a
# real database or encryption key from the developer's environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("DATABASE_URL", "TOKEN_ENCRYPTION_KEYS", "SESSION_SECRET", "STEAM_WEB_API_KEY"):
    os.environ.pop(name, None)

import pytest  # noqa: E402

import dbutil  # noqa: E402


@pytest.fixture(autouse=True)
def _dispose_test_databases():
    yield
    dbutil.cleanup()


def pytest_report_header(config):
    url = dbutil.postgres_url()
    return f"storage backend: {'PostgreSQL (' + dbutil.TEST_DB_ENV + ')' if url else 'SQLite'}"
