"""Deterministic workflow. The checkpointer is in memory. No model and no worker."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import func, select

from app.api.deps import Principal
from app.db.models import (
    ActionProposal,
    Customer,
    Order,
    OrderItem,
    RefundOperation,
    Return,
    StaffUser,
    Ticket,
    WorkflowRun,
)
from app.services.approvals import approve_or_reject_proposal
from app.workflows.support_graph import build_support_graph, workflow_config
from tests.integration.test_refund_execution import (
    PAID,
    RecordingProvider,
    _factory,
    ensure_test_refund_policy,
)

pytestmark = pytest.mark.integration


def _case(
    session,
    *,
    category: str,
    delivered_days: int = 5,
    with_return: bool = True,
    inspection: str = "passed",
    return_requested_days_ago: int = 2,
    with_policy: bool = True,
):
    now = datetime.now(UTC)
    if with_policy:
        ensure_test_refund_policy(session, "allow")
    marker = uuid.uuid4().hex
    customer = Customer(
        external_auth_subject=f"test|graph-{marker}",
        name="Graph Customer",
        email=f"{marker}@graph.test",
    )
    session.add(customer)
    session.flush()
    order = Order(
        customer_id=customer.id,
        status="delivered",
        paid_amount_minor=PAID,
        refund_committed_minor=0,
        delivered_at=now - timedelta(days=delivered_days),
    )
    session.add(order)
    session.flush()
    session.add(
        OrderItem(
            order_id=order.id,
            product_id="sku-graph",
            product_name="Graph item",
            quantity=1,
            paid_amount_minor=PAID,
        )
    )
    if with_return:
        session.add(
            Return(
                order_id=order.id,
                status="received",
                inspection_status=inspection,
                received_at=now - timedelta(days=1),
                created_at=now - timedelta(days=return_requested_days_ago),
            )
        )
    ticket = Ticket(
        customer_id=customer.id,
        order_id=order.id,
        category=category,
        status="open",
        summary="graph case",
    )
    session.add(ticket)
    session.flush()
    thread_id = f"graph-{marker}"
    run = WorkflowRun(ticket_id=ticket.id, thread_id=thread_id, status="running")
    session.add(run)
    session.flush()
    return ticket.id, run.id, thread_id


def _graph(provider: RecordingProvider):
    return build_support_graph(provider, InMemorySaver()), provider


def _counts(sessions, run_id):
    with sessions() as session:
        proposals = session.scalar(
            select(func.count())
            .select_from(ActionProposal)
            .where(ActionProposal.run_id == run_id)
        )
        operations = session.scalar(
            select(func.count())
            .select_from(RefundOperation)
            .join(ActionProposal, ActionProposal.id == RefundOperation.proposal_id)
            .where(ActionProposal.run_id == run_id)
        )
        run = session.get(WorkflowRun, run_id)
        ticket = session.get(Ticket, run.ticket_id)
        return proposals, operations, run.status, ticket.status


def test_eligible_refund_pauses_without_payment(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        ticket_id, run_id, thread_id = _case(session, category="refund_request")
    provider = RecordingProvider()
    graph, _provider = _graph(provider)
    try:
        result = graph.invoke({"ticket_id": str(ticket_id)}, workflow_config(thread_id))
        proposals, operations, run_status, ticket_status = _counts(sessions, run_id)
        assert "__interrupt__" in result
        assert result["__interrupt__"][0].value["proposal_id"]
        assert proposals == 1
        assert operations == 0
        assert provider.calls == []
        assert run_status == "awaiting_approval"
        assert ticket_status == "awaiting_approval"
    finally:
        bind.dispose()


def test_order_status_does_not_propose(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        ticket_id, run_id, thread_id = _case(session, category="order_status", with_policy=False)
    provider = RecordingProvider()
    graph, _provider = _graph(provider)
    try:
        result = graph.invoke({"ticket_id": str(ticket_id)}, workflow_config(thread_id))
        proposals, operations, run_status, ticket_status = _counts(sessions, run_id)
        assert result["message"] == "The latest order status is delivered."
        assert result["terminal"] == "completed"
        assert proposals == 0
        assert operations == 0
        assert provider.calls == []
        assert run_status == "completed"
        assert ticket_status == "open"
        with sessions() as session:
            stored = session.get(Ticket, ticket_id).resolution_message
        assert stored == "The latest order status is delivered."
    finally:
        bind.dispose()


def test_damaged_item_escalates_without_a_proposal(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        ticket_id, run_id, thread_id = _case(session, category="damaged_item")
    provider = RecordingProvider()
    graph, _provider = _graph(provider)
    try:
        result = graph.invoke({"ticket_id": str(ticket_id)}, workflow_config(thread_id))
        proposals, operations, run_status, ticket_status = _counts(sessions, run_id)
        assert result["message"] == "A support specialist will review your case."
        assert result["terminal"] == "escalated"
        assert proposals == 0
        assert operations == 0
        assert provider.calls == []
        assert run_status == "escalated"
        assert ticket_status == "escalated"
        with sessions() as session:
            stored = session.get(Ticket, ticket_id).resolution_message
        assert stored == "A support specialist will review your case."
    finally:
        bind.dispose()


def test_late_return_is_ineligible_and_creates_no_proposal(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        ticket_id, run_id, thread_id = _case(
            session,
            category="refund_request",
            delivered_days=50,
            return_requested_days_ago=10,
        )
    provider = RecordingProvider()
    graph, _provider = _graph(provider)
    try:
        result = graph.invoke({"ticket_id": str(ticket_id)}, workflow_config(thread_id))
        proposals, operations, run_status, _ticket_status = _counts(sessions, run_id)
        assert "30 days" in result["message"]
        assert result["terminal"] == "completed"
        assert proposals == 0
        assert operations == 0
        assert provider.calls == []
        assert run_status == "completed"
        with sessions() as session:
            stored = session.get(Ticket, ticket_id).resolution_message
        assert stored is not None and "30 days" in stored
    finally:
        bind.dispose()


def test_forged_resume_does_not_pay(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        ticket_id, run_id, thread_id = _case(session, category="refund_request")
    provider = RecordingProvider()
    graph, _provider = _graph(provider)
    config = workflow_config(thread_id)
    try:
        graph.invoke({"ticket_id": str(ticket_id)}, config)
        forged = graph.invoke(Command(resume={"approved": True}), config)
        proposals, operations, run_status, ticket_status = _counts(sessions, run_id)
        assert "__interrupt__" in forged
        assert proposals == 1
        assert operations == 0
        assert provider.calls == []
        assert run_status == "awaiting_approval"
        assert ticket_status == "awaiting_approval"
    finally:
        bind.dispose()


def test_replay_reuses_one_proposal_and_one_payment(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        ticket_id, run_id, thread_id = _case(session, category="refund_request")
        reviewer = StaffUser(
            external_auth_subject=f"test|graph-reviewer-{uuid.uuid4().hex}",
            name="Reviewer",
            role="reviewer",
        )
        session.add(reviewer)
        session.flush()
        reviewer_id = reviewer.id
    provider = RecordingProvider()
    config = workflow_config(thread_id)
    try:
        first = build_support_graph(provider, InMemorySaver()).invoke(
            {"ticket_id": str(ticket_id)}, config
        )
        again = build_support_graph(provider, InMemorySaver()).invoke(
            {"ticket_id": str(ticket_id)}, config
        )
        proposals, _operations, _run_status, _ticket_status = _counts(sessions, run_id)
        assert proposals == 1
        assert first["__interrupt__"][0].value["proposal_id"] == (
            again["__interrupt__"][0].value["proposal_id"]
        )
        proposal_id = uuid.UUID(first["__interrupt__"][0].value["proposal_id"])
        with sessions.begin() as session:
            approve_or_reject_proposal(
                session,
                proposal_id,
                Principal(
                    actor="staff",
                    id=reviewer_id,
                    email="reviewer@graph.test",
                    name="Reviewer",
                    role="reviewer",
                ),
                "approve",
            )
        paid = build_support_graph(provider, InMemorySaver()).invoke(
            {"ticket_id": str(ticket_id)}, config
        )
        replay = build_support_graph(provider, InMemorySaver()).invoke(
            {"ticket_id": str(ticket_id)}, config
        )
        proposals, operations, run_status, ticket_status = _counts(sessions, run_id)
        assert paid["message"].startswith("Your refund of ")
        assert "confirmed" in paid["message"]
        assert replay["terminal"] == "completed"
        assert proposals == 1
        assert operations == 1
        assert provider.calls == [f"refund-{proposal_id}"]
        assert run_status == "completed"
        assert ticket_status == "resolved"
        with sessions() as session:
            stored = session.get(Ticket, ticket_id).resolution_message
        assert stored is None
    finally:
        bind.dispose()
