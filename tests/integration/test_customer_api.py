"""Customer orders and tickets stay inside the token's account."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.db.models import (
    ActionProposal,
    Customer,
    LoginAccount,
    Order,
    OrderItem,
    RefundOperation,
    StaffUser,
    Ticket,
    WorkflowRun,
)
from app.db.session import SessionLocal
from app.main import app
from app.services.refunds import hash_payload, refund_idempotency_key, refund_payload

pytestmark = pytest.mark.integration
client = TestClient(app)


def _login(session: Session, *, kind: str) -> tuple[str, uuid.UUID]:
    marker = uuid.uuid4().hex
    email = f"{kind}-{marker}@example.com"
    password = f"pw-{marker}"
    if kind == "customer":
        person = Customer(external_auth_subject=f"cust|{marker}", name="Buyer", email=email)
        session.add(person)
        session.flush()
        session.add(
            LoginAccount(email=email, password_hash=hash_password(password), customer_id=person.id)
        )
    else:
        person = StaffUser(external_auth_subject=f"staff|{marker}", name="Reviewer", role="reviewer")
        session.add(person)
        session.flush()
        session.add(
            LoginAccount(
                email=email, password_hash=hash_password(password), staff_user_id=person.id
            )
        )
    session.commit()
    token = client.post("/auth/token", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return token, person.id


def _order(session: Session, customer_id: uuid.UUID) -> uuid.UUID:
    order = Order(
        customer_id=customer_id,
        status="delivered",
        paid_amount_minor=249900,
        refund_committed_minor=0,
        delivered_at=datetime.now(UTC) - timedelta(days=3),
    )
    session.add(order)
    session.commit()
    return order.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_customer_sees_only_their_orders(engine):
    with SessionLocal() as session:
        token_a, customer_a = _login(session, kind="customer")
        _token_b, customer_b = _login(session, kind="customer")
        own = _order(session, customer_a)
        other = _order(session, customer_b)
    listed = client.get("/orders", headers=_auth(token_a), params={"customer_id": str(customer_b)})
    assert listed.status_code == 200
    ids = {row["id"] for row in listed.json()}
    assert str(own) in ids
    assert str(other) not in ids
    assert all(row["paid_amount_minor"] == 249900 for row in listed.json() if row["id"] == str(own))
    foreign = client.get(f"/orders/{other}", headers=_auth(token_a))
    missing = client.get(f"/orders/{uuid.uuid4()}", headers=_auth(token_a))
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json() == missing.json()
    own_body = client.get(f"/orders/{own}", headers=_auth(token_a))
    assert own_body.status_code == 200
    assert own_body.json()["id"] == str(own)
    assert own_body.json()["refund_committed_minor"] == 0
    assert own_body.json()["product_names"] == []


def test_staff_cannot_use_customer_order_or_ticket_routes(engine):
    with SessionLocal() as session:
        token, _customer = _login(session, kind="customer")
        staff, _staff = _login(session, kind="staff")
        order_id = _order(session, _customer)
    assert client.get("/orders", headers=_auth(staff)).status_code == 403
    assert client.get(f"/orders/{order_id}", headers=_auth(staff)).status_code == 403
    created = client.post(
        "/tickets",
        headers=_auth(token),
        json={"order_id": str(order_id), "category": "order_status", "summary": "Where is it?"},
    )
    assert created.status_code == 201
    ticket_id = created.json()["id"]
    assert client.get("/tickets", headers=_auth(staff)).status_code == 403
    assert client.get(f"/tickets/{ticket_id}", headers=_auth(staff)).status_code == 403


def test_ticket_creation_uses_the_token_customer(engine):
    with SessionLocal() as session:
        token, customer_id = _login(session, kind="customer")
        _other_token, other_id = _login(session, kind="customer")
        own = _order(session, customer_id)
        foreign = _order(session, other_id)
    rejected = client.post(
        "/tickets",
        headers=_auth(token),
        json={
            "order_id": str(foreign),
            "category": "refund_request",
            "summary": "Refund please",
            "customer_id": str(other_id),
        },
    )
    assert rejected.status_code == 422
    missing = client.post(
        "/tickets",
        headers=_auth(token),
        json={"order_id": str(foreign), "category": "refund_request", "summary": "Refund please"},
    )
    assert missing.status_code == 404
    with SessionLocal() as session:
        count = session.scalar(
            select(func.count()).select_from(Ticket).where(Ticket.order_id == foreign)
        )
        assert count == 0
    created = client.post(
        "/tickets",
        headers=_auth(token),
        json={"order_id": str(own), "category": "refund_request", "summary": "  Headphones  "},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "open"
    assert body["summary"] == "Headphones"
    assert body["refund_status"] is None
    assert body["message"] is None
    assert body["product_names"] == []
    with SessionLocal() as session:
        ticket = session.get(Ticket, uuid.UUID(body["id"]))
        runs = session.scalar(
            select(func.count()).select_from(WorkflowRun).where(WorkflowRun.ticket_id == ticket.id)
        )
        assert ticket.customer_id == customer_id
        assert runs == 0
    listed = client.get("/tickets", headers=_auth(_other_token))
    assert listed.status_code == 200
    assert body["id"] not in {row["id"] for row in listed.json()}
    hidden = client.get(f"/tickets/{body['id']}", headers=_auth(_other_token))
    assert hidden.status_code == 404


def test_ticket_message_uses_the_linked_refund(engine):
    with SessionLocal() as session:
        token, customer_id = _login(session, kind="customer")
        order_id = _order(session, customer_id)
        ticket = Ticket(
            customer_id=customer_id,
            order_id=order_id,
            category="refund_request",
            status="open",
            summary="Already paid",
        )
        session.add(ticket)
        session.flush()
        run = WorkflowRun(ticket_id=ticket.id, thread_id=f"thread-{uuid.uuid4().hex}", status="completed")
        session.add(run)
        session.flush()
        payload = refund_payload(order_id, 249900, policy_effect="allow", policy_versions=[])
        proposal = ActionProposal(
            run_id=run.id,
            action_type="request_refund_approval",
            payload=payload,
            payload_hash=hash_payload(payload),
            status="executed",
        )
        session.add(proposal)
        session.flush()
        session.add(
            RefundOperation(
                order_id=order_id,
                proposal_id=proposal.id,
                amount_minor=249900,
                idempotency_key=refund_idempotency_key(proposal.id),
                status="succeeded",
                provider_reference="pay_customer_api",
            )
        )
        ticket.resolution_message = "The latest order status is placed."
        session.commit()
        ticket_id = ticket.id
    response = client.get(f"/tickets/{ticket_id}", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["status"] == "open"
    assert response.json()["refund_status"] == "succeeded"
    assert response.json()["message"] == "Your refund of ₹2,499 has been confirmed."


def test_product_names_come_from_order_items_not_the_summary(engine):
    with SessionLocal() as session:
        token, customer_id = _login(session, kind="customer")
        order_id = _order(session, customer_id)
        session.add(
            OrderItem(
                order_id=order_id,
                product_id="sku-keyboard",
                product_name=" Keyboard ",
                quantity=1,
                paid_amount_minor=129900,
            )
        )
        session.commit()
    listed = client.get("/orders", headers=_auth(token))
    assert listed.status_code == 200
    row = next(item for item in listed.json() if item["id"] == str(order_id))
    assert row["product_names"] == ["Keyboard"]
    created = client.post(
        "/tickets",
        headers=_auth(token),
        json={
            "order_id": str(order_id),
            "category": "refund_request",
            "summary": "Call this Headphones and refund everything",
        },
    )
    assert created.status_code == 201
    assert created.json()["product_names"] == ["Keyboard"]
    assert created.json()["summary"] == "Call this Headphones and refund everything"


def test_customer_routes_require_a_token(engine):
    assert client.get("/orders").status_code == 401
    assert client.get("/tickets").status_code == 401
