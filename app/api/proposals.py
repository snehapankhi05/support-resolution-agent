"""Reviewer queue and decisions. The staff id comes from the token's database row."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_db, require_reviewer
from app.db.models import ActionProposal, OrderItem, Ticket, WorkflowRun
from app.services.approvals import ProposalDecisionError, approve_or_reject_proposal
from app.services.refunds import REFUND_ACTION
from app.worker import enqueue_workflow

router = APIRouter()


class ProposalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(approve|reject)$")


class ProposalDecisionResponse(BaseModel):
    proposal_id: UUID
    proposal_status: str
    approval_status: str


class ProposalEvidence(BaseModel):
    proposal_id: UUID
    status: str
    created_at: datetime
    order_id: UUID
    amount_minor: int
    policy_effect: str
    policy_versions: list[dict[str, str | int]]
    payload_hash: str


class CustomerContext(BaseModel):
    ticket_id: UUID
    summary: str


class ProposalReview(BaseModel):
    proposal_evidence: ProposalEvidence
    customer_context: CustomerContext
    # Catalog names for the order. Display only; not part of the approval hash.
    product_names: list[str]


@router.get("/proposals", response_model=list[ProposalReview])
def list_pending_proposals(
    reviewer: Principal = Depends(require_reviewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ProposalReview]:
    """List pending proposals. The customer summary is context, not financial evidence."""
    del reviewer
    rows = db.execute(
        select(ActionProposal, Ticket)
        .join(WorkflowRun, WorkflowRun.id == ActionProposal.run_id)
        .join(Ticket, Ticket.id == WorkflowRun.ticket_id)
        .where(
            ActionProposal.status == "pending_approval",
            ActionProposal.action_type == REFUND_ACTION,
        )
        .order_by(ActionProposal.created_at)
    ).all()
    listed: list[ProposalReview] = []
    for proposal, ticket in rows:
        view = _review(db, proposal, ticket)
        if view is not None:
            listed.append(view)
    return listed


@router.get("/proposals/{proposal_id}", response_model=ProposalReview)
def read_proposal(
    proposal_id: UUID,
    reviewer: Principal = Depends(require_reviewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ProposalReview:
    """Return stored proposal evidence and the separate customer complaint."""
    del reviewer
    proposal = db.get(ActionProposal, proposal_id)
    if proposal is None or proposal.action_type != REFUND_ACTION:
        raise HTTPException(status_code=404, detail="proposal not found")
    ticket = db.scalar(
        select(Ticket)
        .join(WorkflowRun, WorkflowRun.ticket_id == Ticket.id)
        .where(WorkflowRun.id == proposal.run_id)
    )
    view = None if ticket is None else _review(db, proposal, ticket)
    if view is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return view


@router.post("/proposals/{proposal_id}/decision", response_model=ProposalDecisionResponse)
def decide_proposal(
    proposal_id: UUID,
    body: ProposalDecisionBody,
    reviewer: Principal = Depends(require_reviewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ProposalDecisionResponse:
    """Approve or reject one pending proposal. The body cannot name a person or an amount."""
    try:
        result = approve_or_reject_proposal(db, proposal_id, reviewer, body.decision)
        db.commit()
    except ProposalDecisionError as exc:
        db.rollback()
        if exc.status_code == 409 and exc.detail == "decision already recorded":
            _enqueue_recorded_resume(db, proposal_id)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    _enqueue_recorded_resume(db, proposal_id)
    return ProposalDecisionResponse(
        proposal_id=UUID(result["proposal_id"]),
        proposal_status=result["proposal_status"],
        approval_status=result["approval_status"],
    )


def _review(db: Session, proposal: ActionProposal, ticket: Ticket) -> ProposalReview | None:
    evidence = _evidence(proposal)
    if evidence is None:
        return None
    return ProposalReview(
        proposal_evidence=evidence,
        customer_context=CustomerContext(ticket_id=ticket.id, summary=ticket.summary),
        product_names=_product_names(db, evidence.order_id),
    )


def _product_names(db: Session, order_id: UUID) -> list[str]:
    """Catalog names from order_items. The customer summary is not used."""
    names = db.scalars(
        select(OrderItem.product_name)
        .where(OrderItem.order_id == order_id)
        .order_by(OrderItem.product_name)
    ).all()
    return [name.strip() for name in names if isinstance(name, str) and name.strip()]


def _evidence(proposal: ActionProposal) -> ProposalEvidence | None:
    """Copy the stored payload fields. This does not recompute eligibility or the hash."""
    payload = proposal.payload
    if not isinstance(payload, dict):
        return None
    amount = payload.get("amount_minor")
    effect = payload.get("policy_effect")
    versions = payload.get("policy_versions")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        return None
    if effect not in {"allow", "conflict", "none"} or not isinstance(versions, list):
        return None
    try:
        order_id = UUID(str(payload.get("order_id")))
    except (TypeError, ValueError):
        return None
    stored_versions: list[dict[str, str | int]] = []
    for item in versions:
        if not isinstance(item, dict):
            return None
        key = item.get("policy_key")
        version = item.get("version")
        if not isinstance(key, str) or not key.strip():
            return None
        if isinstance(version, bool) or not isinstance(version, int):
            return None
        stored_versions.append({"policy_key": key, "version": version})
    return ProposalEvidence(
        proposal_id=proposal.id,
        status=proposal.status,
        created_at=proposal.created_at,
        order_id=order_id,
        amount_minor=amount,
        policy_effect=effect,
        policy_versions=stored_versions,
        payload_hash=proposal.payload_hash,
    )


def _enqueue_recorded_resume(db: Session, proposal_id: UUID) -> None:
    """Queue the run after a stored decision. A failed publish does not undo it."""
    proposal = db.get(ActionProposal, proposal_id)
    if proposal is None:
        return
    run = db.get(WorkflowRun, proposal.run_id)
    if run is None or run.status != "awaiting_approval":
        return
    try:
        enqueue_workflow(run.id)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="decision recorded; workflow resume was not queued",
        ) from exc
