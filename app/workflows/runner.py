"""Run one stored workflow thread. Resume checks the approvals table first."""

import uuid

from langgraph.types import Command
from sqlalchemy import select

from app.config import get_settings
from app.db.models import ActionProposal, Approval, Ticket, WorkflowRun
from app.db.session import SessionLocal
from app.services.payments import HttpPaymentProvider, PaymentProvider
from app.services.recovery import schedule_recovery
from app.services.refunds import REFUND_ACTION
from app.workflows.checkpoints import postgres_checkpointer
from app.workflows.support_graph import FINISHED_RUN, build_support_graph, workflow_config

# The graph discards this value. It is not an approval.
_RESUME = "continue"


def execute_workflow(run_id: uuid.UUID, *, provider: PaymentProvider | None = None) -> str:
    """Continue a run from its stored thread until it pauses or finishes.

    A resume is sent only when an approval row is already committed. The queue
    payload cannot approve, reject, or choose an amount.
    """
    with SessionLocal() as session:
        run = session.get(WorkflowRun, run_id)
        if run is None:
            return "missing"
        ticket = session.get(Ticket, run.ticket_id)
        if ticket is None:
            return "missing"
        status = run.status
        thread_id = run.thread_id
        ticket_id = str(ticket.id)
    if status in FINISHED_RUN:
        schedule_recovery(SessionLocal)
        return "finished"

    owns_provider = provider is None
    payment = (
        provider
        if provider is not None
        else HttpPaymentProvider(get_settings().payment_provider_url)
    )
    try:
        with postgres_checkpointer() as saver:
            graph = build_support_graph(payment, saver)
            config = workflow_config(thread_id)
            if graph.get_state(config).interrupts:
                if _stored_decision(run_id) is None:
                    return "paused"
                graph.invoke(Command(resume=_RESUME), config)
            else:
                graph.invoke({"ticket_id": ticket_id}, config)
        with SessionLocal() as session:
            current = session.scalar(select(WorkflowRun.status).where(WorkflowRun.id == run_id))
        if current in FINISHED_RUN:
            return "finished"
        return "paused"
    finally:
        if owns_provider:
            payment.close()
        schedule_recovery(SessionLocal)


def _stored_decision(run_id: uuid.UUID) -> str | None:
    """Return approved or rejected only when the proposal and approval rows agree."""
    with SessionLocal() as session:
        proposal = session.scalar(
            select(ActionProposal)
            .where(
                ActionProposal.run_id == run_id,
                ActionProposal.action_type == REFUND_ACTION,
            )
            .order_by(ActionProposal.created_at.desc())
        )
        if proposal is None or proposal.status not in {"approved", "rejected"}:
            return None
        approval = session.scalar(select(Approval).where(Approval.proposal_id == proposal.id))
        if (
            approval is None
            or approval.status != proposal.status
            or approval.reviewer_id is None
            or approval.decided_at is None
        ):
            return None
        return approval.status
