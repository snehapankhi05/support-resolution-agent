"""Find open refunds and unfinished ticket writes. This module does not call the provider."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActionProposal, AuditEvent, RefundOperation, Ticket, WorkflowRun
from app.services.refunds import _audit


def retry_failed_ticket_updates(sessions: sessionmaker[Session]) -> int:
    """Resolve a ticket only when its latest ticket-write audit is still a failure.

    The refund is already `succeeded`, so this does not call the provider or move paise.
    A later `ticket_update_retried` audit consumes that failure; reopening the ticket
    does not create a new failure, so the reopen stays open.
    """
    with sessions() as session:
        pairs = session.execute(
            select(RefundOperation.id, Ticket.id, ActionProposal.id)
            .join(ActionProposal, ActionProposal.id == RefundOperation.proposal_id)
            .join(WorkflowRun, WorkflowRun.id == ActionProposal.run_id)
            .join(Ticket, Ticket.id == WorkflowRun.ticket_id)
            .where(
                RefundOperation.status == "succeeded",
                Ticket.status != "resolved",
            )
        ).all()
        pairs = [
            (operation_id, ticket_id)
            for operation_id, ticket_id, proposal_id in pairs
            if _ticket_failure_is_pending(session, proposal_id)
        ]
    updated = 0
    for operation_id, ticket_id in pairs:
        if _resolve_succeeded_ticket(sessions, operation_id, ticket_id):
            updated += 1
    return updated


def schedule_recovery(sessions: sessionmaker[Session]) -> int:
    """Retry ticket writes, then publish every open refund. Broker errors stay off the ledger."""
    updated = retry_failed_ticket_updates(sessions)
    try:
        from app.worker import BROKER_ERRORS, enqueue_open_reconciliations

        enqueue_open_reconciliations()
    except BROKER_ERRORS:
        return updated
    return updated


def _resolve_succeeded_ticket(
    sessions: sessionmaker[Session],
    operation_id: uuid.UUID,
    ticket_id: uuid.UUID,
) -> bool:
    try:
        with sessions.begin() as session:
            operation = session.get(RefundOperation, operation_id, with_for_update=True)
            ticket = session.get(Ticket, ticket_id, with_for_update=True)
            if (
                operation is None
                or ticket is None
                or operation.status != "succeeded"
                or ticket.status == "resolved"
            ):
                return False
            proposal = session.get(ActionProposal, operation.proposal_id)
            if proposal is None or not _ticket_failure_is_pending(session, proposal.id):
                return False
            run_id = proposal.run_id
            ticket.status = "resolved"
            ticket.updated_at = datetime.now(UTC)
            _audit(
                session,
                run_id,
                "ticket_update_retried",
                {
                    "proposal_id": str(operation.proposal_id),
                    "operation_id": str(operation.id),
                    "status": "succeeded",
                },
            )
        return True
    except SQLAlchemyError:
        return False


def _ticket_failure_is_pending(session: Session, proposal_id: uuid.UUID) -> bool:
    """True when ticket_update_failed is newer than any ticket_update_retried for this proposal."""
    key = str(proposal_id)
    failed_at = session.scalar(_latest_audit(key, "ticket_update_failed"))
    if failed_at is None:
        return False
    retried_at = session.scalar(_latest_audit(key, "ticket_update_retried"))
    return retried_at is None or failed_at > retried_at


def _latest_audit(proposal_id: str, event_type: str):
    return (
        select(func.max(AuditEvent.created_at))
        .where(
            AuditEvent.event_type == event_type,
            AuditEvent.redacted_payload["proposal_id"].as_string() == proposal_id,
        )
    )
