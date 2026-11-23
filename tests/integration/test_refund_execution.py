"""Refund execution against Postgres. The concurrency test is the Step 2 gate."""

import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.db.models import (
    ActionProposal,
    Approval,
    Customer,
    Order,
    OrderItem,
    PolicyChunk,
    PolicyDocument,
    RefundOperation,
    Return,
    StaffUser,
    Ticket,
    WorkflowRun,
)
from app.services.payments import ProviderRecord, ProviderResult, ProviderUnavailable
from app.services.refunds import execute_approved_refund, hash_payload, refund_payload

pytestmark = pytest.mark.integration

PAID = 50000


class RecordingProvider:
    def __init__(self, result: ProviderResult | None = None, error: Exception | None = None):
        self.result = result or ProviderResult(status="succeeded", provider_reference="pay_test")
        self.error = error
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def create_refund(self, *, idempotency_key: str, amount_minor: int, order_id: str) -> ProviderResult:
        with self._lock:
            self.calls.append(idempotency_key)
        time.sleep(0.2)
        if self.error is not None:
            raise self.error
        if amount_minor != PAID:
            raise AssertionError(amount_minor)
        return self.result

    def lookup_refund(self, *, idempotency_key: str) -> ProviderRecord | None:
        return None


def _factory():
    engine = create_engine(get_settings().database_url, pool_size=20, max_overflow=5)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


TEST_POLICY_URI = "memory://test-refund-policy"
TEST_POLICY_VERSIONS = [{"policy_key": "test-refund-decision", "version": 1}]


def ensure_test_refund_policy(session, effect: str = "allow") -> None:
    """One refund decision row for execution tests. The integration fixture deletes it."""
    document = session.scalar(
        select(PolicyDocument).where(
            PolicyDocument.source_uri == TEST_POLICY_URI,
            PolicyDocument.version == 1,
        )
    )
    metadata = {"applies_to": "refund", "effect": effect}
    if document is None:
        document = PolicyDocument(
            policy_key="test-refund-decision",
            version=1,
            effective_from=date.today(),
            source_uri=TEST_POLICY_URI,
        )
        session.add(document)
        session.flush()
        session.add(
            PolicyChunk(
                document_id=document.id,
                section_ref="decision",
                content="test refund decision",
                chunk_metadata=metadata,
            )
        )
        session.flush()
        return
    chunk = session.scalar(select(PolicyChunk).where(PolicyChunk.document_id == document.id))
    chunk.chunk_metadata = metadata
    flag_modified(chunk, "chunk_metadata")
    session.flush()


def _approved_case(
    session,
    *,
    payload_amount: int = PAID,
    payload_hash: str | None = None,
    policy_effect: str = "allow",
):
    now = datetime.now(UTC)
    ensure_test_refund_policy(session, "allow")
    marker = uuid.uuid4().hex
    reviewer = StaffUser(
        external_auth_subject=f"test|reviewer-{marker}", name="Reviewer", role="reviewer"
    )
    customer = Customer(
        external_auth_subject=f"test|customer-{marker}",
        name="Test Customer",
        email=f"{marker}@test.example",
    )
    session.add_all([reviewer, customer])
    session.flush()
    order = Order(
        customer_id=customer.id,
        status="delivered",
        paid_amount_minor=PAID,
        refund_committed_minor=0,
        delivered_at=now - timedelta(days=5),
    )
    session.add(order)
    session.flush()
    session.add(
        OrderItem(
            order_id=order.id,
            product_id="sku-test",
            product_name="Test item",
            quantity=1,
            paid_amount_minor=PAID,
        )
    )
    session.add(
        Return(
            order_id=order.id,
            status="received",
            inspection_status="passed",
            received_at=now - timedelta(days=1),
            created_at=now - timedelta(days=2),
        )
    )
    ticket = Ticket(
        customer_id=customer.id,
        order_id=order.id,
        category="refund_request",
        status="awaiting_approval",
        summary="test refund",
    )
    session.add(ticket)
    session.flush()
    run = WorkflowRun(ticket_id=ticket.id, thread_id=f"test-thread-{marker}", status="awaiting_approval")
    session.add(run)
    session.flush()
    payload = refund_payload(
        order.id,
        payload_amount,
        policy_effect=policy_effect,
        policy_versions=TEST_POLICY_VERSIONS,
    )
    proposal = ActionProposal(
        run_id=run.id,
        action_type="request_refund_approval",
        payload=payload,
        payload_hash=payload_hash or hash_payload(payload),
        status="approved",
    )
    session.add(proposal)
    session.flush()
    session.add(
        Approval(
            proposal_id=proposal.id,
            reviewer_id=reviewer.id,
            status="approved",
            decided_at=now,
            expires_at=now + timedelta(hours=24),
        )
    )
    session.flush()
    return proposal.id


def test_ten_parallel_refunds_one_success(engine):
    bind, sessions = _factory()
    provider = RecordingProvider()
    with sessions.begin() as session:
        proposal_id = _approved_case(session)
    barrier = threading.Barrier(10)
    results = []
    errors = []
    lock = threading.Lock()

    def attempt():
        barrier.wait()
        try:
            result = execute_approved_refund(sessions, proposal_id, provider)
            with lock:
                results.append(result)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert errors == []
        assert [thread.is_alive() for thread in threads] == [False] * 10
        assert len(results) == 10
        assert sum(1 for result in results if result.provider_called) == 1
        assert sum(1 for result in results if result.outcome == "succeeded") == 1
        assert provider.calls == [provider.calls[0]]
        with sessions() as session:
            operations = session.scalars(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            ).all()
            assert len(operations) == 1
            assert operations[0].status == "succeeded"
            assert operations[0].amount_minor == PAID
            order = session.scalar(
                select(Order).where(Order.id == operations[0].order_id)
            )
            assert order is not None
            assert order.refund_committed_minor == PAID
            assert session.scalar(select(func.count()).select_from(RefundOperation).where(
                RefundOperation.idempotency_key == operations[0].idempotency_key
            )) == 1
    finally:
        bind.dispose()


def test_timeout_is_unknown_and_not_sent_again(engine):
    bind, sessions = _factory()
    provider = RecordingProvider(error=ProviderUnavailable("timed out"))
    with sessions.begin() as session:
        proposal_id = _approved_case(session)
    try:
        first = execute_approved_refund(sessions, proposal_id, provider)
        second = execute_approved_refund(sessions, proposal_id, provider)
        assert first.outcome == "unknown"
        assert first.provider_called is True
        assert second.outcome == "unknown"
        assert second.reason == "provider_has_no_record"
        assert second.provider_called is False
        assert len(provider.calls) == 1
        with sessions() as session:
            operation = session.scalar(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            )
            order = session.get(Order, operation.order_id)
            assert operation.status == "unknown"
            assert order.refund_committed_minor == PAID
    finally:
        bind.dispose()


def test_changed_amount_is_rejected_without_a_provider_call(engine):
    bind, sessions = _factory()
    provider = RecordingProvider()
    with sessions.begin() as session:
        proposal_id = _approved_case(session, payload_amount=100)
    try:
        result = execute_approved_refund(sessions, proposal_id, provider)
        assert result.outcome == "rejected"
        assert result.reason == "amount_changed"
        assert provider.calls == []
        with sessions() as session:
            count = session.scalar(
                select(func.count()).select_from(RefundOperation).where(
                    RefundOperation.proposal_id == proposal_id
                )
            )
            assert count == 0
    finally:
        bind.dispose()
