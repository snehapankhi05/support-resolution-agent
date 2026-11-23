"""Actions MCP server stores a proposal. It does not create a run or move money."""

import inspect
import uuid
from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
from sqlalchemy import func, select

from app.auth.passwords import hash_password
from app.auth.tokens import (
    JWT_ALGORITHM,
    MCP_AUDIENCE,
    customer_subject,
    issue_mcp_token,
    issue_token,
    read_mcp_customer,
    staff_subject,
)
from app.config import get_settings
from app.db.models import (
    ActionProposal,
    Customer,
    LoginAccount,
    Order,
    OrderItem,
    PolicyChunk,
    PolicyDocument,
    RefundOperation,
    Return,
    Ticket,
    WorkflowRun,
)
from app.db.session import SessionLocal
from app.mcp_servers import actions, queries
from app.mcp_servers.actions import server
from scripts.issue_mcp_token import main, token_for_email
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

PAID = 15000


def _app():
    return server.streamable_http_app(stateless_http=True, json_response=True)


def _case(
    *, with_run: bool, with_return: bool, run_status: str = "running"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    marker = uuid.uuid4().hex
    now = datetime.now(UTC)
    with SessionLocal() as session:
        person = Customer(
            external_auth_subject=f"mcp-action|{marker}",
            name="Kabir",
            email=f"action-{marker}@example.com",
        )
        other = Customer(
            external_auth_subject=f"mcp-action-other|{marker}",
            name="Isha",
            email=f"action-other-{marker}@example.com",
        )
        session.add_all([person, other])
        session.flush()
        order = Order(
            customer_id=person.id,
            status="delivered",
            paid_amount_minor=PAID,
            refund_committed_minor=0,
            delivered_at=now - timedelta(days=4),
        )
        session.add(order)
        session.flush()
        session.add(
            OrderItem(
                order_id=order.id,
                product_id="sku",
                product_name="Lamp",
                quantity=1,
                paid_amount_minor=PAID,
            )
        )
        if with_return:
            session.add(
                Return(
                    order_id=order.id,
                    status="received",
                    inspection_status="passed",
                    received_at=now - timedelta(days=1),
                    created_at=now - timedelta(days=2),
                )
            )
        ticket = Ticket(
            customer_id=person.id,
            order_id=order.id,
            category="refund_request",
            summary="Please refund this order",
        )
        foreign = Ticket(
            customer_id=other.id,
            order_id=order.id,
            category="refund_request",
            summary="Not this customer's ticket",
        )
        session.add_all([ticket, foreign])
        session.flush()
        if with_run:
            session.add(
                WorkflowRun(ticket_id=ticket.id, thread_id=f"thread-{marker}", status=run_status)
            )
        session.commit()
        return person.id, ticket.id, foreign.id


def test_proposal_requires_an_existing_run_and_hides_foreign_tickets(engine):
    customer_id, ticket_id, foreign_id = _case(with_run=False, with_return=True)
    token = issue_mcp_token(customer_id)

    async def scenario():
        async with connected(_app(), token) as session:
            missing_run = await call_tool(session, "create_refund_proposal", {"ticket_id": str(ticket_id)})
            foreign = await call_tool(session, "create_refund_proposal", {"ticket_id": str(foreign_id)})
            missing = await call_tool(
                session, "create_refund_proposal", {"ticket_id": str(uuid.uuid4())}
            )
            return missing_run, foreign, missing

    missing_run, foreign, missing = run(scenario())
    assert missing_run["error_code"] == "NO_ACTIVE_RUN"
    assert foreign == missing
    assert foreign["error_code"] == "TICKET_NOT_FOUND"
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(WorkflowRun).where(WorkflowRun.ticket_id == ticket_id)) == 0


@pytest.mark.parametrize("run_status", ["completed", "failed", "escalated"])
def test_inactive_run_does_not_store_a_proposal(engine, run_status):
    customer_id, ticket_id, _foreign = _case(with_run=True, with_return=True, run_status=run_status)
    token = issue_mcp_token(customer_id)

    async def scenario():
        async with connected(_app(), token) as session:
            return await call_tool(session, "create_refund_proposal", {"ticket_id": str(ticket_id)})

    body = run(scenario())
    assert body["success"] is False
    assert body["error_code"] == "NO_ACTIVE_RUN"
    with SessionLocal() as session:
        stored = session.scalar(
            select(ActionProposal.id)
            .join(WorkflowRun, ActionProposal.run_id == WorkflowRun.id)
            .where(WorkflowRun.ticket_id == ticket_id)
        )
        assert stored is None


