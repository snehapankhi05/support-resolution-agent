"""The pytest guard must reject the development database name."""

import pytest

from tests.db_safety import DevelopmentDatabaseForbidden, refuse_development_database


def test_support_database_is_refused():
    with pytest.raises(DevelopmentDatabaseForbidden):
        refuse_development_database("postgresql+psycopg://app:app@localhost:5433/support")


def test_support_test_database_is_accepted():
    url = refuse_development_database("postgresql+psycopg://app:app@localhost:5433/support_test")
    assert url.endswith("/support_test")
