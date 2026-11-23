"""Finished non-payment outcomes keep the code sentence on the ticket."""

import uuid

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.api.deps import Principal
from app.auth.passwords import hash_password
from app.db.models import Customer, LoginAccount, StaffUser, Ticket
from app.db.session import SessionLocal
from app.main import app
from app.services.approvals import approve_or_reject_proposal
from app.workflows.support_graph import build_support_graph, workflow_config
from tests.integration.test_refund_execution import RecordingProvider, _factory
from tests.integration.test_support_graph import _case

pytestmark = pytest.mark.integration
client = TestClient(app)


def _customer_token(customer_id: uuid.UUID) -> str:
    password = f"pw-{uuid.uuid4().hex}"
    with SessionLocal() as session:
        customer = session.get(Customer, customer_id)
        email = customer.email
        session.add(
            LoginAccount(
                email=email,
                password_hash=hash_password(password),
                customer_id=customer.id,
            )
        )
        session.commit()
    body = client.post("/auth/token", json={"email": email, "password": password}).json()
    return body["access_token"]


def _message(ticket_id: uuid.UUID, token: str) -> str | None:
    response = client.get(
        f"/tickets/{ticket_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    return response.json()["message"]


def test_get_returns_stored_sentences_and_hides_another_customers_ticket(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        order_ticket, _order_run, order_thread = _case(
            session, category="order_status", with_policy=False
        )
        late_ticket, _late_run, late_thread = _case(
            session,
            category="refund_request",
            delivered_days=50,
            return_requested_days_ago=10,
        )
        damaged_ticket, _damaged_run, damaged_thread = _case(session, category="damaged_item")
        rejected_ticket, _rejected_run, rejected_thread = _case(session, category="refund_request")
        reviewer = StaffUser(
            external_auth_subject=f"phase8|{uuid.uuid4().hex}",
            name="Reviewer",
            role="reviewer",
        )
        session.add(reviewer)
        session.flush()
        reviewer_id = reviewer.id
    with SessionLocal() as session:
        customers = {
            ticket_id: session.get(Ticket, ticket_id).customer_id
            for ticket_id in (order_ticket, late_ticket, damaged_ticket, rejected_ticket)
        }
    provider = RecordingProvider()
    try:
        build_support_graph(provider, InMemorySaver()).invoke(
            {"ticket_id": str(order_ticket)}, workflow_config(order_thread)
        )
        build_support_graph(provider, InMemorySaver()).invoke(
            {"ticket_id": str(late_ticket)}, workflow_config(late_thread)
        )
        build_support_graph(provider, InMemorySaver()).invoke(
            {"ticket_id": str(damaged_ticket)}, workflow_config(damaged_thread)
        )
        paused = build_support_graph(provider, InMemorySaver()).invoke(
            {"ticket_id": str(rejected_ticket)}, workflow_config(rejected_thread)
        )
        proposal_id = uuid.UUID(paused["__interrupt__"][0].value["proposal_id"])
        with sessions.begin() as session:
            approve_or_reject_proposal(
                session,
                proposal_id,
                Principal(
                    actor="staff",
                    id=reviewer_id,
                    email="reviewer@phase8.test",
                    name="Reviewer",
                    role="reviewer",
                ),
                "reject",
            )
        build_support_graph(provider, InMemorySaver()).invoke(
            {"ticket_id": str(rejected_ticket)}, workflow_config(rejected_thread)
        )
        order_token = _customer_token(customers[order_ticket])
        late_token = _customer_token(customers[late_ticket])
        assert _message(order_ticket, order_token) == "The latest order status is delivered."
        late_message = _message(late_ticket, late_token)
        assert late_message is not None and "30 days" in late_message
        assert _message(damaged_ticket, _customer_token(customers[damaged_ticket])) == (
            "A support specialist will review your case."
        )
        assert _message(rejected_ticket, _customer_token(customers[rejected_ticket])) == (
            "A reviewer declined this refund request."
        )
        hidden = client.get(
            f"/tickets/{order_ticket}",
            headers={"Authorization": f"Bearer {late_token}"},
        )
        assert hidden.status_code == 404
        assert provider.calls == []
    finally:
        bind.dispose()