@pytest.mark.parametrize("run_status", ["running", "awaiting_approval"])
def test_eligible_ticket_stores_one_pending_proposal(engine, run_status):
    customer_id, ticket_id, _foreign = _case(
        with_run=True, with_return=True, run_status=run_status
    )
    phrase = f"refund-rule-{uuid.uuid4().hex}"
    with SessionLocal() as session:
        document = PolicyDocument(
            policy_key=phrase,
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
                content=f"A refund is allowed {phrase}",
                chunk_metadata={"applies_to": "refund", "effect": "allow"},
            )
        )
        session.commit()
        document_id = document.id
        before = session.scalar(select(func.count()).select_from(RefundOperation))
    token = issue_mcp_token(customer_id)

    async def scenario():
        async with connected(_app(), token) as session:
            first = await call_tool(session, "create_refund_proposal", {"ticket_id": str(ticket_id)})
            second = await call_tool(session, "create_refund_proposal", {"ticket_id": str(ticket_id)})
            listed = await schemas(session)
            return first, second, listed

    try:
        first, second, listed = run(scenario())
    finally:
        with SessionLocal() as session:
            session.query(PolicyChunk).filter(PolicyChunk.document_id == document_id).delete()
            session.query(PolicyDocument).filter(PolicyDocument.id == document_id).delete()
            session.commit()
    assert first["success"] is True
    assert first["data"]["proposed"] is True
    assert first["data"]["created"] is True
    assert first["data"]["status"] == "pending_approval"
    assert first["data"]["amount_minor"] == PAID
    assert second["data"]["proposal_id"] == first["data"]["proposal_id"]
    assert second["data"]["created"] is False
    assert {row["name"] for row in listed} == {"create_refund_proposal"}
    assert schema_fields(listed[0]["schema"]).isdisjoint(FORBIDDEN_FIELDS)
    assert schema_fields(listed[0]["schema"]).isdisjoint(
        {"applies_to", "effect", "metadata", "policy_found", "policy_conflict"}
    )
    with SessionLocal() as session:
        stored = session.get(ActionProposal, uuid.UUID(first["data"]["proposal_id"]))
        assert stored is not None
        assert stored.payload["amount_minor"] == PAID
        assert session.scalar(select(func.count()).select_from(RefundOperation)) == before
    assert "execute_approved_refund" not in inspect.getsource(actions)
    assert "execute_approved_refund" not in inspect.getsource(queries)


def test_refund_keyword_does_not_authorize_a_proposal(engine):
    customer_id, ticket_id, _foreign = _case(with_run=True, with_return=True)
    phrase = f"prohibit-{uuid.uuid4().hex}"
    with SessionLocal() as session:
        document = PolicyDocument(
            policy_key=phrase,
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
                content="Refunds are prohibited for this order.",
                chunk_metadata={},
            )
        )
        session.commit()
        document_id = document.id
    token = issue_mcp_token(customer_id)

    async def scenario():
        async with connected(_app(), token) as session:
            return await call_tool(session, "create_refund_proposal", {"ticket_id": str(ticket_id)})

    try:
        body = run(scenario())
    finally:
        with SessionLocal() as session:
            session.query(PolicyChunk).filter(PolicyChunk.document_id == document_id).delete()
            session.query(PolicyDocument).filter(PolicyDocument.id == document_id).delete()
            session.commit()
    assert body["data"]["proposed"] is False
    assert body["data"]["reason"] == "no_policy"
    assert body["data"]["proposal_id"] is None
    with SessionLocal() as session:
        run_id = session.scalar(select(WorkflowRun.id).where(WorkflowRun.ticket_id == ticket_id))
        stored = session.scalar(select(ActionProposal).where(ActionProposal.run_id == run_id))
        assert stored is None


