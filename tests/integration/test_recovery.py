"""Lost queue messages and missed ticket writes are recovered without another payment."""

import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import ActionProposal, AuditEvent, Order, RefundOperation, Ticket, WorkflowRun
from app.db.session import SessionLocal
from app.services.recovery import retry_failed_ticket_updates
from app.services.refunds import _ProviderCall, _save_result
from app.worker import discover_payments, enqueue_open_reconciliations
from tests.integration.test_reconciliation import _operation
from tests.integration.test_refund_execution import PAID, _factory

pytestmark = pytest.mark.integration


def _plan(operation_id: uuid.UUID) -> _ProviderCall:
    with SessionLocal() as session:
        operation = session.get(RefundOperation, operation_id)
        proposal = session.get(ActionProposal, operation.proposal_id)
        run = session.get(WorkflowRun, proposal.run_id)
        return _ProviderCall(
            operation_id=operation.id,
            order_id=operation.order_id,
            proposal_id=proposal.id,
            run_id=run.id,
            ticket_id=run.ticket_id,
            amount_minor=operation.amount_minor,
            idempotency_key=operation.idempotency_key,
        )


def test_a_lost_reconcile_enqueue_is_discovered_from_the_ledger(engine, monkeypatch):
    bind, sessions = _factory()
    with sessions.begin() as session:
        operation_id, order_id = _operation(session, "reserved")
    plan = _plan(operation_id)

    def fail(_operation_id: uuid.UUID) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.worker.enqueue_reconciliation", fail)
    try:
        _save_result(sessions, plan, "unknown", None)
        published: list[uuid.UUID] = []
        monkeypatch.setattr("app.worker.enqueue_reconciliation", published.append)
        found = enqueue_open_reconciliations()
        with SessionLocal() as session:
            row = session.get(RefundOperation, operation_id)
            committed = session.scalar(
                select(Order.refund_committed_minor).where(Order.id == order_id)
            )
            audits = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == "refund_reconcile_enqueue_failed",
                    AuditEvent.redacted_payload["operation_id"].as_string() == str(operation_id),
                )
            )
        assert row is not None and row.status == "unknown"
        assert committed == PAID
        assert audits == 1
        assert operation_id in published
        assert found >= 1
        assert discover_payments.name == "payments.discover"
    finally:
        bind.dispose()


def test_a_missed_ticket_update_resolves_without_another_payment(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        operation_id, order_id = _operation(session, "succeeded")
    try:
        with SessionLocal() as session:
            proposal_id = session.get(RefundOperation, operation_id).proposal_id
            ticket_id = session.scalar(
                select(WorkflowRun.ticket_id)
                .join(ActionProposal, ActionProposal.run_id == WorkflowRun.id)
                .where(ActionProposal.id == proposal_id)
            )
            run_id = session.scalar(
                select(ActionProposal.run_id).where(ActionProposal.id == proposal_id)
            )
        with sessions.begin() as session:
            session.add(
                AuditEvent(
                    run_id=run_id,
                    actor="system",
                    event_type="ticket_update_failed",
                    redacted_payload={"proposal_id": str(proposal_id), "status": "succeeded"},
                )
            )
        first = retry_failed_ticket_updates(sessions)
        retried = _retried_count(sessions, proposal_id)
        retry_failed_ticket_updates(sessions)
        with SessionLocal() as session:
            ticket = session.get(Ticket, ticket_id)
            operation = session.get(RefundOperation, operation_id)
            committed = session.scalar(
                select(Order.refund_committed_minor).where(Order.id == order_id)
            )
            payments = session.scalar(
                select(func.count())
                .select_from(RefundOperation)
                .where(RefundOperation.proposal_id == operation.proposal_id)
            )
        assert first >= 1
        assert retried >= 1
        assert _retried_count(sessions, proposal_id) == retried
        assert ticket is not None and ticket.status == "resolved"
        assert operation.status == "succeeded"
        assert committed == PAID
        assert payments == 1
    finally:
        bind.dispose()


def test_a_reopened_ticket_stays_open(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        operation_id, order_id = _operation(session, "succeeded")
    try:
        with SessionLocal() as session:
            proposal_id = session.get(RefundOperation, operation_id).proposal_id
            ticket_id = session.scalar(
                select(WorkflowRun.ticket_id)
                .join(ActionProposal, ActionProposal.run_id == WorkflowRun.id)
                .where(ActionProposal.id == proposal_id)
            )
            run_id = session.scalar(
                select(ActionProposal.run_id).where(ActionProposal.id == proposal_id)
            )
        retry_failed_ticket_updates(sessions)
        with SessionLocal() as session:
            before = session.get(Ticket, ticket_id).status
        with sessions.begin() as session:
            session.add(
                AuditEvent(
                    run_id=run_id,
                    actor="system",
                    event_type="ticket_update_failed",
                    redacted_payload={"proposal_id": str(proposal_id), "status": "succeeded"},
                )
            )
        retry_failed_ticket_updates(sessions)
        with sessions.begin() as session:
            ticket = session.get(Ticket, ticket_id)
            ticket.status = "open"
        retry_failed_ticket_updates(sessions)
        with SessionLocal() as session:
            ticket = session.get(Ticket, ticket_id)
            operation = session.get(RefundOperation, operation_id)
            committed = session.scalar(
                select(Order.refund_committed_minor).where(Order.id == order_id)
            )
        assert before == "awaiting_approval"
        assert ticket.status == "open"
        assert operation.status == "succeeded"
        assert committed == PAID
    finally:
        bind.dispose()


def _retried_count(sessions, proposal_id) -> int:
    with sessions() as session:
        found = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.event_type == "ticket_update_retried",
                AuditEvent.redacted_payload["proposal_id"].as_string() == str(proposal_id),
            )
        )
    return int(found or 0)
