"""Lookup-before-pay recovery. These tests commit only to support_test."""

import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import uvicorn
from sqlalchemy import select

from app.db.models import ActionProposal, Approval, Order, RefundOperation, Ticket, WorkflowRun
from app.services.payments import HttpPaymentProvider, ProviderUnavailable
from app.services.refunds import execute_approved_refund, refund_idempotency_key
from mock_services.payments.app import create_app
from tests.integration.test_refund_execution import (
    PAID,
    RecordingProvider,
    _approved_case,
    _factory,
)

pytestmark = pytest.mark.integration


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def payment_server(tmp_path):
    app = create_app(tmp_path / "provider.sqlite")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            httpx.get(f"{base_url}/health", timeout=0.2)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("mock payment provider did not start")
    yield base_url
    server.should_exit = True
    thread.join(timeout=5)


def test_changed_provider_payload_does_not_release_or_pay(engine, payment_server):
    bind, sessions = _factory()
    with sessions.begin() as session:
        proposal_id = _approved_case(session)
    key = refund_idempotency_key(proposal_id)
    seeded = httpx.post(
        f"{payment_server}/refunds",
        json={"idempotency_key": key, "amount_minor": 1, "order_id": "other-order"},
        timeout=2,
    )
    assert seeded.status_code == 200
    client = httpx.Client(base_url=payment_server, timeout=2)
    provider = HttpPaymentProvider(payment_server, client=client)
    try:
        result = execute_approved_refund(sessions, proposal_id, provider)
        assert result.outcome == "conflict"
        assert result.provider_called is False
        with sessions() as session:
            operation = session.scalar(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            )
            order = session.get(Order, operation.order_id)
            assert operation.status == "reserved"
            assert operation.amount_minor == PAID
            assert order.refund_committed_minor == PAID
        assert httpx.get(f"{payment_server}/stats", timeout=2).json()["attempts"] == 1
    finally:
        client.close()
        bind.dispose()


def test_expired_approval_with_no_provider_record_releases(engine, payment_server):
    bind, sessions = _factory()
    with sessions.begin() as session:
        proposal_id = _approved_case(session)
        approval = session.scalar(select(Approval).where(Approval.proposal_id == proposal_id))
        approval.expires_at = datetime.now(UTC) - timedelta(hours=1)
        proposal = session.get(ActionProposal, proposal_id)
        run = session.get(WorkflowRun, proposal.run_id)
        ticket = session.get(Ticket, run.ticket_id)
        order = session.get(Order, ticket.order_id)
        order.refund_committed_minor = PAID
        session.add(
            RefundOperation(
                order_id=order.id,
                proposal_id=proposal.id,
                amount_minor=PAID,
                idempotency_key=refund_idempotency_key(proposal.id),
                status="reserved",
            )
        )
    client = httpx.Client(base_url=payment_server, timeout=2)
    provider = HttpPaymentProvider(payment_server, client=client)
    try:
        result = execute_approved_refund(sessions, proposal_id, provider)
        assert result.outcome == "rejected"
        assert result.reason == "approval_expired"
        assert result.provider_called is False
        with sessions() as session:
            operation = session.scalar(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            )
            order = session.get(Order, operation.order_id)
            assert operation.status == "failed"
            assert order.refund_committed_minor == 0
        assert httpx.get(f"{payment_server}/stats", timeout=2).json()["attempts"] == 0
    finally:
        client.close()
        bind.dispose()


def test_failed_lookup_leaves_unknown_unchanged(engine):
    bind, sessions = _factory()

    class LookupFails(RecordingProvider):
        def __init__(self):
            super().__init__(error=ProviderUnavailable("timed out"))
            self.lookups = 0

        def lookup_refund(self, *, idempotency_key: str):
            self.lookups += 1
            if self.lookups == 1:
                return
            raise ProviderUnavailable("lookup down")

    provider = LookupFails()
    with sessions.begin() as session:
        proposal_id = _approved_case(session)
    try:
        first = execute_approved_refund(sessions, proposal_id, provider)
        second = execute_approved_refund(sessions, proposal_id, provider)
        assert first.outcome == "unknown"
        assert second.reason == "lookup_unavailable"
        assert second.provider_called is False
        assert len(provider.calls) == 1
        with sessions() as session:
            operation = session.scalar(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            )
            assert operation.status == "unknown"
    finally:
        bind.dispose()


def test_ten_parallel_payments_post_once(engine, payment_server):
    bind, sessions = _factory()
    with sessions.begin() as session:
        proposal_id = _approved_case(session)
    barrier = threading.Barrier(10)
    results = []
    errors = []
    lock = threading.Lock()

    def attempt():
        barrier.wait()
        client = httpx.Client(base_url=payment_server, timeout=2)
        provider = HttpPaymentProvider(payment_server, client=client)
        try:
            result = execute_approved_refund(sessions, proposal_id, provider)
            with lock:
                results.append(result)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            client.close()

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert errors == []
        assert sum(1 for result in results if result.provider_called) == 1
        assert sum(1 for result in results if result.outcome == "succeeded") == 1
        assert httpx.get(f"{payment_server}/stats", timeout=2).json()["attempts"] == 1
        with sessions() as session:
            operations = session.scalars(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            ).all()
            assert len(operations) == 1
            assert operations[0].status == "succeeded"
    finally:
        bind.dispose()
