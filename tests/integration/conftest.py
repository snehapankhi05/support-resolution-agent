"""Integration fixtures. Execution tests leave a refund policy row that must not leak."""

import pytest
from sqlalchemy import delete, select

from app.db.models import PolicyChunk, PolicyDocument
from app.db.session import SessionLocal

TEST_POLICY_URI = "memory://test-refund-policy"


@pytest.fixture(autouse=True)
def remove_test_refund_policy(engine):
    """Delete the execution-test policy after every integration test."""
    yield
    with SessionLocal() as session:
        ids = list(
            session.scalars(
                select(PolicyDocument.id).where(PolicyDocument.source_uri == TEST_POLICY_URI)
            )
        )
        if not ids:
            return
        session.execute(delete(PolicyChunk).where(PolicyChunk.document_id.in_(ids)))
        session.execute(delete(PolicyDocument).where(PolicyDocument.id.in_(ids)))
        session.commit()
