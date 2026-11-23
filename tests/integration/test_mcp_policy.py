"""Policy search returns stored text only. An empty match is not a found policy."""

import uuid
from datetime import UTC, date, datetime, timedelta

import jwt
import pytest

from app.auth.tokens import (
    JWT_ALGORITHM,
    MCP_AUDIENCE,
    customer_subject,
    issue_mcp_token,
    issue_token,
    staff_subject,
)
from app.config import get_settings
from app.db.models import Customer, PolicyChunk, PolicyDocument
from app.db.session import SessionLocal
from app.mcp_servers.policy import server
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


def _customer() -> uuid.UUID:
    marker = uuid.uuid4().hex
    with SessionLocal() as session:
        person = Customer(
            external_auth_subject=f"mcp-policy|{marker}",
            name="Meera",
            email=f"policy-{marker}@example.com",
        )
        session.add(person)
        session.commit()
        return person.id


def test_unknown_text_is_not_a_policy(engine):
    token = issue_mcp_token(_customer())

    async def scenario():
        async with connected(_app(), token) as session:
            return await call_tool(session, "search_policy", {"query": f"missing-{uuid.uuid4().hex}"})

    body = run(scenario())
    assert body["success"] is True
    assert body["data"]["matched"] is False
    assert body["data"]["citations"] == []


def test_inserted_chunk_is_visible_to_any_customer_then_removed(engine):
    phrase = f"window-{uuid.uuid4().hex}"
    with SessionLocal() as session:
        document = PolicyDocument(
            policy_key=f"returns-{phrase}",
            version=1,
            effective_from=date.today(),
            source_uri="memory://policy",
        )
        session.add(document)
        session.flush()
        session.add(
            PolicyChunk(
                document_id=document.id,
                section_ref="1",
                content=f"A refund is allowed. Marker {phrase}.",
            )
        )
        session.commit()
        document_id = document.id
    first = issue_mcp_token(_customer())
    second = issue_mcp_token(_customer())

    async def scenario(token: str):
        async with connected(_app(), token) as session:
            return await call_tool(session, "search_policy", {"query": phrase})

    try:
        seen_by_a = run(scenario(first))
        seen_by_b = run(scenario(second))
    finally:
        with SessionLocal() as session:
            session.query(PolicyChunk).filter(PolicyChunk.document_id == document_id).delete()
            session.query(PolicyDocument).filter(PolicyDocument.id == document_id).delete()
            session.commit()
    assert seen_by_a["data"]["matched"] is True
    citation = seen_by_a["data"]["citations"][0]
    assert phrase in citation["content"]
    assert set(citation) == {"policy_key", "version", "section_ref", "content", "score"}
    assert seen_by_b["data"]["citations"] == seen_by_a["data"]["citations"]


def test_policy_schema_and_rejected_tokens(engine):
    customer_id = _customer()

    async def listed():
        async with connected(_app(), issue_mcp_token(customer_id)) as session:
            return await schemas(session)

    rows = run(listed())
    assert {row["name"] for row in rows} == {"search_policy"}
    assert schema_fields(rows[0]["schema"]).isdisjoint(FORBIDDEN_FIELDS)
    assert schema_fields(rows[0]["schema"]).isdisjoint(
        {"applies_to", "effect", "metadata", "policy_found", "policy_conflict"}
    )
    settings = get_settings()
    expired = issue_mcp_token(customer_id, now=datetime.now(UTC) - timedelta(hours=1))
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

    async def rejected():
        return [
            await http_status(_app(), {}),
            await http_status(_app(), {"Authorization": f"Bearer {expired}"}),
            await http_status(_app(), {"Authorization": f"Bearer {issue_token(customer_subject(customer_id))}"}),
            await http_status(_app(), {"Authorization": f"Bearer {staff}"}),
            await http_status(_app(), {}, path=f"/mcp?token={issue_mcp_token(customer_id)}"),
        ]

    assert run(rejected()) == [401, 401, 401, 401, 401]
