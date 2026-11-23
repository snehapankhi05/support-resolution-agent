"""Publication and citation tests. The embedder is a stub, and rows are removed."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

from app.db.models import PolicyChunk, PolicyDocument
from app.db.session import SessionLocal
from app.services.embeddings import EMBEDDING_DIM, StubEmbedder
from app.services.policy import current_refund_policy
from app.services.policy_ingest import PolicyIngestError, ingest_policy, load_policy
from app.services.retrieval import search_citations

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "policies" / "refund_policy_v1.json"


def _delete_published() -> None:
    with SessionLocal() as session:
        ids = list(
            session.scalars(
                select(PolicyDocument.id).where(PolicyDocument.policy_key == "refund_policy")
            )
        )
        if ids:
            session.execute(delete(PolicyChunk).where(PolicyChunk.document_id.in_(ids)))
            session.execute(delete(PolicyDocument).where(PolicyDocument.id.in_(ids)))
            session.commit()


def test_ingest_is_idempotent_and_a_changed_body_is_refused(engine):
    _delete_published()
    draft = load_policy(MANIFEST)
    try:
        with SessionLocal() as session:
            first = ingest_policy(session, draft, StubEmbedder())
            session.commit()
        with SessionLocal() as session:
            second = ingest_policy(session, draft, StubEmbedder())
            session.commit()
            stored = session.scalar(
                select(func.count())
                .select_from(PolicyDocument)
                .where(PolicyDocument.policy_key == "refund_policy")
            )
        assert first.already_present is False
        assert second.already_present is True
        assert first.chunks == 8
        assert stored == 1
        changed = draft.__class__(
            policy_key=draft.policy_key,
            version=draft.version,
            effective_from=draft.effective_from,
            effective_to=draft.effective_to,
            source_uri=draft.source_uri,
            applies_to=draft.applies_to,
            effect=draft.effect,
            sections=(draft.sections[0].__class__(draft.sections[0].section_ref, "Changed body."),)
            + draft.sections[1:],
        )
        with SessionLocal() as session:
            before = session.scalar(
                select(PolicyChunk.content)
                .join(PolicyDocument, PolicyDocument.id == PolicyChunk.document_id)
                .where(PolicyDocument.policy_key == "refund_policy", PolicyChunk.section_ref == draft.sections[0].section_ref)
            )
            with pytest.raises(PolicyIngestError, match="different text"):
                ingest_policy(session, changed, StubEmbedder())
            session.rollback()
            after = session.scalar(
                select(PolicyChunk.content)
                .join(PolicyDocument, PolicyDocument.id == PolicyChunk.document_id)
                .where(PolicyDocument.policy_key == "refund_policy", PolicyChunk.section_ref == draft.sections[0].section_ref)
            )
            assert after == before
    finally:
        _delete_published()


def test_new_version_closes_the_open_document(engine):
    _delete_published()
    draft = load_policy(MANIFEST)
    later = draft.__class__(
        policy_key=draft.policy_key,
        version=2,
        effective_from=date.today(),
        effective_to=None,
        source_uri=draft.source_uri,
        applies_to="refund",
        effect="allow",
        sections=draft.sections,
    )
    try:
        with SessionLocal() as session:
            ingest_policy(session, draft, StubEmbedder())
            ingest_policy(session, later, StubEmbedder())
            session.commit()
            snapshot = current_refund_policy(session)
            opened = session.scalar(
                select(PolicyDocument).where(
                    PolicyDocument.policy_key == "refund_policy",
                    PolicyDocument.version == 1,
                )
            )
        published = tuple(item for item in snapshot.versions if item["policy_key"] == "refund_policy")
        assert opened.effective_to == date.today()
        assert published == ({"policy_key": "refund_policy", "version": 2},)
    finally:
        _delete_published()


def test_expired_deny_does_not_conflict_and_is_not_cited(engine):
    marker = "expired-deny-only"
    with SessionLocal() as session:
        old = PolicyDocument(
            policy_key=marker,
            version=1,
            effective_from=date.today() - timedelta(days=10),
            effective_to=date.today(),
            source_uri="memory://expired-deny",
        )
        current = PolicyDocument(
            policy_key=marker,
            version=2,
            effective_from=date.today(),
            source_uri="memory://expired-deny",
        )
        session.add_all([old, current])
        session.flush()
        session.add(
            PolicyChunk(
                document_id=old.id,
                section_ref="old",
                content=f"deny marker {marker}",
                chunk_metadata={"applies_to": "refund", "effect": "deny"},
            )
        )
        session.add(
            PolicyChunk(
                document_id=current.id,
                section_ref="current",
                content=f"allow marker {marker}",
                embedding=[1.0] + [0.0] * (EMBEDDING_DIM - 1),
                chunk_metadata={"applies_to": "refund", "effect": "allow"},
            )
        )
        session.commit()
        old_id = old.id
        current_id = current.id
    try:
        with SessionLocal() as session:
            snapshot = current_refund_policy(session)
            citations = search_citations(session, marker, embedder=None)
        versions = {item["version"] for item in snapshot.versions if item["policy_key"] == marker}
        assert versions == {2}
        assert [row.section_ref for row in citations] == ["current"]
        assert old_id != current_id
    finally:
        with SessionLocal() as session:
            session.execute(delete(PolicyChunk).where(PolicyChunk.document_id.in_([old_id, current_id])))
            session.execute(delete(PolicyDocument).where(PolicyDocument.id.in_([old_id, current_id])))
            session.commit()


def test_vector_rank_finds_the_near_chunk_when_the_words_do_not_match(engine):
    near = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    far = [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2)

    class FixedEmbedder:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return [near for _text in texts]

    with SessionLocal() as session:
        document = PolicyDocument(
            policy_key="vector-demo",
            version=1,
            effective_from=date.today(),
            source_uri="memory://vector-demo",
        )
        session.add(document)
        session.flush()
        session.add(
            PolicyChunk(
                document_id=document.id,
                section_ref="far",
                content="unrelated warehouse hours",
                embedding=far,
                chunk_metadata={"applies_to": "shipping", "effect": "allow"},
            )
        )
        session.add(
            PolicyChunk(
                document_id=document.id,
                section_ref="near",
                content="unrelated carrier cutoff",
                embedding=near,
                chunk_metadata={"applies_to": "shipping", "effect": "allow"},
            )
        )
        session.commit()
        document_id = document.id
    try:
        with SessionLocal() as session:
            citations = search_citations(session, "zz-no-shared-word", embedder=FixedEmbedder())
        assert citations[0].section_ref == "near"
    finally:
        with SessionLocal() as session:
            session.execute(delete(PolicyChunk).where(PolicyChunk.document_id == document_id))
            session.execute(delete(PolicyDocument).where(PolicyDocument.id == document_id))
            session.commit()
