"""Login constraints. The connection fixture rolls every insert back."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def _customer(conn) -> str:
    return conn.execute(
        text(
            "INSERT INTO customers (external_auth_subject, name, email) "
            "VALUES (:s, 'Login', :e) RETURNING id"
        ),
        {"s": f"login|{uuid.uuid4().hex}", "e": f"{uuid.uuid4().hex}@example.com"},
    ).scalar_one()


def _staff(conn) -> str:
    return conn.execute(
        text(
            "INSERT INTO staff_users (external_auth_subject, name, role) "
            "VALUES (:s, 'Reviewer', 'reviewer') RETURNING id"
        ),
        {"s": f"login|{uuid.uuid4().hex}"},
    ).scalar_one()


def test_login_requires_exactly_one_owner(conn):
    customer_id = _customer(conn)
    staff_id = _staff(conn)
    insert = text(
        "INSERT INTO login_accounts (email, password_hash, customer_id, staff_user_id) "
        "VALUES (:e, 'hash', :c, :s)"
    )
    with pytest.raises(IntegrityError), conn.begin_nested():
        conn.execute(insert, {"e": f"{uuid.uuid4().hex}@example.com", "c": None, "s": None})
    with pytest.raises(IntegrityError), conn.begin_nested():
        conn.execute(
            insert, {"e": f"{uuid.uuid4().hex}@example.com", "c": customer_id, "s": staff_id}
        )


def test_login_owner_and_email_are_unique(conn):
    customer_id = _customer(conn)
    insert = text(
        "INSERT INTO login_accounts (email, password_hash, customer_id) "
        "VALUES (:e, 'hash', :c)"
    )
    email = f"{uuid.uuid4().hex}@example.com"
    conn.execute(insert, {"e": email, "c": customer_id})
    with pytest.raises(IntegrityError), conn.begin_nested():
        conn.execute(insert, {"e": f"{uuid.uuid4().hex}@example.com", "c": customer_id})
    with pytest.raises(IntegrityError), conn.begin_nested():
        other = _customer(conn)
        conn.execute(insert, {"e": email, "c": other})
