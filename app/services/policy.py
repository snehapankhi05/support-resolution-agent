"""The only refund authorization decision. Chunk text is not an input."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import PolicyChunk, PolicyDocument
from app.services.eligibility import refund_policy_decision

POLICY_EFFECTS = frozenset({"allow", "conflict", "none"})


@dataclass(frozen=True)
class RefundPolicySnapshot:
    """Metadata decision for the documents that are effective today."""

    policy_found: bool
    policy_conflict: bool
    versions: tuple[dict[str, str | int], ...]


def policy_effect_name(policy_found: bool, policy_conflict: bool) -> str:
    """Stable payload field. allow is the only effect that can be paid."""
    if policy_conflict:
        return "conflict"
    if policy_found:
        return "allow"
    return "none"


def effective_on(day: date | None = None):
    """SQL filter for a policy document that is current on that date."""
    today = date.today() if day is None else day
    return and_(
        PolicyDocument.effective_from <= today,
        or_(PolicyDocument.effective_to.is_(None), PolicyDocument.effective_to > today),
    )


def current_refund_policy(session: Session, *, day: date | None = None) -> RefundPolicySnapshot:
    """Load effective refund decisions and pass them to refund_policy_decision.

    Rows come from policy_chunks joined to policy_documents. A tool argument
    cannot set applies_to, effect, or the version list.
    """
    rows = session.execute(
        select(PolicyDocument.policy_key, PolicyDocument.version, PolicyChunk.chunk_metadata)
        .join(PolicyChunk, PolicyChunk.document_id == PolicyDocument.id)
        .where(
            effective_on(day),
            PolicyChunk.chunk_metadata["applies_to"].as_string() == "refund",
        )
        .order_by(PolicyDocument.policy_key, PolicyDocument.version, PolicyChunk.section_ref)
    ).all()
    metadata = [row.chunk_metadata for row in rows if isinstance(row.chunk_metadata, dict)]
    found, conflict = refund_policy_decision(metadata)
    versions = tuple(
        {"policy_key": key, "version": version}
        for key, version in sorted({(row.policy_key, row.version) for row in rows})
    )
    return RefundPolicySnapshot(found, conflict, versions)
