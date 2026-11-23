"""HS256 tokens. The subject names the table and primary key. Role is not a claim."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings

DEFAULT_JWT_SECRET = "change-me-in-real-use"
JWT_ALGORITHM = "HS256"
_MIN_SECRET_LENGTH = 32


class TokenError(Exception):
    """The token is missing, expired, or not one this API issued."""


def assert_jwt_secret_configured(secret: str) -> None:
    """Refuse the checked-in default and any secret too short for HS256."""
    if secret == DEFAULT_JWT_SECRET or len(secret) < _MIN_SECRET_LENGTH:
        raise RuntimeError(
            "JWT_SECRET must be set to a non-default value of at least 32 characters"
        )


def customer_subject(customer_id: uuid.UUID) -> str:
    return f"customer:{customer_id}"


def staff_subject(staff_user_id: uuid.UUID) -> str:
    return f"staff:{staff_user_id}"


def issue_token(subject: str, *, now: datetime | None = None) -> str:
    """Sign a short-lived API token. The caller has already loaded the account."""
    settings = get_settings()
    assert_jwt_secret_configured(settings.jwt_secret)
    moment = now or datetime.now(UTC)
    payload = {
        "sub": subject,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": moment,
        "exp": moment + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def read_token(token: str) -> tuple[str, uuid.UUID]:
    """Return (actor, id) after checking algorithm, issuer, audience, and expiry."""
    settings = get_settings()
    assert_jwt_secret_configured(settings.jwt_secret)
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[JWT_ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc
    return _parse_subject(str(payload.get("sub", "")))


MCP_AUDIENCE = "support-mcp"
MCP_TTL_SECONDS = 900


def issue_mcp_token(customer_id: uuid.UUID, *, now: datetime | None = None) -> str:
    """Sign a short-lived customer token for the MCP servers.

    The audience is support-mcp, so an API token cannot be reused here.
    """
    settings = get_settings()
    assert_jwt_secret_configured(settings.jwt_secret)
    moment = now or datetime.now(UTC)
    payload = {
        "sub": customer_subject(customer_id),
        "iss": settings.jwt_issuer,
        "aud": MCP_AUDIENCE,
        "iat": moment,
        "exp": moment + timedelta(seconds=MCP_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def read_mcp_customer(token: str) -> uuid.UUID:
    """Return the customer primary key. Staff subjects and API tokens are rejected."""
    settings = get_settings()
    assert_jwt_secret_configured(settings.jwt_secret)
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[JWT_ALGORITHM],
            audience=MCP_AUDIENCE,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc
    actor, actor_id = _parse_subject(str(payload.get("sub", "")))
    if actor != "customer":
        raise TokenError("invalid token")
    return actor_id


def _parse_subject(subject: str) -> tuple[str, uuid.UUID]:
    actor, separator, raw_id = subject.partition(":")
    if separator != ":" or actor not in {"customer", "staff"}:
        raise TokenError("invalid token")
    try:
        return actor, uuid.UUID(raw_id)
    except ValueError as exc:
        raise TokenError("invalid token") from exc
