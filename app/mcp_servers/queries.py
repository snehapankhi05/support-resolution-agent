"""Ownership queries shared by the MCP tools. Tools do not accept a customer id."""

import uuid
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    ActionProposal,
    AuditEvent,
    Order,
    OrderItem,
    RefundOperation,
    Return,
    Ticket,
    WorkflowRun,
)
from app.db.session import SessionLocal
from app.mcp_servers.results import ToolResult, fail, ok
from app.services.eligibility import RefundFacts, evaluate_refund_eligibility
from app.services.embeddings import get_policy_embedder
from app.services.policy import current_refund_policy, policy_effect_name
from app.services.refunds import REFUND_ACTION, hash_payload, refund_payload
from app.services.retrieval import search_citations

ACTIVE_RUN_STATUSES = frozenset({"running", "awaiting_approval"})
PENDING_PROPOSAL_CONSTRAINT = "uq_one_pending_proposal_per_action"


def customer_uuid(raw: str | None) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def own_order(session: Session, customer_id: uuid.UUID, order_id: uuid.UUID) -> Order | None:
    return session.scalar(
        select(Order).where(Order.id == order_id, Order.customer_id == customer_id)
    )


def order_payload(order: Order) -> dict[str, object]:
    delivered = None if order.delivered_at is None else order.delivered_at.isoformat()
    return {
        "id": str(order.id),
        "status": order.status,
        "currency": order.currency.strip(),
        "paid_amount_minor": order.paid_amount_minor,
        "refund_committed_minor": order.refund_committed_minor,
        "delivered_at": delivered,
    }


def return_for_order(session: Session, order_id: uuid.UUID) -> Return | None:
    return session.scalar(select(Return).where(Return.order_id == order_id))


def latest_refund(session: Session, order_id: uuid.UUID) -> RefundOperation | None:
    return session.scalar(
        select(RefundOperation)
        .where(RefundOperation.order_id == order_id)
        .order_by(RefundOperation.created_at.desc())
    )


def search_policy_chunks(session: Session, query: str, *, limit: int = 5) -> list[dict[str, object]]:
    citations = search_citations(
        session, query, embedder=get_policy_embedder(), limit=limit
    )
    return [
        {
            "policy_key": row.policy_key,
            "version": row.version,
            "section_ref": row.section_ref,
            "content": row.content,
            "score": row.score,
        }
        for row in citations
    ]


def read_order(customer_id: uuid.UUID, order_id: uuid.UUID) -> ToolResult:
    with SessionLocal() as session:
        order = own_order(session, customer_id, order_id)
        if order is None:
            return fail("ORDER_NOT_FOUND", NOT_FOUND_ORDER)
        return ok(order_payload(order))


NOT_FOUND_ORDER = "order not found"
NOT_FOUND_TICKET = "ticket not found"


def read_return(customer_id: uuid.UUID, order_id: uuid.UUID) -> ToolResult:
    with SessionLocal() as session:
        order = own_order(session, customer_id, order_id)
        if order is None:
            return fail("ORDER_NOT_FOUND", NOT_FOUND_ORDER)
        row = return_for_order(session, order.id)
        if row is None:
            return ok({"order_id": str(order.id), "return": None})
        received = None if row.received_at is None else row.received_at.isoformat()
        return ok(
            {
                "order_id": str(order.id),
                "return": {
                    "status": row.status,
                    "inspection_status": row.inspection_status,
                    "received_at": received,
                },
            }
        )


def read_refund_status(customer_id: uuid.UUID, order_id: uuid.UUID) -> ToolResult:
    with SessionLocal() as session:
        order = own_order(session, customer_id, order_id)
        if order is None:
            return fail("ORDER_NOT_FOUND", NOT_FOUND_ORDER)
        refund = latest_refund(session, order.id)
        if refund is None:
            return ok({"order_id": str(order.id), "refund": None})
        return ok(
            {
                "order_id": str(order.id),
                "refund": {"status": refund.status, "amount_minor": refund.amount_minor},
            }
        )


def search_policy(query: str) -> ToolResult:
    cleaned = query.strip()
    if not cleaned:
        return fail("INVALID_QUERY", "query is required")
    with SessionLocal() as session:
        citations = search_policy_chunks(session, cleaned)
        return ok({"matched": bool(citations), "citations": citations})


