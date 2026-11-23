"""Proves the database itself enforces the financial safety rules from the design doc."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


def _customer(conn) -> str:
    return conn.execute(
        text(
            "INSERT INTO customers (external_auth_subject, name, email) "
            "VALUES ('auth|c1', 'Asha', 'asha@example.com') RETURNING id"
        )
    ).scalar_one()


def _order(conn, customer_id, paid=249900) -> str:
    return conn.execute(
        text(
            "INSERT INTO orders (customer_id, status, paid_amount_minor) "
            "VALUES (:c, 'delivered', :p) RETURNING id"
        ),
        {"c": customer_id, "p": paid},
    ).scalar_one()


def _ticket_run_proposal(conn, customer_id, order_id, suffix="a"):
    ticket = conn.execute(
        text(
            "INSERT INTO tickets (customer_id, order_id, category, summary) "
            "VALUES (:c, :o, 'refund_request', 'refund please') RETURNING id"
        ),
        {"c": customer_id, "o": order_id},
    ).scalar_one()
    run = conn.execute(
        text("INSERT INTO workflow_runs (ticket_id, thread_id) VALUES (:t, :th) RETURNING id"),
        {"t": ticket, "th": f"thread-{suffix}"},
    ).scalar_one()
    proposal = conn.execute(
        text(
            "INSERT INTO action_proposals (run_id, action_type, payload, payload_hash) "
            "VALUES (:r, 'request_refund_approval', CAST(:p AS jsonb), 'h1') RETURNING id"
        ),
        {"r": run, "p": json.dumps({"amount_minor": 249900})},
    ).scalar_one()
    return run, proposal


def test_refund_cannot_exceed_paid_amount(conn):
    order = _order(conn, _customer(conn), paid=1000)
    with pytest.raises(IntegrityError), conn.begin_nested():
        conn.execute(
            text("UPDATE orders SET refund_committed_minor = 1001 WHERE id = :o"), {"o": order}
        )


def test_same_idempotency_key_cannot_create_two_operations(conn):
    cust = _customer(conn)
    order = _order(conn, cust)
    _, p1 = _ticket_run_proposal(conn, cust, order, "a")
    _, p2 = _ticket_run_proposal(conn, cust, order, "b")
    insert = text(
        "INSERT INTO refund_operations (order_id, proposal_id, amount_minor, idempotency_key) "
        "VALUES (:o, :p, 100, 'key-1')"
    )
    conn.execute(insert, {"o": order, "p": p1})
    with pytest.raises(IntegrityError), conn.begin_nested():
        conn.execute(insert, {"o": order, "p": p2})


def test_audit_events_are_append_only(conn):
    conn.execute(text("INSERT INTO audit_events (actor, event_type) VALUES ('system', 'x')"))
    with pytest.raises(DBAPIError), conn.begin_nested():
        conn.execute(text("UPDATE audit_events SET actor = 'attacker'"))
    with pytest.raises(DBAPIError), conn.begin_nested():
        conn.execute(text("DELETE FROM audit_events"))


def test_one_active_run_per_ticket(conn):
    customer_id = _customer(conn)
    order_id = _order(conn, customer_id)
    ticket_id = conn.execute(
        text(
            "INSERT INTO tickets (customer_id, order_id, category, summary) "
            "VALUES (:c, :o, 'order_status', 'status') RETURNING id"
        ),
        {"c": customer_id, "o": order_id},
    ).scalar_one()
    insert = text(
        "INSERT INTO workflow_runs (ticket_id, thread_id, status) "
        "VALUES (:t, :th, :status)"
    )
    conn.execute(insert, {"t": ticket_id, "th": "finished-1", "status": "completed"})
    conn.execute(insert, {"t": ticket_id, "th": "finished-2", "status": "escalated"})
    conn.execute(insert, {"t": ticket_id, "th": "active-1", "status": "running"})
    indexdef = conn.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_one_active_run_per_ticket'")
    ).scalar_one()
    assert "UNIQUE" in indexdef
    assert "running" in indexdef
    assert "awaiting_approval" in indexdef
    with pytest.raises(IntegrityError) as running, conn.begin_nested():
        conn.execute(insert, {"t": ticket_id, "th": "active-2", "status": "running"})
    with pytest.raises(IntegrityError) as waiting, conn.begin_nested():
        conn.execute(insert, {"t": ticket_id, "th": "active-3", "status": "awaiting_approval"})
    assert running.value.orig.diag.constraint_name == "uq_one_active_run_per_ticket"
    assert waiting.value.orig.diag.constraint_name == "uq_one_active_run_per_ticket"


def test_approved_proposal_payload_is_immutable(conn):
    cust = _customer(conn)
    _, proposal = _ticket_run_proposal(conn, cust, _order(conn, cust))
    with pytest.raises(DBAPIError), conn.begin_nested():
        conn.execute(
            text("UPDATE action_proposals SET payload = CAST(:p AS jsonb) WHERE id = :i"),
            {"p": json.dumps({"amount_minor": 999999}), "i": proposal},
        )
