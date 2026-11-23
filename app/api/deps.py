"""Database session and the account loaded from a token."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.tokens import TokenError, read_token
from app.db.models import Customer, LoginAccount, StaffUser
from app.db.session import SessionLocal


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@dataclass(frozen=True)
class Principal:
    """The person named by the token, read again from the database."""

    actor: str
    id: uuid.UUID
    email: str
    name: str
    role: str


bearer_scheme = HTTPBearer(bearerFormat="JWT", auto_error=False)


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Principal:
    """Load the customer or staff row. A role claim in the token is ignored."""
    if credentials is None or credentials.scheme != "Bearer":
        raise HTTPException(status_code=401, detail="authentication required")
    token = credentials.credentials.strip()
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        actor, actor_id = read_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    if actor == "customer":
        customer = db.get(Customer, actor_id)
        if customer is None:
            raise HTTPException(status_code=401, detail="invalid token")
        account = db.scalar(select(LoginAccount).where(LoginAccount.customer_id == customer.id))
        if account is None:
            raise HTTPException(status_code=401, detail="invalid token")
        return Principal("customer", customer.id, account.email, customer.name, "customer")
    staff = db.get(StaffUser, actor_id)
    if staff is None:
        raise HTTPException(status_code=401, detail="invalid token")
    account = db.scalar(select(LoginAccount).where(LoginAccount.staff_user_id == staff.id))
    if account is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return Principal("staff", staff.id, account.email, staff.name, staff.role)


def require_customer(principal: Principal = Depends(get_principal)) -> Principal:  # noqa: B008
    """Customer routes reject a staff token before any order or ticket lookup."""
    if principal.actor != "customer":
        raise HTTPException(status_code=403, detail="customer account required")
    return principal


def require_reviewer(principal: Principal = Depends(get_principal)) -> Principal:  # noqa: B008
    """Reviewer routes reject anyone who is not a database staff reviewer or admin."""
    if principal.actor != "staff" or principal.role not in {"reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="reviewer account required")
    return principal