def test_explicit_deny_does_not_store_a_proposal(engine):
    customer_id, ticket_id, _foreign = _case(with_run=True, with_return=True)
    phrase = f"deny-{uuid.uuid4().hex}"
    with SessionLocal() as session:
        document = PolicyDocument(
            policy_key=phrase,
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
                content="A refund might be discussed here.",
                chunk_metadata={"applies_to": "refund", "effect": "deny"},
            )
        )
        session.commit()
        document_id = document.id
    token = issue_mcp_token(customer_id)

    async def scenario():
        async with connected(_app(), token) as session:
            return await call_tool(session, "create_refund_proposal", {"ticket_id": str(ticket_id)})

    try:
        body = run(scenario())
    finally:
        with SessionLocal() as session:
            session.query(PolicyChunk).filter(PolicyChunk.document_id == document_id).delete()
            session.query(PolicyDocument).filter(PolicyDocument.id == document_id).delete()
            session.commit()
    assert body["data"]["proposed"] is False
    assert body["data"]["reason"] == "policy_conflict"
    with SessionLocal() as session:
        run_id = session.scalar(select(WorkflowRun.id).where(WorkflowRun.ticket_id == ticket_id))
        stored = session.scalar(select(ActionProposal).where(ActionProposal.run_id == run_id))
        assert stored is None


def test_concurrent_calls_store_one_pending_proposal(engine):
    import threading

    from app.mcp_servers.queries import create_refund_proposal

    customer_id, ticket_id, _foreign = _case(with_run=True, with_return=True)
    phrase = f"allow-{uuid.uuid4().hex}"
    with SessionLocal() as session:
        document = PolicyDocument(
            policy_key=phrase,
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
                content="Stored policy text is not the decision.",
                chunk_metadata={"applies_to": "refund", "effect": "allow"},
            )
        )
        session.commit()
        document_id = document.id
        before = session.scalar(select(func.count()).select_from(RefundOperation))
    barrier = threading.Barrier(2)
    results: list = []
    errors: list = []

    def call() -> None:
        barrier.wait()
        try:
            results.append(create_refund_proposal(customer_id, ticket_id))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=call), threading.Thread(target=call)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        with SessionLocal() as session:
            session.query(PolicyChunk).filter(PolicyChunk.document_id == document_id).delete()
            session.query(PolicyDocument).filter(PolicyDocument.id == document_id).delete()
            session.commit()
    assert errors == []
    assert len(results) == 2
    proposal_ids = {row.data["proposal_id"] for row in results}
    assert len(proposal_ids) == 1
    assert None not in proposal_ids
    assert {row.data["created"] for row in results} == {True, False}
    with SessionLocal() as session:
        run_id = session.scalar(select(WorkflowRun.id).where(WorkflowRun.ticket_id == ticket_id))
        pending = session.scalars(
            select(ActionProposal).where(
                ActionProposal.run_id == run_id,
                ActionProposal.status == "pending_approval",
            )
        ).all()
        assert len(pending) == 1
        assert session.scalar(select(func.count()).select_from(RefundOperation)) == before


def test_unrelated_integrity_error_is_not_the_pending_constraint():
    from sqlalchemy.exc import IntegrityError

    from app.mcp_servers.queries import PENDING_PROPOSAL_CONSTRAINT, _is_named_constraint

    class Diag:
        constraint_name = "pk_action_proposals"

    class Orig(Exception):
        diag = Diag()

    exc = IntegrityError("INSERT", {}, Orig())
    assert _is_named_constraint(exc, PENDING_PROPOSAL_CONSTRAINT) is False
    assert _is_named_constraint(exc, "pk_action_proposals") is True


def test_named_customer_token_uses_the_login_owner(engine):
    marker = uuid.uuid4().hex
    email = f"mcp-cli-{marker}@example.com"
    with SessionLocal() as session:
        person = Customer(external_auth_subject=f"mcp-cli|{marker}", name="Dev", email=email)
        session.add(person)
        session.flush()
        session.add(LoginAccount(email=email, password_hash=hash_password("not-printed"), customer_id=person.id))
        session.commit()
        token = token_for_email(session, email.upper())
        assert read_mcp_customer(token) == person.id


def test_mcp_token_command_refuses_the_test_database(engine):
    with pytest.raises(SystemExit, match="support_test"):
        main(["asha@seed.example"])


def test_actions_server_rejects_bad_tokens(engine):
    customer_id = uuid.uuid4()
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
