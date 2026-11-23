"""Refund execution against the mock provider's failure switches."""

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from sqlalchemy import select

from app.db.models import Order, RefundOperation
from app.services.payments import HttpPaymentProvider
from app.services.refunds import execute_approved_refund
from mock_services.payments.app import create_app
from tests.integration.test_refund_execution import PAID, _approved_case, _factory

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
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
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


def _run(base_url: str, timeout: float):
    bind, sessions = _factory()
    client = httpx.Client(base_url=base_url, timeout=timeout)
    provider = HttpPaymentProvider(base_url, client=client, timeout=timeout)
    with sessions.begin() as session:
        proposal_id = _approved_case(session)
    try:
        first = execute_approved_refund(sessions, proposal_id, provider)
        second = execute_approved_refund(sessions, proposal_id, provider)
        with sessions() as session:
            operation = session.scalar(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            )
            order = session.get(Order, operation.order_id)
        return first, second, operation, order
    finally:
        client.close()
        bind.dispose()


def test_timeout_after_accept_is_unknown_and_not_sent_again(engine, payment_server):
    httpx.post(
        f"{payment_server}/control",
        json={"mode": "timeout_after_accept", "delay_seconds": 1},
        timeout=2,
    )
    first, second, operation, order = _run(payment_server, timeout=0.2)
    stored = httpx.get(f"{payment_server}/refunds/{operation.idempotency_key}", timeout=2)
    assert first.outcome == "unknown"
    assert first.provider_called is True
    assert second.outcome == "succeeded"
    assert second.provider_called is False
    assert operation.status == "succeeded"
    assert operation.provider_reference == stored.json()["provider_reference"]
    assert order.refund_committed_minor == PAID
    assert stored.status_code == 200
    assert stored.json()["status"] == "succeeded"
    assert stored.json()["amount_minor"] == PAID
    assert httpx.get(f"{payment_server}/stats", timeout=2).json()["attempts"] == 1


def test_http_500_is_unknown_and_not_sent_again(engine, payment_server):
    httpx.post(f"{payment_server}/control", json={"mode": "error_500"}, timeout=2)
    first, second, operation, order = _run(payment_server, timeout=2)
    missing = httpx.get(f"{payment_server}/refunds/{operation.idempotency_key}", timeout=2)
    assert first.outcome == "unknown"
    assert second.outcome == "unknown"
    assert second.reason == "provider_has_no_record"
    assert second.provider_called is False
    assert operation.status == "unknown"
    assert order.refund_committed_minor == PAID
    assert missing.status_code == 404
    assert httpx.get(f"{payment_server}/stats", timeout=2).json() == {
        "attempts": 1,
        "operations": 0,
    }


def test_delay_then_success_is_recorded_once(engine, payment_server):
    httpx.post(
        f"{payment_server}/control",
        json={"mode": "delay", "delay_seconds": 0.2},
        timeout=2,
    )
    first, second, operation, _order = _run(payment_server, timeout=2)
    stored = httpx.get(f"{payment_server}/refunds/{operation.idempotency_key}", timeout=2)
    assert first.outcome == "succeeded"
    assert first.provider_called is True
    assert second.provider_called is False
    assert operation.status == "succeeded"
    assert operation.provider_reference == stored.json()["provider_reference"]
    assert httpx.get(f"{payment_server}/stats", timeout=2).json()["attempts"] == 1
