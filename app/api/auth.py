"""Login and the current account. Identity is loaded from the database."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_db, get_principal
from app.auth.passwords import verify_password_or_dummy
from app.auth.tokens import customer_subject, issue_token, staff_subject
from app.config import get_settings
from app.db.models import LoginAccount
from app.schemas.auth import MeResponse, TokenRequest, TokenResponse

router = APIRouter()

_INVALID_LOGIN = "invalid email or password"


def normalize_email(email: str) -> str:
    """Compare emails in one case. The stored value is lowercase."""
    cleaned = email.strip().lower()
    if "@" not in cleaned or " " in cleaned:
        raise HTTPException(status_code=422, detail="invalid email")
    return cleaned


@router.post("/auth/token", response_model=TokenResponse)
def login(body: TokenRequest, db: Session = Depends(get_db)) -> TokenResponse:  # noqa: B008
    """Issue a token after the password matches one login row. No role is accepted."""
    email = normalize_email(body.email)
    account = db.scalar(select(LoginAccount).where(LoginAccount.email == email))
    stored = None if account is None else account.password_hash
    if not verify_password_or_dummy(body.password, stored) or account is None:
        raise HTTPException(status_code=401, detail=_INVALID_LOGIN)
    if account.customer_id is not None:
        subject = customer_subject(account.customer_id)
    elif account.staff_user_id is not None:
        subject = staff_subject(account.staff_user_id)
    else:
        raise HTTPException(status_code=401, detail=_INVALID_LOGIN)
    settings = get_settings()
    return TokenResponse(
        access_token=issue_token(subject),
        token_type="bearer",
        expires_in=settings.jwt_ttl_seconds,
    )


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_principal)) -> MeResponse:  # noqa: B008
    """Return the account named by the token, using the role stored in the database."""
    return MeResponse(
        actor=principal.actor,
        id=principal.id,
        email=principal.email,
        name=principal.name,
        role=principal.role,
    )
