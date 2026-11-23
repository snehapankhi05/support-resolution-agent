"""Orders MCP server: the token's customer is the only customer the tools can see."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy.orm import Session

from app.auth.tokens import (
    JWT_ALGORITHM,
    MCP_AUDIENCE,
    customer_subject,
    issue_mcp_token,
    issue_token,
    staff_subject,
)
from app.config import get_settings
from app.db.models import Customer, Order
from app.db.session import SessionLocal
from app.mcp_servers.orders import server
from tests.mcp_client import (
    FORBIDDEN_FIELDS,
    call_tool,
    connected,
    http_status,
    run,
    schema_fields,
    schemas,
)

pytestmark = pytest.mark.integration


def _app():
    return server.streamable_http_app(stateless_http=True, json_response=True)


def _people(session: Session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    marker = uuid.uuid4().hex
    first = Customer(external_auth_subject=f"mcp|{marker}-a", name="Asha", email=f"a-{marker}@example.com")
    second = Customer(external_auth_subject=f"mcp|{marker}-b", name="Rohan", email=f"b-{marker}@example.com")
    session.add_all([first, second])
    session.flush()
    own = Order(
        customer_id=first.id,
        status="delivered",
        paid_amount_minor=10000,
        refund_committed_minor=0,
        delivered_at=datetime.now(UTC),
    )
    other = Order(
        customer_id=second.id,
        status="delivered",
        paid_amount_minor=20000,
        refund_committed_minor=0,
        delivered_at=datetime.now(UTC),
    )
    session.add_all([own, other])
    session.commit()
    return first.id, own.id, other.id


def test_customer_cannot_read_another_customers_order(engine):
    with SessionLocal() as session:
        customer_id, own_id, other_id = _people(session)
    token = issue_mcp_token(customer_id)

    async def scenario():
        async with connected(_app(), token) as session:
            own = await call_tool(session, "get_order", {"order_id": str(own_id)})
            foreign = await call_tool(session, "get_order", {"order_id": str(other_id)})
            missing = await call_tool(session, "get_order", {"order_id": str(uuid.uuid4())})
            hidden = [
                (
                    await call_tool(session, name, {"order_id": str(other_id)}),
                    await call_tool(session, name, {"order_id": str(uuid.uuid4())}),
                )
                for name in ("get_return", "get_refund_status")
            ]
            return own, foreign, missing, hidden

    own, foreign, missing, hidden = run(scenario())
    assert own["success"] is True
    assert own["data"]["id"] == str(own_id)
    assert own["data"]["paid_amount_minor"] == 10000
    assert foreign == missing
    assert foreign["success"] is False
    assert foreign["error_code"] == "ORDER_NOT_FOUND"
    assert "20000" not in str(foreign)
    for other, absent in hidden:
        assert other == absent
        assert other["error_code"] == "ORDER_NOT_FOUND"


def test_order_tool_schemas_have_no_identity_or_amount_fields(engine):
    with SessionLocal() as session:
        customer_id, _own, _other = _people(session)

    async def scenario():
        async with connected(_app(), issue_mcp_token(customer_id)) as session:
            return await schemas(session)

    listed = run(scenario())
    names = {row["name"] for row in listed}
    assert names == {"get_order", "get_return", "get_refund_status"}
    for row in listed:
        assert schema_fields(row["schema"]).isdisjoint(FORBIDDEN_FIELDS)


def test_orders_server_rejects_bad_tokens(engine):
    with SessionLocal() as session:
        customer_id, _own, _other = _people(session)
    settings = get_settings()
    expired = issue_mcp_token(customer_id, now=datetime.now(UTC) - timedelta(hours=1))
    api = issue_token(customer_subject(customer_id))
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
    valid = issue_mcp_token(customer_id)

    async def scenario():
        return [
            await http_status(_app(), {}),
            await http_status(_app(), {"Authorization": f"Bearer {expired}"}),
            await http_status(_app(), {"Authorization": f"Bearer {api}"}),
            await http_status(_app(), {"Authorization": f"Bearer {staff}"}),
            await http_status(_app(), {}, path=f"/mcp?token={valid}"),
        ]

    assert run(scenario()) == [401, 401, 401, 401, 401]
