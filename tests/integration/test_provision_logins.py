"""Demo login provisioning does not replace orders or refunds."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
from app.services.refunds import hash_payload, refund_idempotency_key, refund_payload
from scripts.provision_logins import ProvisionError, main, provision_demo_logins
from scripts.seed import CUSTOMERS, REVIEWER_EMAIL, SEED

pytestmark = pytest.mark.integration


def _session(conn) -> Session:
    return Session(bind=conn)


def _seed_people(session: Session) -> tuple[list[Customer], StaffUser]:
    people: list[Customer] = []
    for subject, name, email in CUSTOMERS:
        person = Customer(
            external_auth_subject=f"{SEED}{subject}",
            name=name,
            email=email,
        )
        session.add(person)
        people.append(person)
    reviewer = StaffUser(
        external_auth_subject=f"{SEED}reviewer",
        name="Priya",
        role="reviewer",
    )
    session.add(reviewer)
    session.flush()
    return people, reviewer


def _order_and_refund(session: Session, customer: Customer) -> None:
    order = Order(
        customer_id=customer.id,
        status="delivered",
        paid_amount_minor=249900,
        refund_committed_minor=249900,
    )
    session.add(order)
    session.flush()
    ticket = Ticket(
        customer_id=customer.id,
        order_id=order.id,
        category="refund_request",
        status="resolved",
        summary="Already refunded",
    )
    session.add(ticket)
    session.flush()
    run = WorkflowRun(
        ticket_id=ticket.id,
        thread_id=f"provision-{uuid.uuid4().hex}",
        status="completed",
    )
    session.add(run)
    session.flush()
    payload = refund_payload(order.id, 249900, policy_effect="allow", policy_versions=[])
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
            order_id=order.id,
            proposal_id=proposal.id,
            amount_minor=249900,
            idempotency_key=refund_idempotency_key(proposal.id),
            status="succeeded",
        )
    )
    session.flush()


def _counts(session: Session) -> tuple[int, int]:
    orders = session.scalar(select(func.count()).select_from(Order))
    refunds = session.scalar(select(func.count()).select_from(RefundOperation))
    assert orders is not None and refunds is not None
    return orders, refunds


def test_provision_adds_logins_without_changing_orders_or_refunds(conn):
    session = _session(conn)
    people, reviewer = _seed_people(session)
    _order_and_refund(session, people[0])
    before = _counts(session)

    first = provision_demo_logins(session)
    session.flush()
    assert first.inserted == 11
    assert first.already_present == 0
    assert _counts(session) == before

    customer_login = session.scalar(
        select(LoginAccount).where(LoginAccount.customer_id == people[0].id)
    )
    staff_login = session.scalar(
        select(LoginAccount).where(LoginAccount.staff_user_id == reviewer.id)
    )
    assert customer_login is not None
    assert customer_login.email == people[0].email
    assert staff_login is not None
    assert staff_login.email == REVIEWER_EMAIL
    stored_hash = customer_login.password_hash

    second = provision_demo_logins(session)
    session.flush()
    assert second.inserted == 0
    assert second.already_present == 11
    assert _counts(session) == before
    again = session.scalar(
        select(LoginAccount).where(LoginAccount.customer_id == people[0].id)
    )
    assert again is not None
    assert again.password_hash == stored_hash


def test_provision_stops_when_an_email_is_already_used(conn):
    session = _session(conn)
    people, _reviewer = _seed_people(session)
    other = Customer(
        external_auth_subject=f"other|{uuid.uuid4().hex}",
        name="Other",
        email=f"other-{uuid.uuid4().hex}@example.com",
    )
    session.add(other)
    session.flush()
    session.add(
        LoginAccount(
            email=people[0].email,
            password_hash="not-a-real-hash",
            customer_id=other.id,
        )
    )
    session.flush()
    with pytest.raises(ProvisionError, match="email already used"):
        provision_demo_logins(session)
    own = session.scalar(select(LoginAccount).where(LoginAccount.customer_id == people[0].id))
    assert own is None


def test_provision_stops_when_a_seed_person_is_missing(conn):
    session = _session(conn)
    people, _reviewer = _seed_people(session)
    session.delete(people[-1])
    session.flush()
    with pytest.raises(ProvisionError, match="missing seed people"):
        provision_demo_logins(session)


def test_cli_refuses_the_test_database(engine):
    with pytest.raises(SystemExit, match="support_test"):
        main()
