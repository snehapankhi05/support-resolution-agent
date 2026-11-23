"""Refuse any pytest URL that is not the isolated support_test database."""

from sqlalchemy.engine.url import URL, make_url

DEVELOPMENT_DATABASE = "support"
TEST_DATABASE = "support_test"


class DevelopmentDatabaseForbidden(RuntimeError):
    """Raised when a test run would use the development database."""


def refuse_development_database(url: str | URL) -> str:
    """Return the URL string when it names support_test, and refuse support."""
    parsed = make_url(url)
    if parsed.database == DEVELOPMENT_DATABASE or parsed.database != TEST_DATABASE:
        raise DevelopmentDatabaseForbidden(
            f"Refusing to run pytest against database {parsed.database!r}. "
            "Tests must use support_test."
        )
    return parsed.render_as_string(hide_password=False)
