"""Insert demo logins for the seed people who already exist.

This does not create customers, staff, orders, or tickets, and it does not
delete anything. A login that already belongs to that person is left as it is.

Usage: uv run python -m scripts.provision_logins

The command runs only when the connected database name is support.
"""

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.db.models import Customer, LoginAccount, StaffUser
from app.db.session import SessionLocal
from scripts.seed import CUSTOMERS, DEMO_PASSWORD, REVIEWER_EMAIL, SEED

DEVELOPMENT_DATABASE = "support"


class ProvisionError(Exception):
    """The existing seed people cannot be given logins safely."""


@dataclass(frozen=True)
class ProvisionResult:
    inserted: int
    already_present: int


def provision_demo_logins(session: Session) -> ProvisionResult:
    """Add missing logins for the ten seed customers and the seed reviewer.

    The caller commits. On failure nothing in this function is left pending
    only if the caller rolls the transaction back.
    """
    people: list[Customer] = []
    missing: list[str] = []
    for subject, _name, _email in CUSTOMERS:
        auth_subject = f"{SEED}{subject}"
        person = session.scalar(
            select(Customer).where(Customer.external_auth_subject == auth_subject)
        )
        if person is None:
            missing.append(auth_subject)
        else:
            people.append(person)
    reviewer = session.scalar(
        select(StaffUser).where(StaffUser.external_auth_subject == f"{SEED}reviewer")
    )
    if reviewer is None:
        missing.append(f"{SEED}reviewer")
    if missing:
        raise ProvisionError("missing seed people: " + ", ".join(missing))

    pending: list[LoginAccount] = []
    already_present = 0
    claimed: set[str] = set()
    for person in people:
        already_present += _queue_customer(session, person, pending, claimed)
    assert reviewer is not None
    already_present += _queue_staff(session, reviewer, pending, claimed)
    session.add_all(pending)
    return ProvisionResult(inserted=len(pending), already_present=already_present)


def _queue_customer(
    session: Session,
    person: Customer,
    pending: list[LoginAccount],
    claimed: set[str],
) -> int:
    existing = session.scalar(
        select(LoginAccount).where(LoginAccount.customer_id == person.id)
    )
    if existing is not None:
        return 1
    email = _login_email(person.email)
    _require_email_free(session, email, claimed)
    pending.append(
        LoginAccount(
            email=email,
            password_hash=hash_password(DEMO_PASSWORD),
            customer_id=person.id,
        )
    )
    return 0


def _queue_staff(
    session: Session,
    reviewer: StaffUser,
    pending: list[LoginAccount],
    claimed: set[str],
) -> int:
    existing = session.scalar(
        select(LoginAccount).where(LoginAccount.staff_user_id == reviewer.id)
    )
    if existing is not None:
        return 1
    email = _login_email(REVIEWER_EMAIL)
    _require_email_free(session, email, claimed)
    pending.append(
        LoginAccount(
            email=email,
            password_hash=hash_password(DEMO_PASSWORD),
            staff_user_id=reviewer.id,
        )
    )
    return 0


def _login_email(email: str) -> str:
    cleaned = email.strip().lower()
    if "@" not in cleaned or " " in cleaned:
        raise ProvisionError("seed person has no usable email")
    return cleaned


def _require_email_free(session: Session, email: str, claimed: set[str]) -> None:
    if email in claimed:
        raise ProvisionError(f"email already used: {email}")
    owner = session.scalar(select(LoginAccount).where(LoginAccount.email == email))
    if owner is not None:
        raise ProvisionError(f"email already used: {email}")
    claimed.add(email)


def main() -> None:
    with SessionLocal() as session:
        name = session.scalar(text("SELECT current_database()"))
        if name != DEVELOPMENT_DATABASE:
            raise SystemExit(
                f"Refusing to provision logins on database {name!r}. "
                "This command only runs against support."
            )
        version = session.scalar(text("SELECT version_num FROM alembic_version"))
        try:
            result = provision_demo_logins(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
    print(
        f"database {name} alembic {version} "
        f"inserted {result.inserted} already_present {result.already_present}"
    )


if __name__ == "__main__":
    main()
