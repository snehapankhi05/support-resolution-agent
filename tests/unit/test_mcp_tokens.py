"""MCP tokens are a separate audience from the customer API."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.auth.tokens import (
    JWT_ALGORITHM,
    MCP_AUDIENCE,
    TokenError,
    customer_subject,
    issue_mcp_token,
    issue_token,
    read_mcp_customer,
    read_token,
    staff_subject,
)
from app.config import get_settings


def test_mcp_token_is_not_an_api_token():
    customer_id = uuid.uuid4()
    token = issue_mcp_token(customer_id)
    assert read_mcp_customer(token) == customer_id
    try:
        read_token(token)
    except TokenError:
        return
    raise AssertionError("an MCP token must not pass the API audience check")


def test_api_token_is_not_an_mcp_token():
    customer_id = uuid.uuid4()
    token = issue_token(customer_subject(customer_id))
    assert read_token(token) == ("customer", customer_id)
    try:
        read_mcp_customer(token)
    except TokenError:
        return
    raise AssertionError("an API token must not pass the MCP audience check")


def test_expired_and_staff_mcp_tokens_are_rejected():
    customer_id = uuid.uuid4()
    expired = issue_mcp_token(customer_id, now=datetime.now(UTC) - timedelta(hours=1))
    settings = get_settings()
    staff = jwt.encode(
        {
            "sub": staff_subject(uuid.uuid4()),
            "iss": settings.jwt_issuer,
            "aud": MCP_AUDIENCE,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    for token in (expired, staff):
        try:
            read_mcp_customer(token)
        except TokenError:
            continue
        raise AssertionError("token was accepted")
