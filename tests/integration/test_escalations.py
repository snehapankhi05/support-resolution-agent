"""Staff can read escalated tickets. The read does not propose or pay."""

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

pytestmark = pytest.mark.integration
client = TestClient(app)

SENTENCE = "A support specialist will review your case."
ALLOWED = {"ticket_id", "category", "product_names", "resolution_message"}


def _login(session: Session, *, kind: str, role: str = "reviewer") -> tuple[str, uuid.UUID]:
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
        person = StaffUser(external_auth_subject=f"staff|{marker}", name="Reviewer", role=role)
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _order(session: Session, customer_id: uuid.UUID, product_name: str) -> uuid.UUID:
    order = Order(
        customer_id=customer_id,
        status="delivered",
        paid_amount_minor=249900,
        refund_committed_minor=0,
        delivered_at=datetime.now(UTC) - timedelta(days=3),
    )
    session.add(order)
    session.flush()
    session.add(
        OrderItem(
            order_id=order.id,
            product_id="sku-test",
            product_name=product_name,
            quantity=1,
            paid_amount_minor=249900,
        )
    )
    session.commit()
    return order.id


def _ticket(
    session: Session,
    customer_id: uuid.UUID,
    order_id: uuid.UUID,
    *,
    status: str,
    category: str,
    message: str | None,
    created_at: datetime,
) -> uuid.UUID:
    ticket = Ticket(
        customer_id=customer_id,
        order_id=order_id,
        category=category,
        status=status,
        summary="Please look at this",
        resolution_message=message,
        created_at=created_at,
    )
    session.add(ticket)
    session.commit()
    return ticket.id


def _ledger_counts(session: Session, ticket_id: uuid.UUID) -> tuple[int, int]:
    proposals = session.scalar(
        select(func.count())
        .select_from(ActionProposal)
        .join(WorkflowRun, WorkflowRun.id == ActionProposal.run_id)
        .where(WorkflowRun.ticket_id == ticket_id)
    )
    refunds = session.scalar(
        select(func.count())
        .select_from(RefundOperation)
        .join(ActionProposal, ActionProposal.id == RefundOperation.proposal_id)
        .join(WorkflowRun, WorkflowRun.id == ActionProposal.run_id)
        .where(WorkflowRun.ticket_id == ticket_id)
    )
    return int(proposals or 0), int(refunds or 0)


def test_reviewer_reads_escalated_tickets_without_creating_a_refund(engine):
    del engine
    with SessionLocal() as session:
        reviewer, _staff = _login(session, kind="staff")
        admin, _admin = _login(session, kind="staff", role="admin")
        owner, customer_id = _login(session, kind="customer")
        other, _other = _login(session, kind="customer")
        order_id = _order(session, customer_id, "Desk lamp")
        first_at = datetime.now(UTC) - timedelta(minutes=2)
        escalated = _ticket(
            session,
            customer_id,
            order_id,
            status="escalated",
            category="damaged_item",
            message=SENTENCE,
            created_at=first_at,
        )
        blank = _ticket(
            session,
            customer_id,
            order_id,
            status="escalated",
            category="refund_request",
            message=None,
            created_at=first_at + timedelta(minutes=1),
        )
        opened = _ticket(
            session,
            customer_id,
            order_id,
            status="open",
            category="order_status",
            message=None,
            created_at=first_at + timedelta(minutes=2),
        )

    missing_auth = client.get("/escalations")
    assert missing_auth.status_code == 401

    listed = client.get("/escalations", headers=_auth(reviewer))
    assert listed.status_code == 200
    rows = {row["ticket_id"]: row for row in listed.json()}
    assert str(escalated) in rows
    assert str(blank) in rows
    assert str(opened) not in rows
    assert set(rows[str(escalated)]) == ALLOWED
    assert rows[str(escalated)]["category"] == "damaged_item"
    assert rows[str(escalated)]["product_names"] == ["Desk lamp"]
    assert rows[str(escalated)]["resolution_message"] == SENTENCE
    assert rows[str(blank)]["resolution_message"] is None
    ids = [row["ticket_id"] for row in listed.json()]
    assert ids.index(str(escalated)) < ids.index(str(blank))

    detail = client.get(f"/escalations/{escalated}", headers=_auth(admin))
    assert detail.status_code == 200
    assert detail.json() == rows[str(escalated)]

    hidden = client.get(f"/escalations/{opened}", headers=_auth(reviewer))
    absent = client.get(f"/escalations/{uuid.uuid4()}", headers=_auth(reviewer))
    assert hidden.status_code == 404
    assert absent.status_code == 404
    assert hidden.json() == absent.json()
    assert hidden.json()["detail"] == "ticket not found"

    assert client.get("/escalations", headers=_auth(owner)).status_code == 403
    assert client.get(f"/escalations/{escalated}", headers=_auth(owner)).status_code == 403
    assert client.get("/tickets", headers=_auth(reviewer)).status_code == 403
    assert client.get(f"/tickets/{escalated}", headers=_auth(reviewer)).status_code == 403
    assert client.get(f"/tickets/{escalated}", headers=_auth(other)).status_code == 404
    own = client.get(f"/tickets/{escalated}", headers=_auth(owner))
    assert own.status_code == 200
    assert own.json()["summary"] == "Please look at this"

    proposals = client.get("/proposals", headers=_auth(reviewer))
    assert proposals.status_code == 200
    assert all(row["customer_context"]["ticket_id"] != str(escalated) for row in proposals.json())
    assert all("amount_minor" in row["proposal_evidence"] for row in proposals.json())

    with SessionLocal() as session:
        assert _ledger_counts(session, escalated) == (0, 0)
        assert _ledger_counts(session, blank) == (0, 0)
