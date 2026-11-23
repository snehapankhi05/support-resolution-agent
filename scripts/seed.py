"""Replace synthetic seed customers and their business rows.

Running this deletes and recreates every `seed|` customer, the seed reviewer,
and their orders, tickets, returns, proposals, approvals, and refunds. It also
tries to delete audit events for those runs. `audit_events` is append-only, so
that delete fails once a seed run has an audit row.

This is not how demo logins are added. Use `python -m scripts.provision_logins`.

Usage: uv run python -m scripts.seed
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.db.models import (
    ActionProposal,
    Approval,
    AuditEvent,
    Customer,
    LoginAccount,
    Order,
    OrderItem,
    RefundOperation,
    Return,
    StaffUser,
    Ticket,
    WorkflowRun,
)
from app.db.session import SessionLocal
from app.services.refunds import hash_payload, refund_idempotency_key, refund_payload

SEED = "seed|"
NOW = datetime.now(UTC)
DEMO_PASSWORD = "demo-password"
REVIEWER_EMAIL = "priya@seed.example"

CUSTOMERS = (
    ("asha", "Asha", "asha@seed.example"),
    ("rohan", "Rohan", "rohan@seed.example"),
    ("meera", "Meera", "meera@seed.example"),
    ("kabir", "Kabir", "kabir@seed.example"),
    ("isha", "Isha", "isha@seed.example"),
    ("dev", "Dev", "dev@seed.example"),
    ("neha", "Neha", "neha@seed.example"),
    ("arjun", "Arjun", "arjun@seed.example"),
    ("sara", "Sara", "sara@seed.example"),
    ("vikram", "Vikram", "vikram@seed.example"),
)

# 8 eligible, 6 too old, 3 outside the 30-day window, 4 failed inspection,
# 3 return not received, 3 not delivered, 3 already refunded.
ORDER_KINDS = (
    ["eligible", "too_old", "eligible"]
    + ["eligible"] * 6
    + ["too_old"] * 5
    + ["outside_window"] * 3
    + ["inspection_failed"] * 4
    + ["not_received"] * 3
    + ["not_delivered"] * 3
    + ["refunded"] * 3
)

DEMO_PRODUCTS = {
    0: ("sku-headphones", "Headphones", 249900),
    1: ("sku-watch", "Smart watch", 899900),
    2: ("sku-keyboard", "Keyboard", 129900),
}

# Display names for sku-3 .. sku-29. Ids and prices stay on the index formula.
DISPLAY_NAMES = {
    3: "Desk lamp",
    4: "Bluetooth speaker",
    5: "USB-C hub",
    6: "Wireless mouse",
    7: "Laptop stand",
    8: "Phone case",
    9: "Power bank",
    10: "Webcam",
    11: "Noise-cancelling earbuds",
    12: "Portable SSD",
    13: "Monitor arm",
    14: "Fitness band",
    15: "Tablet sleeve",
    16: "HDMI cable",
    17: "Drawing tablet",
    18: "Wi-Fi router",
    19: "External drive",
    20: "Wireless charger",
    21: "Gaming mouse pad",
    22: "LED light bar",
    23: "USB microphone",
    24: "Camera tripod",
    25: "Screen protector",
    26: "Laptop sleeve",
    27: "Action camera",
    28: "E-reader",
    29: "Mini projector",
}


def clear_seed(session: Session) -> None:
    customers = select(Customer.id).where(Customer.external_auth_subject.startswith(SEED))
    staff = select(StaffUser.id).where(StaffUser.external_auth_subject.startswith(SEED))
    orders = select(Order.id).where(Order.customer_id.in_(customers))
    tickets = select(Ticket.id).where(Ticket.customer_id.in_(customers))
    runs = select(WorkflowRun.id).where(WorkflowRun.ticket_id.in_(tickets))
    proposals = select(ActionProposal.id).where(ActionProposal.run_id.in_(runs))
    session.execute(
        delete(LoginAccount).where(
            LoginAccount.customer_id.in_(customers) | LoginAccount.staff_user_id.in_(staff)
        )
    )
    session.execute(delete(AuditEvent).where(AuditEvent.run_id.in_(runs)))
    session.execute(delete(RefundOperation).where(RefundOperation.order_id.in_(orders)))
    session.execute(delete(Approval).where(Approval.proposal_id.in_(proposals)))
    session.execute(delete(ActionProposal).where(ActionProposal.id.in_(proposals)))
    session.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(runs)))
    session.execute(delete(Ticket).where(Ticket.id.in_(tickets)))
    session.execute(delete(Return).where(Return.order_id.in_(orders)))
    session.execute(delete(OrderItem).where(OrderItem.order_id.in_(orders)))
    session.execute(delete(Order).where(Order.id.in_(orders)))
    session.execute(delete(Customer).where(Customer.id.in_(customers)))
    session.execute(delete(StaffUser).where(StaffUser.external_auth_subject.startswith(SEED)))


def seed(session: Session) -> None:
    if len(CUSTOMERS) != 10 or len(ORDER_KINDS) != 30:
        raise RuntimeError("seed shape drifted from 10 customers and 30 orders")
    if set(DISPLAY_NAMES) != set(range(3, 30)):
        raise RuntimeError("display names must cover sku-3 through sku-29 exactly")
    clear_seed(session)
    reviewer = StaffUser(
        external_auth_subject=f"{SEED}reviewer", name="Priya", role="reviewer"
    )
    session.add(reviewer)
    session.flush()

    people: list[Customer] = []
    for subject, name, email in CUSTOMERS:
        person = Customer(external_auth_subject=f"{SEED}{subject}", name=name, email=email)
        session.add(person)
        people.append(person)
    session.flush()
    _add_login(session, email=REVIEWER_EMAIL, staff_user_id=reviewer.id)
    for person in people:
        _add_login(session, email=person.email, customer_id=person.id)

    for index, kind in enumerate(ORDER_KINDS):
        person = people[index % len(people)]
        _add_order(session, reviewer, person, index, kind)
    session.flush()


def _add_login(
    session: Session,
    *,
    email: str,
    customer_id: uuid.UUID | None = None,
    staff_user_id: uuid.UUID | None = None,
) -> None:
    session.add(
        LoginAccount(
            email=email.strip().lower(),
            password_hash=hash_password(DEMO_PASSWORD),
            customer_id=customer_id,
            staff_user_id=staff_user_id,
        )
    )


def _add_order(
    session: Session, reviewer: StaffUser, person: Customer, index: int, kind: str
) -> None:
    if index in DEMO_PRODUCTS:
        product_id, product_name, paid = DEMO_PRODUCTS[index]
    else:
        product_id = f"sku-{index}"
        product_name = DISPLAY_NAMES[index]
        paid = 100000 + index * 1000

    delivered_at, order_status = _delivery(kind)
    order = Order(
        customer_id=person.id,
        status=order_status,
        paid_amount_minor=paid,
        refund_committed_minor=paid if kind == "refunded" else 0,
        delivered_at=delivered_at,
    )
    session.add(order)
    session.flush()
    session.add(
        OrderItem(
            order_id=order.id,
            product_id=product_id,
            product_name=product_name,
            quantity=1,
            paid_amount_minor=paid,
        )
    )
    _add_return(session, order.id, kind, delivered_at)
    if kind == "refunded":
        _add_completed_refund(session, reviewer, person, order, paid, index)


def _delivery(kind: str) -> tuple[datetime | None, str]:
    if kind == "not_delivered":
        return None, "shipped"
    if kind == "too_old":
        return NOW - timedelta(days=45), "delivered"
    if kind == "outside_window":
        return NOW - timedelta(days=50), "delivered"
    return NOW - timedelta(days=10), "delivered"


def _add_return(
    session: Session, order_id: uuid.UUID, kind: str, delivered_at: datetime | None
) -> None:
    if kind in {"too_old", "not_delivered"} or delivered_at is None:
        return
    if kind == "outside_window":
        requested = delivered_at + timedelta(days=40)
        session.add(
            Return(
                order_id=order_id,
                status="received",
                inspection_status="passed",
                received_at=requested,
                created_at=requested,
            )
        )
        return
    requested = delivered_at + timedelta(days=2)
    if kind == "inspection_failed":
        session.add(
            Return(
                order_id=order_id,
                status="received",
                inspection_status="failed",
                received_at=requested + timedelta(days=1),
                created_at=requested,
            )
        )
        return
    if kind == "not_received":
        session.add(
            Return(
                order_id=order_id,
                status="in_transit",
                inspection_status="pending",
                created_at=requested,
            )
        )
        return
    session.add(
        Return(
            order_id=order_id,
            status="received",
            inspection_status="passed",
            received_at=requested + timedelta(days=1),
            created_at=requested,
        )
    )


def _add_completed_refund(
    session: Session,
    reviewer: StaffUser,
    person: Customer,
    order: Order,
    paid: int,
    index: int,
) -> None:
    ticket = Ticket(
        customer_id=person.id,
        order_id=order.id,
        category="refund_request",
        status="resolved",
        summary=f"Seed refund already completed for order {index}",
    )
    session.add(ticket)
    session.flush()
    run = WorkflowRun(ticket_id=ticket.id, thread_id=f"{SEED}thread-{index}", status="completed")
    session.add(run)
    session.flush()
    payload = refund_payload(order.id, paid, policy_effect="allow", policy_versions=[])
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
        Approval(
            proposal_id=proposal.id,
            reviewer_id=reviewer.id,
            status="approved",
            expires_at=NOW + timedelta(hours=24),
            decided_at=NOW - timedelta(hours=1),
        )
    )
    session.add(
        RefundOperation(
            order_id=order.id,
            proposal_id=proposal.id,
            amount_minor=paid,
            idempotency_key=refund_idempotency_key(proposal.id),
            provider_reference=f"{SEED}pay-{index}",
            status="succeeded",
        )
    )


def main() -> None:
    with SessionLocal() as session:
        seed(session)
        session.commit()
    print("seeded 10 customers and 30 orders")


if __name__ == "__main__":
    main()
