"""The reconciler looks up a stored key. It does not create a payment."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import (
    ActionProposal,
    AuditEvent,
    Customer,
    Order,
    RefundOperation,
    Ticket,
    WorkflowRun,
)
from app.services.payments import ProviderRecord, ProviderUnavailable
from app.services.reconciliation import ABSENCE_GRACE, reconcile_operation
from tests.integration.test_refund_execution import PAID, _factory

pytestmark = pytest.mark.integration


class LookupOnly:
    def __init__(self, record: ProviderRecord | None = None, error: Exception | None = None):
        self.record = record
        self.error = error
        self.lookups = 0
        self.creates = 0

    def lookup_refund(self, *, idempotency_key: str) -> ProviderRecord | None:
        self.lookups += 1
        if self.error is not None:
            raise self.error
        return self.record

    def create_refund(self, *, idempotency_key: str, amount_minor: int, order_id: str) -> None:
        self.creates += 1
        raise AssertionError("reconcile posted a refund")


def _operation(session, status: str, *, updated_at: datetime | None = None) -> tuple[uuid.UUID, uuid.UUID]:
    marker = uuid.uuid4().hex
    customer = Customer(
        external_auth_subject=f"reconcile|{marker}",
        name="Buyer",
        email=f"reconcile-{marker}@example.com",
    )
    session.add(customer)
    session.flush()
    order = Order(
        customer_id=customer.id,
        status="delivered",
        paid_amount_minor=PAID,
        refund_committed_minor=PAID,
        delivered_at=datetime.now(UTC) - timedelta(days=5),
    )
    session.add(order)
    session.flush()
    ticket = Ticket(
        customer_id=customer.id,
        order_id=order.id,
        category="refund_request",
        status="awaiting_approval",
        summary="refund",
    )
    session.add(ticket)
    session.flush()
    run = WorkflowRun(ticket_id=ticket.id, thread_id=f"reconcile-{marker}", status="completed")
    session.add(run)
    session.flush()
    proposal = ActionProposal(
        run_id=run.id,
        action_type="request_refund_approval",
        payload={"action_type": "request_refund_approval", "amount_minor": PAID},
        payload_hash="reconcile-test",
        status="approved",
    )
    session.add(proposal)
    session.flush()
    operation = RefundOperation(
        order_id=order.id,
        proposal_id=proposal.id,
        amount_minor=PAID,
        idempotency_key=f"refund-{proposal.id}",
        status=status,
        updated_at=updated_at or datetime.now(UTC),
    )
    session.add(operation)
    session.flush()
    return operation.id, order.id


def _committed(session, order_id) -> int:
    return session.scalar(select(Order.refund_committed_minor).where(Order.id == order_id))


def test_a_matching_provider_record_is_adopted_once(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        unknown_id, unknown_order = _operation(session, "unknown")
        processing_id, processing_order = _operation(session, "processing")
    provider = LookupOnly(
        ProviderRecord(status="succeeded", amount_minor=PAID, order_id="", provider_reference="pay_1")
    )
    try:
        with sessions() as session:
            unknown_key = session.get(RefundOperation, unknown_id).idempotency_key
            unknown_order_id = str(session.get(RefundOperation, unknown_id).order_id)
            processing_order_id = str(session.get(RefundOperation, processing_id).order_id)
        provider.record = ProviderRecord(
            status="succeeded",
            amount_minor=PAID,
            order_id=unknown_order_id,
            provider_reference="pay_1",
        )
        first = reconcile_operation(sessions, unknown_id, provider)
        second = reconcile_operation(sessions, unknown_id, provider)
        provider.record = ProviderRecord(
            status="succeeded",
            amount_minor=PAID,
            order_id=processing_order_id,
            provider_reference="pay_2",
        )
        adopted_processing = reconcile_operation(sessions, processing_id, provider)
        with sessions() as session:
            unknown = session.get(RefundOperation, unknown_id)
            processing = session.get(RefundOperation, processing_id)
            audits = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == "refund_provider_result",
                    AuditEvent.redacted_payload["proposal_id"].as_string() == str(unknown.proposal_id),
                )
            )
            unknown_committed = _committed(session, unknown_order)
            processing_committed = _committed(session, processing_order)
        assert first.outcome == "adopted" and first.status == "succeeded"
        assert second.outcome == "settled"
        assert adopted_processing.outcome == "adopted"
        assert unknown.status == "succeeded" and unknown.provider_reference == "pay_1"
        assert processing.status == "succeeded"
        assert unknown_committed == PAID
        assert processing_committed == PAID
        assert provider.creates == 0
        assert provider.lookups == 2
        assert audits == 1
        assert unknown_key == f"refund-{unknown.proposal_id}"
    finally:
        bind.dispose()


def test_confirmed_absence_retries_then_releases_only_an_aged_unknown(engine):
    bind, sessions = _factory()
    fresh = datetime.now(UTC)
    aged = fresh - ABSENCE_GRACE - timedelta(seconds=1)
    with sessions.begin() as session:
        fresh_id, fresh_order = _operation(session, "unknown", updated_at=fresh)
        aged_id, aged_order = _operation(session, "unknown", updated_at=aged)
        submitted_id, submitted_order = _operation(session, "submitted", updated_at=aged)
    provider = LookupOnly(None)
    try:
        waiting = reconcile_operation(sessions, fresh_id, provider, now=fresh)
        released = reconcile_operation(sessions, aged_id, provider, now=fresh)
        held = reconcile_operation(sessions, submitted_id, provider, now=fresh)
        with sessions() as session:
            fresh_row = session.get(RefundOperation, fresh_id)
            aged_row = session.get(RefundOperation, aged_id)
            submitted_row = session.get(RefundOperation, submitted_id)
            absent = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "refund_reconcile_absent")
            )
            fresh_committed = _committed(session, fresh_order)
            aged_committed = _committed(session, aged_order)
            submitted_committed = _committed(session, submitted_order)
        assert waiting.outcome == "retry" and waiting.retry_after_seconds == 30
        assert fresh_row.status == "unknown"
        assert fresh_committed == PAID
        assert released.outcome == "released" and aged_row.status == "failed"
        assert aged_committed == 0
        assert held.outcome == "held" and submitted_row.status == "submitted"
        assert submitted_committed == PAID
        assert provider.creates == 0
        assert absent >= 3
    finally:
        bind.dispose()


def test_a_transient_lookup_failure_keeps_the_reservation(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        operation_id, order_id = _operation(session, "unknown")
    provider = LookupOnly(error=ProviderUnavailable("lookup timed out"))
    try:
        result = reconcile_operation(sessions, operation_id, provider)
        again = reconcile_operation(sessions, operation_id, provider)
        with sessions() as session:
            row = session.get(RefundOperation, operation_id)
            audits = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == "refund_reconcile_lookup_failed",
                    AuditEvent.redacted_payload["operation_id"].as_string() == str(operation_id),
                )
            )
            committed = _committed(session, order_id)
        assert result.outcome == "retry" and result.retry_after_seconds == 30
        assert again.outcome == "retry"
        assert row.status == "unknown"
        assert committed == PAID
        assert provider.creates == 0
        assert provider.lookups == 2
        assert audits == 2
    finally:
        bind.dispose()


def test_a_mismatched_record_is_not_adopted(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        operation_id, order_id = _operation(session, "submitted")
        order_key = str(order_id)
    provider = LookupOnly(
        ProviderRecord(
            status="succeeded",
            amount_minor=PAID + 1,
            order_id=order_key,
            provider_reference="pay_other",
        )
    )
    try:
        first = reconcile_operation(sessions, operation_id, provider)
        second = reconcile_operation(sessions, operation_id, provider)
        with sessions() as session:
            row = session.get(RefundOperation, operation_id)
            conflicts = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == "refund_payload_conflict",
                    AuditEvent.redacted_payload["order_id"].as_string() == order_key,
                )
            )
            committed = _committed(session, order_id)
        assert first.outcome == "conflict" and second.outcome == "conflict"
        assert row.status == "submitted" and row.provider_reference is None
        assert committed == PAID
        assert provider.creates == 0
        assert provider.lookups == 2
        assert conflicts == 2
    finally:
        bind.dispose()


def test_a_reserved_provider_record_is_adopted_without_a_post(engine):
    bind, sessions = _factory()
    now = datetime.now(UTC)
    aged = now - ABSENCE_GRACE - timedelta(seconds=1)
    with sessions.begin() as session:
        present_id, present_order = _operation(session, "reserved")
        absent_id, absent_order = _operation(session, "reserved", updated_at=aged)
    with sessions() as session:
        present = session.get(RefundOperation, present_id)
        order_key = str(present.order_id)
    provider = LookupOnly(
        ProviderRecord(
            status="succeeded",
            amount_minor=PAID,
            order_id=order_key,
            provider_reference="pay_reserved",
        )
    )
    try:
        adopted = reconcile_operation(sessions, present_id, provider, now=now)
        provider.record = None
        held = reconcile_operation(sessions, absent_id, provider, now=now)
        with sessions() as session:
            present_row = session.get(RefundOperation, present_id)
            absent_row = session.get(RefundOperation, absent_id)
            present_committed = _committed(session, present_order)
            absent_committed = _committed(session, absent_order)
        assert adopted.outcome == "adopted" and present_row.status == "succeeded"
        assert present_row.provider_reference == "pay_reserved"
        assert held.outcome == "held" and absent_row.status == "reserved"
        assert present_committed == PAID
        assert absent_committed == PAID
        assert provider.creates == 0
    finally:
        bind.dispose()
