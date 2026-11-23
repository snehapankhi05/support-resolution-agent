"""Checkpoint setup refuses the development database before connecting."""

import pytest

from app.workflows.checkpoints import CheckpointDatabaseForbidden, assert_checkpoint_database


def test_support_database_is_refused_before_connect():
    with pytest.raises(CheckpointDatabaseForbidden):
        assert_checkpoint_database("support")


def test_support_test_database_is_allowed():
    assert_checkpoint_database("support_test")
