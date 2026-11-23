"""Login and GET /me. Rows are committed only to support_test."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.db.models import Customer, LoginAccount, StaffUser
from app.db.session import SessionLocal
from app.main import app

pytestmark = pytest.mark.integration
client = TestClient(app)


def _account(session: Session, *, kind: str, role: str = "reviewer") -> tuple[str, str]:
    marker = uuid.uuid4().hex
    email = f"{kind}-{marker}@example.com"
    password = f"pw-{marker}"
    if kind == "customer":
        person = Customer(external_auth_subject=f"auth|{marker}", name="Asha", email=email)
        session.add(person)
        session.flush()
        session.add(
            LoginAccount(email=email, password_hash=hash_password(password), customer_id=person.id)
        )
    else:
        person = StaffUser(external_auth_subject=f"auth|{marker}", name="Priya", role=role)
        session.add(person)
        session.flush()
        session.add(
            LoginAccount(
                email=email, password_hash=hash_password(password), staff_user_id=person.id
            )
        )
    session.commit()
    return email, password


def test_login_rejects_unknown_fields_and_bad_passwords(engine):
    with SessionLocal() as session:
        email, password = _account(session, kind="customer")
    extra = client.post(
        "/auth/token",
        json={"email": email, "password": password, "customer_id": str(uuid.uuid4())},
    )
    assert extra.status_code == 422
    unknown = client.post(
        "/auth/token",
        json={"email": f"{uuid.uuid4().hex}@example.com", "password": password},
    )
    wrong = client.post("/auth/token", json={"email": email, "password": "not-the-password"})
    assert unknown.status_code == 401
    assert wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_customer_login_is_case_insensitive_and_has_no_role_claim(engine):
    with SessionLocal() as session:
        email, password = _account(session, kind="customer")
    response = client.post(
        "/auth/token", json={"email": email.upper(), "password": password}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    me = client.get("/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["actor"] == "customer"
    assert me.json()["role"] == "customer"
    assert me.json()["email"] == email


def test_issued_bearer_token_authorizes_customer_routes(engine):
    with SessionLocal() as session:
        email, password = _account(session, kind="customer")
    token = client.post("/auth/token", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/me", headers=headers).status_code == 200
    assert client.get("/orders", headers=headers).status_code == 200
    assert client.get("/tickets", headers=headers).status_code == 200


def test_staff_role_is_read_from_the_database(engine):
    with SessionLocal() as session:
        email, password = _account(session, kind="staff", role="reviewer")
    token = client.post("/auth/token", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    first = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert first.status_code == 200
    assert first.json()["role"] == "reviewer"
    with SessionLocal() as session:
        staff = session.get(StaffUser, uuid.UUID(first.json()["id"]))
        staff.role = "admin"
        session.commit()
    again = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert again.status_code == 200
    assert again.json()["role"] == "admin"
    assert again.json()["actor"] == "staff"


def test_missing_and_altered_tokens_are_rejected(engine):
    assert client.get("/me").status_code == 401
    with SessionLocal() as session:
        email, password = _account(session, kind="customer")
    token = client.post("/auth/token", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    altered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    refused = client.get("/me", headers={"Authorization": f"Bearer {altered}"})
    assert refused.status_code == 401


def test_deleted_account_cannot_keep_using_a_token(engine):
    with SessionLocal() as session:
        email, password = _account(session, kind="customer")
        customer = session.scalar(select(Customer).where(Customer.email == email))
        customer_id = customer.id
    token = client.post("/auth/token", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    with SessionLocal() as session:
        account = session.scalar(select(LoginAccount).where(LoginAccount.customer_id == customer_id))
        session.delete(account)
        session.commit()
    refused = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 401