def create_refund_proposal(customer_id: uuid.UUID, ticket_id: uuid.UUID) -> ToolResult:
    """Store one pending proposal for a running or awaiting-approval run. This does not move money."""
    with SessionLocal() as session:
        ticket = session.scalar(
            select(Ticket).where(Ticket.id == ticket_id, Ticket.customer_id == customer_id)
        )
        if ticket is None or ticket.order_id is None:
            return fail("TICKET_NOT_FOUND", NOT_FOUND_TICKET)
        run = session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.ticket_id == ticket.id)
            .order_by(WorkflowRun.started_at.desc())
        )
        if run is None or run.status not in ACTIVE_RUN_STATUSES:
            return fail("NO_ACTIVE_RUN", "no workflow run exists for this ticket")
        existing = _pending_proposal(session, run.id)
        if existing is not None:
            return ok(_proposal_data(existing, created=False))

        order = own_order(session, customer_id, ticket.order_id)
        if order is None:
            return fail("ORDER_NOT_FOUND", NOT_FOUND_ORDER)
        decision = evaluate_refund_eligibility(_facts(session, order, customer_id))
        if not decision.eligible or decision.escalate or decision.amount_minor <= 0:
            return ok(
                {
                    "proposed": False,
                    "reason": decision.reason,
                    "escalate": decision.escalate,
                    "amount_minor": 0,
                    "proposal_id": None,
                }
            )
        snapshot = current_refund_policy(session)
        payload = refund_payload(
            order.id,
            decision.amount_minor,
            policy_effect=policy_effect_name(snapshot.policy_found, snapshot.policy_conflict),
            policy_versions=list(snapshot.versions),
        )
        proposal = ActionProposal(
            run_id=run.id,
            action_type=REFUND_ACTION,
            payload=payload,
            payload_hash=hash_payload(payload),
            status="pending_approval",
        )
        run_id = run.id
        try:
            session.add(proposal)
            session.flush()
            session.add(
                AuditEvent(
                    run_id=run_id,
                    actor=f"customer:{customer_id}",
                    event_type="proposal_created",
                    redacted_payload={
                        "proposal_id": str(proposal.id),
                        "amount_minor": decision.amount_minor,
                    },
                )
            )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if not _is_named_constraint(exc, PENDING_PROPOSAL_CONSTRAINT):
                raise
            winner = _pending_proposal(session, run_id)
            if winner is None:
                raise
            return ok(_proposal_data(winner, created=False))
        return ok(_proposal_data(proposal, created=True))


def _pending_proposal(session: Session, run_id: uuid.UUID) -> ActionProposal | None:
    return session.scalar(
        select(ActionProposal).where(
            ActionProposal.run_id == run_id,
            ActionProposal.action_type == REFUND_ACTION,
            ActionProposal.status == "pending_approval",
        )
    )


def _is_named_constraint(exc: IntegrityError, name: str) -> bool:
    """True only for the named Postgres constraint. Every other error stays raised."""
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None) == name


def _proposal_data(proposal: ActionProposal, *, created: bool) -> dict[str, object]:
    amount = proposal.payload.get("amount_minor")
    return {
        "proposed": True,
        "created": created,
        "proposal_id": str(proposal.id),
        "status": proposal.status,
        "amount_minor": amount,
    }


def _facts(session: Session, order: Order, customer_id: uuid.UUID) -> RefundFacts:
    row = return_for_order(session, order.id)
    refund = latest_refund(session, order.id)
    returned = 0
    if row is not None:
        amounts = session.scalars(
            select(OrderItem.paid_amount_minor).where(OrderItem.order_id == order.id)
        ).all()
        returned = sum(amounts)
    snapshot = current_refund_policy(session)
    requested = None if row is None else row.created_at
    if requested is not None and requested.tzinfo is None:
        requested = requested.replace(tzinfo=UTC)
    delivered = order.delivered_at
    if delivered is not None and delivered.tzinfo is None:
        delivered = delivered.replace(tzinfo=UTC)
    return RefundFacts(
        order_status=order.status,
        order_customer_id=order.customer_id,
        requesting_customer_id=customer_id,
        delivered_at=delivered,
        return_requested_at=requested,
        return_status=None if row is None else row.status,
        inspection_status=None if row is None else row.inspection_status,
        paid_amount_minor=order.paid_amount_minor,
        refund_committed_minor=order.refund_committed_minor,
        returned_items_paid_minor=returned,
        existing_refund_status=None if refund is None else refund.status,
        existing_refund_amount_minor=None if refund is None else refund.amount_minor,
        policy_found=snapshot.policy_found,
        policy_conflict=snapshot.policy_conflict,
    )
