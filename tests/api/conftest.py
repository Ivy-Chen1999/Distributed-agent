"""Postgres-backed tests use a throwaway database; they are skipped when no server is reachable.

Local: `docker-compose up -d` (port 55432). Override with WOMM_TEST_DATABASE_URL.
"""

import os
import uuid

import psycopg
import pytest

from womm.api.db import Database

ADMIN_URL = os.environ.get("WOMM_TEST_DATABASE_URL", "postgresql://womm:womm@localhost:55432/womm")


def _server_available() -> bool:
    try:
        with psycopg.connect(ADMIN_URL, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


@pytest.fixture
def database_url():
    if not _server_available():
        pytest.skip("no Postgres server for tests (docker-compose up -d)")
    name = f"womm_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def db(database_url):
    database = Database(database_url)
    await database.open()
    await database.migrate()
    yield database
    await database.close()
