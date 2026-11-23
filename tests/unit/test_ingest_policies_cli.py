"""CLI database guard for policy ingest. Does not open Postgres or embed."""

import pytest

from scripts.ingest_policies import assert_ingest_cli_database


def test_ingest_cli_allows_support_test_and_support_ui():
    assert assert_ingest_cli_database("support_test") == "support_test"
    assert assert_ingest_cli_database("support_ui") == "support_ui"


def test_ingest_cli_refuses_support_and_support_eval():
    with pytest.raises(SystemExit, match="refuses database 'support'"):
        assert_ingest_cli_database("support")
    with pytest.raises(SystemExit, match="refuses database 'support_eval'"):
        assert_ingest_cli_database("support_eval")


def test_ingest_cli_refuses_other_database_names():
    with pytest.raises(SystemExit, match="support_test or support_ui"):
        assert_ingest_cli_database("postgres")
    with pytest.raises(SystemExit, match="support_test or support_ui"):
        assert_ingest_cli_database(None)
