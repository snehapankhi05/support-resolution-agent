"""POST /runs opens one active run and does not execute or pay."""

import threading
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
    RefundOperation,
    StaffUser,
    Ticket,
    WorkflowRun,
)
from app.db.session import SessionLocal
from app.main import app
from app.services.runs import start_workflow_run

pytestmark = pytest.mark.integration
client = TestClient(app)


@pytest.fixture(autouse=True)
def enqueued(monkeypatch):
    calls: list[uuid.UUID] = []
    monkeypatch.setattr("app.api.runs.enqueue_workflow", calls.append)
    return calls


def _login(session: Session, *, kind: str) -> tuple[str, uuid.UUID]:
    marker = uuid.uuid4().hex
    email = f"{kind}-{marker}@runs.example"
    password = f"pw-{marker}"
    if kind == "customer":
        person = Customer(external_auth_subject=f"run|{marker}", name="Buyer", email=email)
        session.add(person)
        session.flush()
        session.add(
            LoginAccount(email=email, password_hash=hash_password(password), customer_id=person.id)
        )
    else:
        person = StaffUser(external_auth_subject=f"run-staff|{marker}", name="Reviewer", role="reviewer")
        session.add(person)
        session.flush()
        session.add(
            LoginAccount(email=email, password_hash=hash_password(password), staff_user_id=person.id)
        )
    session.commit()
    token = client.post("/auth/token", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return token, person.id


def _ticket(session: Session, customer_id: uuid.UUID) -> uuid.UUID:
    order = Order(
        customer_id=customer_id,
        status="delivered",
        paid_amount_minor=1000,
        refund_committed_minor=0,
        delivered_at=datetime.now(UTC) - timedelta(days=1),
    )
    session.add(order)
    session.flush()
    ticket = Ticket(
        customer_id=customer_id,
        order_id=order.id,
        category="order_status",
        summary="Where is the order?",
    )
    session.add(ticket)
    session.commit()
    return ticket.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_customer_opens_one_run_and_a_repeat_returns_it(engine, enqueued):
    with SessionLocal() as session:
        token, customer_id = _login(session, kind="customer")
        ticket_id = _ticket(session, customer_id)
        refunds_before = session.scalar(select(func.count()).select_from(RefundOperation))
    created = client.post("/runs", headers=_auth(token), json={"ticket_id": str(ticket_id)})
    again = client.post(
        "/runs",
        headers=_auth(token),
        json={"ticket_id": str(ticket_id), "customer_id": str(uuid.uuid4())},
    )
    ignored_query = client.post(
        "/runs",
        headers=_auth(token),
        params={"customer_id": str(uuid.uuid4())},
        json={"ticket_id": str(ticket_id)},
    )
    assert created.status_code == 201
    assert created.json()["created"] is True
    assert created.json()["status"] == "running"
    assert created.json()["ticket_id"] == str(ticket_id)
    assert created.json()["thread_id"] == created.json()["run_id"]
    assert again.status_code == 422
    assert ignored_query.status_code == 200
    assert ignored_query.json()["run_id"] == created.json()["run_id"]
    assert ignored_query.json()["created"] is False
    assert enqueued == [uuid.UUID(created.json()["run_id"]), uuid.UUID(created.json()["run_id"])]
    with SessionLocal() as session:
        runs = session.scalars(select(WorkflowRun).where(WorkflowRun.ticket_id == ticket_id)).all()
        proposals = session.scalar(
            select(func.count())
            .select_from(ActionProposal)
            .where(ActionProposal.run_id == uuid.UUID(created.json()["run_id"]))
        )
        refunds_after = session.scalar(select(func.count()).select_from(RefundOperation))
    assert len(runs) == 1
    assert proposals == 0
    assert refunds_after == refunds_before


def test_other_customer_and_staff_cannot_open_a_run(engine):
    with SessionLocal() as session:
        token, customer_id = _login(session, kind="customer")
        other, _other_id = _login(session, kind="customer")
        staff, _staff_id = _login(session, kind="staff")
        ticket_id = _ticket(session, customer_id)
    missing = client.post("/runs", headers=_auth(token), json={"ticket_id": str(uuid.uuid4())})
    foreign = client.post("/runs", headers=_auth(other), json={"ticket_id": str(ticket_id)})
    staff_call = client.post("/runs", headers=_auth(staff), json={"ticket_id": str(ticket_id)})
    anonymous = client.post("/runs", json={"ticket_id": str(ticket_id)})
    assert missing.status_code == 404
    assert foreign.status_code == 404
    assert foreign.json() == missing.json()
    assert foreign.json()["detail"] == "ticket not found"
    assert staff_call.status_code == 403
    assert staff_call.json()["detail"] == "customer account required"
    assert anonymous.status_code == 401
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count()).select_from(WorkflowRun).where(WorkflowRun.ticket_id == ticket_id)
        ) == 0


def test_completed_run_allows_a_new_active_run(engine):
    with SessionLocal() as session:
        token, customer_id = _login(session, kind="customer")
        ticket_id = _ticket(session, customer_id)
        finished = WorkflowRun(
            ticket_id=ticket_id,
            thread_id=f"finished-{uuid.uuid4().hex}",
            status="completed",
        )
        session.add(finished)
        session.commit()
        finished_id = finished.id
    created = client.post("/runs", headers=_auth(token), json={"ticket_id": str(ticket_id)})
    assert created.status_code == 201
    assert created.json()["run_id"] != str(finished_id)
    assert created.json()["status"] == "running"
    repeat = client.post("/runs", headers=_auth(token), json={"ticket_id": str(ticket_id)})
    assert repeat.status_code == 200
    assert repeat.json()["run_id"] == created.json()["run_id"]


def test_concurrent_starts_store_one_active_run(engine):
    with SessionLocal() as session:
        _token, customer_id = _login(session, kind="customer")
        ticket_id = _ticket(session, customer_id)
    barrier = threading.Barrier(2)
    results: list = []
    errors: list = []

    def call() -> None:
        barrier.wait()
        try:
            with SessionLocal() as session:
                result = start_workflow_run(session, customer_id, ticket_id)
                session.commit()
                results.append(result)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=call), threading.Thread(target=call)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(results) == 2
    assert len({row.run_id for row in results}) == 1
    assert {row.created for row in results} == {True, False}
    with SessionLocal() as session:
        active = session.scalars(
            select(WorkflowRun).where(
                WorkflowRun.ticket_id == ticket_id,
                WorkflowRun.status.in_(("running", "awaiting_approval")),
            )
        ).all()
    assert len(active) == 1
