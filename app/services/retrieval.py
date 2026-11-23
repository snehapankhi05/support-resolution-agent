"""Citations from effective policy text. Rank is not permission to refund."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PolicyChunk, PolicyDocument
from app.services.embeddings import Embedder
from app.services.policy import effective_on

RRF_K = 60
CANDIDATES = 20


@dataclass(frozen=True)
class PolicyCitation:
    policy_key: str
    version: int
    section_ref: str
    content: str
    score: float


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int = RRF_K) -> list[tuple[str, float]]:
    """Merge ordered ids. A shared high rank scores above a single high rank."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def search_citations(
    session: Session,
    query: str,
    *,
    embedder: Embedder | None = None,
    limit: int = 5,
) -> list[PolicyCitation]:
    """Lexical search, plus cosine search when an embedder is supplied."""
    lexical = _lexical_ids(session, query)
    vector: list[str] = []
    if embedder is not None:
        vector = _vector_ids(session, embedder.embed([query])[0])
    fused = reciprocal_rank_fusion([lexical, vector])[:limit]
    if not fused:
        return []
    wanted = [uuid.UUID(item_id) for item_id, _score in fused]
    rows = session.execute(
        select(PolicyChunk, PolicyDocument)
        .join(PolicyDocument, PolicyDocument.id == PolicyChunk.document_id)
        .where(PolicyChunk.id.in_(wanted))
    ).all()
    by_id = {str(chunk.id): (chunk, document) for chunk, document in rows}
    citations: list[PolicyCitation] = []
    for item_id, score in fused:
        pair = by_id.get(item_id)
        if pair is None:
            continue
        chunk, document = pair
        citations.append(
            PolicyCitation(
                policy_key=document.policy_key,
                version=document.version,
                section_ref=chunk.section_ref,
                content=chunk.content,
                score=score,
            )
        )
    return citations


def _lexical_ids(session: Session, query: str) -> list[str]:
    escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = session.scalars(
        select(PolicyChunk)
        .join(PolicyDocument, PolicyDocument.id == PolicyChunk.document_id)
        .where(effective_on(), PolicyChunk.content.ilike(f"%{escaped}%", escape="\\"))
        .order_by(PolicyChunk.section_ref)
        .limit(CANDIDATES)
    ).all()
    return [str(row.id) for row in rows]


def _vector_ids(session: Session, vector: list[float]) -> list[str]:
    distance = PolicyChunk.embedding.cosine_distance(vector)
    rows = session.scalars(
        select(PolicyChunk)
        .join(PolicyDocument, PolicyDocument.id == PolicyChunk.document_id)
        .where(effective_on(), PolicyChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(CANDIDATES)
    ).all()
    return [str(row.id) for row in rows]
