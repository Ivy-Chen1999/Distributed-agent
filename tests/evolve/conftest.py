import psycopg
import pytest

from ..api.conftest import ADMIN_URL, database_url, db  # noqa: F401
from ..graph.conftest import fixture  # noqa: F401
from ..test_cli import fake_version, use_script  # noqa: F401
from .test_gepa_adapter import env  # noqa: F401


@pytest.fixture
def holdout_url(database_url):  # noqa: F811
    """A second throwaway database for the sealed holdout, separate from ``database_url``."""
    name = f"womm_holdout_{database_url.rsplit('_', 1)[-1]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    yield ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
