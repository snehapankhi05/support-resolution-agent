"""LangGraph checkpoints live in schema langgraph. Database support is refused."""

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row
from sqlalchemy.engine.url import make_url

from app.config import get_settings

CHECKPOINT_SCHEMA = "langgraph"
CHECKPOINT_TABLES = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)
REFUSED_DATABASE = "support"


class CheckpointDatabaseForbidden(RuntimeError):
    """Checkpoint setup must not write to the development database."""


def assert_checkpoint_database(database: str | None) -> None:
    """Refuse the development database before any checkpoint connection."""
    if database == REFUSED_DATABASE:
        raise CheckpointDatabaseForbidden(
            "Refusing checkpoint writes to database 'support'."
        )


def _conninfo() -> str:
    url = make_url(get_settings().database_url)
    assert_checkpoint_database(url.database)
    rendered = url.set(drivername="postgresql").render_as_string(hide_password=False)
    return rendered


def _connect() -> Connection:
    return Connection.connect(
        _conninfo(), autocommit=True, prepare_threshold=0, row_factory=dict_row
    )


def _current_database(connection: Connection) -> str:
    row = connection.execute("SELECT current_database() AS name").fetchone()
    if row is None:
        raise CheckpointDatabaseForbidden("Checkpoint connection has no database name.")
    return str(row["name"])


@contextmanager
def postgres_checkpointer() -> Iterator[PostgresSaver]:
    """Open a saver whose tables are created only in schema langgraph."""
    admin = _connect()
    try:
        assert_checkpoint_database(_current_database(admin))
        admin.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")
    finally:
        admin.close()

    connection = _connect()
    try:
        assert_checkpoint_database(_current_database(connection))
        connection.execute(f"SET search_path TO {CHECKPOINT_SCHEMA}")
        path = connection.execute("SHOW search_path").fetchone()
        if path is None or not str(path["search_path"]).startswith(CHECKPOINT_SCHEMA):
            raise CheckpointDatabaseForbidden("Checkpoint search_path is not langgraph.")
        saver = PostgresSaver(connection)
        saver.setup()
        _assert_checkpoint_tables(connection)
        yield saver
    finally:
        connection.close()


def _assert_checkpoint_tables(connection: Connection) -> None:
    rows = connection.execute(
        """
        SELECT n.nspname AS schema_name, c.relname AS table_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = ANY(%s) AND c.relkind = 'r'
        """,
        (list(CHECKPOINT_TABLES),),
    ).fetchall()
    found = {(row["schema_name"], row["table_name"]) for row in rows}
    expected = {(CHECKPOINT_SCHEMA, name) for name in CHECKPOINT_TABLES}
    if not expected.issubset(found):
        raise CheckpointDatabaseForbidden(
            "Checkpoint tables are missing from schema langgraph."
        )
    if any(schema == "public" and name in CHECKPOINT_TABLES for schema, name in found):
        raise CheckpointDatabaseForbidden("Checkpoint tables must not live in schema public.")
