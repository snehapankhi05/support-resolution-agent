"""Record a human decision on one proposal. The caller supplies the loaded principal."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.db.models import (
    ActionProposal,
    Approval,
    AuditEvent,
    Order,
    OrderItem,
    Return,
    Ticket,
    WorkflowRun,
)
from app.services.eligibility import RefundFacts, evaluate_refund_eligibility
from app.services.policy import current_refund_policy, policy_effect_name
from app.services.refunds import (
    REFUND_ACTION,
    canonical_policy_versions,
    hash_payload,
    refund_payload,
)

APPROVAL_TTL = timedelta(hours=24)


class ProposalDecisionError(Exception):
    """The decision was refused. status_code is the HTTP status the route should return."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def approve_or_reject_proposal(
    session: Session,
    proposal_id: uuid.UUID,
    principal: Principal,
    decision: str,
) -> dict[str, str]:
    """Approve or reject one pending proposal. This does not move money.

    The staff id is the principal loaded from the database. A second decision
    for the same proposal is refused. Approve recomputes eligibility and the
    payload hash before the approval row is inserted.
    """
    if principal.actor != "staff" or principal.role not in {"reviewer", "admin"}:
        raise ProposalDecisionError(403, "reviewer account required")
    if decision not in {"approve", "reject"}:
        raise ProposalDecisionError(422, "decision must be approve or reject")

    proposal = session.execute(
        select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update()
    ).scalar_one_or_none()
    if proposal is None or proposal.action_type != REFUND_ACTION:
        raise ProposalDecisionError(404, "proposal not found")
    existing = session.scalar(select(Approval).where(Approval.proposal_id == proposal.id))
    if existing is not None:
        raise ProposalDecisionError(409, "decision already recorded")
    if proposal.status != "pending_approval":
        raise ProposalDecisionError(409, "proposal is not pending approval")

    if decision == "approve":
        _require_current_proposal(session, proposal)

    now = datetime.now(UTC)
    status = "approved" if decision == "approve" else "rejected"
    proposal.status = status
    approval = Approval(
        proposal_id=proposal.id,
        reviewer_id=principal.id,
        status=status,
        decided_at=now,
        expires_at=now + APPROVAL_TTL,
    )
    session.add(approval)
    session.add(
        AuditEvent(
            run_id=proposal.run_id,
            actor=f"staff:{principal.id}",
            event_type="proposal_approved" if decision == "approve" else "proposal_rejected",
            redacted_payload={"proposal_id": str(proposal.id), "decision": decision},
        )
    )
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None) != (
            "uq_approval_per_proposal"
        ):
            raise
        raise ProposalDecisionError(409, "decision already recorded") from exc
    return {
        "proposal_id": str(proposal.id),
        "proposal_status": proposal.status,
        "approval_status": approval.status,
    }


def _require_current_proposal(session: Session, proposal: ActionProposal) -> None:
    run = session.get(WorkflowRun, proposal.run_id)
    ticket = session.get(Ticket, run.ticket_id) if run is not None else None
    if ticket is None or ticket.order_id is None:
        raise ProposalDecisionError(409, "proposal is not eligible")
    order = session.get(Order, ticket.order_id)
    if order is None or order.customer_id != ticket.customer_id:
        raise ProposalDecisionError(409, "proposal is not eligible")
    snapshot = current_refund_policy(session)
    amounts = session.scalars(
        select(OrderItem.paid_amount_minor).where(OrderItem.order_id == order.id)
    ).all()
    latest_return = session.scalar(
        select(Return).where(Return.order_id == order.id).order_by(Return.created_at.desc())
    )
    requested = None if latest_return is None else latest_return.created_at
    delivered = order.delivered_at
    if requested is not None and requested.tzinfo is None:
        requested = requested.replace(tzinfo=UTC)
    if delivered is not None and delivered.tzinfo is None:
        delivered = delivered.replace(tzinfo=UTC)
    decision = evaluate_refund_eligibility(
        RefundFacts(
            order_status=order.status,
            order_customer_id=order.customer_id,
            requesting_customer_id=ticket.customer_id,
            delivered_at=delivered,
            return_requested_at=requested,
            return_status=None if latest_return is None else latest_return.status,
            inspection_status=None if latest_return is None else latest_return.inspection_status,
            paid_amount_minor=order.paid_amount_minor,
            refund_committed_minor=order.refund_committed_minor,
            returned_items_paid_minor=sum(amounts, 0),
            policy_found=snapshot.policy_found,
            policy_conflict=snapshot.policy_conflict,
        )
    )
    if not decision.eligible or decision.escalate or decision.amount_minor <= 0:
        raise ProposalDecisionError(409, decision.reason)
    expected = refund_payload(
        order.id,
        decision.amount_minor,
        policy_effect=policy_effect_name(snapshot.policy_found, snapshot.policy_conflict),
        policy_versions=canonical_policy_versions(list(snapshot.versions)),
    )
    if hash_payload(proposal.payload) != proposal.payload_hash:
        raise ProposalDecisionError(409, "payload_hash_mismatch")
    if proposal.payload_hash != hash_payload(expected):
        if proposal.payload.get("amount_minor") != expected["amount_minor"]:
            reason = "amount_changed"
        elif proposal.payload.get("policy_effect") != expected["policy_effect"]:
            reason = "policy_changed"
        elif proposal.payload.get("policy_versions") != expected["policy_versions"]:
            reason = "policy_version_changed"
        else:
            reason = "amount_changed"
        raise ProposalDecisionError(409, reason)
