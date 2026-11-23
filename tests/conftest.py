"""Pytest uses support_test. It must never migrate or write to the development database."""

import os
from pathlib import Path

os.environ["JWT_SECRET"] = "pytest-only-jwt-secret-not-for-production"

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from tests.db_safety import (
    DEVELOPMENT_DATABASE,
    TEST_DATABASE,
    refuse_development_database,
)

ROOT = Path(__file__).resolve().parents[1]

# Capture the application URL before this process switches to the test database.
_DEVELOPMENT_URL = make_url(get_settings().database_url)
_TEST_URL = _DEVELOPMENT_URL.set(database=TEST_DATABASE)

os.environ["DATABASE_URL"] = refuse_development_database(_TEST_URL)
get_settings.cache_clear()
refuse_development_database(get_settings().database_url)


def ensure_support_test_database() -> None:
    """Create support_test when it is absent, then apply the existing migrations."""
    refuse_development_database(get_settings().database_url)
    admin = create_engine(_DEVELOPMENT_URL, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool)
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DATABASE},
            ).scalar()
            if exists is None:
                connection.execute(text("CREATE DATABASE support_test"))
    finally:
        admin.dispose()

    refuse_development_database(get_settings().database_url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


@pytest.fixture(scope="session")
def engine():
    refuse_development_database(get_settings().database_url)
    try:
        ensure_support_test_database()
        eng = create_engine(get_settings().database_url)
        with eng.connect() as connection:
            current = connection.execute(text("SELECT current_database()")).scalar_one()
            connection.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("Postgres not reachable. Run: docker compose up -d")
    if current == DEVELOPMENT_DATABASE or current != TEST_DATABASE:
        eng.dispose()
        pytest.exit(
            f"Refusing to run pytest against database {current!r}. Tests must use support_test.",
            returncode=3,
        )
    return eng


@pytest.fixture
def conn(engine):
    """Every test runs inside a transaction that is rolled back, so tests never leave data."""
    connection = engine.connect()
    outer = connection.begin()
    yield connection
    outer.rollback()
    connection.close()
