"""The worker uses the stored thread and a Postgres checkpoint in schema langgraph."""

import threading
import uuid

import pytest
from sqlalchemy import func, select, text

from app.api.deps import Principal
from app.db.models import ActionProposal, Order, RefundOperation, StaffUser, Ticket, WorkflowRun
from app.db.session import SessionLocal
from app.services.approvals import approve_or_reject_proposal
from app.services.payments import ProviderRecord
from app.workflows.checkpoints import postgres_checkpointer
from app.workflows.runner import execute_workflow
from app.workflows.support_graph import build_support_graph, workflow_config
from tests.integration.test_refund_execution import PAID, RecordingProvider, _factory
from tests.integration.test_support_graph import _case

pytestmark = pytest.mark.integration


def _checkpoint_facts(thread_id: str) -> tuple[str, list[str], int]:
    with SessionLocal() as session:
        database = session.scalar(text("SELECT current_database()"))
        schemas = list(
            session.scalars(
                text(
                    "SELECT n.nspname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relname = 'checkpoints' AND c.relkind = 'r'"
                )
            )
        )
        stored = session.scalar(
            text("SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = :thread"),
            {"thread": thread_id},
        )
    return str(database), schemas, int(stored or 0)


def test_worker_runs_an_order_status_thread_once(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        _ticket_id, run_id, thread_id = _case(
            session, category="order_status", with_policy=False
        )
    provider = RecordingProvider()
    try:
        first = execute_workflow(run_id, provider=provider)
        second = execute_workflow(run_id, provider=provider)
        database, schemas, stored = _checkpoint_facts(thread_id)
        with SessionLocal() as session:
            run = session.get(WorkflowRun, run_id)
            proposals = session.scalar(
                select(func.count())
                .select_from(ActionProposal)
                .where(ActionProposal.run_id == run_id)
            )
        assert first == "finished"
        assert second == "finished"
        assert run is not None and run.status == "completed"
        assert run.thread_id == thread_id
        assert proposals == 0
        assert provider.calls == []
        assert database == "support_test"
        assert schemas == ["langgraph"]
        assert stored >= 1
    finally:
        bind.dispose()


def test_worker_pause_survives_a_new_checkpointer(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        _ticket_id, run_id, thread_id = _case(session, category="refund_request")
    provider = RecordingProvider()
    try:
        first = execute_workflow(run_id, provider=provider)
        with postgres_checkpointer() as saver:
            snapshot = build_support_graph(provider, saver).get_state(workflow_config(thread_id))
        second = execute_workflow(run_id, provider=provider)
        database, schemas, stored = _checkpoint_facts(thread_id)
        with SessionLocal() as session:
            run = session.get(WorkflowRun, run_id)
            proposals = session.scalars(
                select(ActionProposal).where(ActionProposal.run_id == run_id)
            ).all()
        assert first == "paused"
        assert second == "paused"
        assert snapshot.interrupts
        assert snapshot.config["configurable"]["thread_id"] == thread_id
        assert run is not None and run.status == "awaiting_approval"
        assert len(proposals) == 1
        assert proposals[0].status == "pending_approval"
        assert provider.calls == []
        assert database == "support_test"
        assert schemas == ["langgraph"]
        assert stored >= 1
    finally:
        bind.dispose()


def _decide(sessions, proposal_id, decision: str) -> None:
    with sessions.begin() as session:
        reviewer = StaffUser(
            external_auth_subject=f"test|resume-{uuid.uuid4().hex}",
            name="Reviewer",
            role="reviewer",
        )
        session.add(reviewer)
        session.flush()
        approve_or_reject_proposal(
            session,
            proposal_id,
            Principal(
                actor="staff",
                id=reviewer.id,
                email="reviewer@resume.test",
                name="Reviewer",
                role="reviewer",
            ),
            decision,
        )


def test_approval_resume_pays_once_across_a_restart(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        _ticket_id, run_id, _thread_id = _case(session, category="refund_request")
    provider = RecordingProvider()
    try:
        paused = execute_workflow(run_id, provider=provider)
        with SessionLocal() as session:
            proposal_id = session.scalar(
                select(ActionProposal.id).where(ActionProposal.run_id == run_id)
            )
        _decide(sessions, proposal_id, "approve")
        paid = execute_workflow(run_id, provider=provider)
        repeated = execute_workflow(run_id, provider=provider)
        with SessionLocal() as session:
            operations = session.scalar(
                select(func.count())
                .select_from(RefundOperation)
                .where(RefundOperation.proposal_id == proposal_id)
            )
        assert paused == "paused"
        assert paid == "finished"
        assert repeated == "finished"
        assert operations == 1
        assert provider.calls == [f"refund-{proposal_id}"]
    finally:
        bind.dispose()


def test_rejection_resume_does_not_call_the_provider(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        ticket_id, run_id, _thread_id = _case(session, category="refund_request")
    provider = RecordingProvider()
    try:
        paused = execute_workflow(run_id, provider=provider)
        with SessionLocal() as session:
            proposal_id = session.scalar(
                select(ActionProposal.id).where(ActionProposal.run_id == run_id)
            )
        _decide(sessions, proposal_id, "reject")
        finished = execute_workflow(run_id, provider=provider)
        with SessionLocal() as session:
            ticket = session.get(Ticket, ticket_id)
            run = session.get(WorkflowRun, run_id)
            operations = session.scalar(
                select(func.count())
                .select_from(RefundOperation)
                .where(RefundOperation.proposal_id == proposal_id)
            )
        assert paused == "paused"
        assert finished == "finished"
        assert provider.calls == []
        assert operations == 0
        assert ticket is not None and ticket.status == "open"
        assert run is not None and run.status == "completed"
    finally:
        bind.dispose()


class _CrashAfterAccept:
    def __init__(self) -> None:
        self.stored: ProviderRecord | None = None
        self.creates = 0
        self.lookups = 0

    def lookup_refund(self, *, idempotency_key: str) -> ProviderRecord | None:
        self.lookups += 1
        return self.stored

    def create_refund(self, *, idempotency_key: str, amount_minor: int, order_id: str) -> None:
        self.creates += 1
        self.stored = ProviderRecord(
            status="succeeded",
            amount_minor=amount_minor,
            order_id=order_id,
            provider_reference="pay_crash",
        )
        raise RuntimeError("worker died after the provider accepted")


def test_restart_after_provider_accept_adopts_the_same_payment(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        _ticket_id, run_id, _thread_id = _case(session, category="refund_request")
    provider = _CrashAfterAccept()
    try:
        assert execute_workflow(run_id, provider=provider) == "paused"
        with SessionLocal() as session:
            proposal_id = session.scalar(
                select(ActionProposal.id).where(ActionProposal.run_id == run_id)
            )
        _decide(sessions, proposal_id, "approve")
        with pytest.raises(RuntimeError, match="worker died"):
            execute_workflow(run_id, provider=provider)
        finished = execute_workflow(run_id, provider=provider)
        with SessionLocal() as session:
            operation = session.scalar(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            )
            committed = session.scalar(
                select(Order.refund_committed_minor).where(Order.id == operation.order_id)
            )
        assert finished == "finished"
        assert provider.creates == 1
        assert operation is not None and operation.status == "succeeded"
        assert operation.provider_reference == "pay_crash"
        assert committed == PAID
    finally:
        bind.dispose()


def test_two_workers_resume_one_thread_and_pay_once(engine):
    bind, sessions = _factory()
    with sessions.begin() as session:
        _ticket_id, run_id, _thread_id = _case(session, category="refund_request")
    provider = RecordingProvider()
    try:
        assert execute_workflow(run_id, provider=provider) == "paused"
        with SessionLocal() as session:
            proposal_id = session.scalar(
                select(ActionProposal.id).where(ActionProposal.run_id == run_id)
            )
        _decide(sessions, proposal_id, "approve")
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def resume() -> None:
            barrier.wait()
            try:
                execute_workflow(run_id, provider=provider)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        first = threading.Thread(target=resume)
        second = threading.Thread(target=resume)
        first.start()
        second.start()
        first.join()
        second.join()
        with SessionLocal() as session:
            operations = session.scalars(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            ).all()
        assert provider.calls == [f"refund-{proposal_id}"]
        assert len(operations) == 1 and operations[0].status == "succeeded"
        assert errors == []
    finally:
        bind.dispose()
