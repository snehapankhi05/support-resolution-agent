"""Token rules that do not need Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import (
    DEFAULT_JWT_SECRET,
    JWT_ALGORITHM,
    TokenError,
    assert_jwt_secret_configured,
    customer_subject,
    issue_token,
    read_token,
)
from app.config import get_settings


def test_password_hash_matches_only_the_same_password():
    encoded = hash_password("correct-horse")
    assert verify_password("correct-horse", encoded) is True
    assert verify_password("wrong-horse", encoded) is False
    assert encoded != "correct-horse"


def test_default_jwt_secret_is_refused():
    with pytest.raises(RuntimeError):
        assert_jwt_secret_configured(DEFAULT_JWT_SECRET)


def test_token_round_trip_uses_configured_issuer_and_audience():
    customer_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    token = issue_token(customer_subject(customer_id))
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[JWT_ALGORITHM],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
    assert payload["sub"] == f"customer:{customer_id}"
    assert "role" not in payload
    actor, actor_id = read_token(token)
    assert actor == "customer"
    assert actor_id == customer_id


def test_expired_token_is_rejected():
    token = issue_token(
        "staff:22222222-2222-2222-2222-222222222222",
        now=datetime.now(UTC) - timedelta(hours=2),
    )
    with pytest.raises(TokenError):
        read_token(token)


def test_wrong_audience_is_rejected():
    settings = get_settings()
    token = jwt.encode(
        {
            "sub": "customer:11111111-1111-1111-1111-111111111111",
            "iss": settings.jwt_issuer,
            "aud": "support-mcp",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(TokenError):
        read_token(token)


def test_other_algorithm_is_rejected():
    settings = get_settings()
    token = jwt.encode(
        {
            "sub": "customer:11111111-1111-1111-1111-111111111111",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm="HS384",
    )
    with pytest.raises(TokenError):
        read_token(token)
