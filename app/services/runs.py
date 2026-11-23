"""Open one workflow run for a customer ticket. This does not execute or pay."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Ticket, WorkflowRun

ACTIVE_RUN_STATUSES = frozenset({"running", "awaiting_approval"})
ACTIVE_RUN_INDEX = "uq_one_active_run_per_ticket"


class RunStartError(Exception):
    """The run was refused. status_code is the HTTP status the route should return."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class RunStart:
    run_id: uuid.UUID
    ticket_id: uuid.UUID
    thread_id: str
    status: str
    created: bool


def start_workflow_run(
    session: Session, customer_id: uuid.UUID, ticket_id: uuid.UUID
) -> RunStart:
    """Store one running run for a ticket this customer owns.

    A running or awaiting-approval run is returned unchanged. The graph is not called.
    """
    ticket = session.execute(
        select(Ticket).where(Ticket.id == ticket_id).with_for_update()
    ).scalar_one_or_none()
    if ticket is None or ticket.customer_id != customer_id:
        raise RunStartError(404, "ticket not found")
    existing = _active_run(session, ticket.id)
    if existing is not None:
        return _payload(existing, created=False)

    run_id = uuid.uuid4()
    run = WorkflowRun(
        id=run_id,
        ticket_id=ticket.id,
        thread_id=str(run_id),
        status="running",
    )
    try:
        with session.begin_nested():
            session.add(run)
            session.flush()
    except IntegrityError as exc:
        if not _is_named_index(exc):
            raise
        winner = _active_run(session, ticket.id)
        if winner is None:
            raise
        return _payload(winner, created=False)
    return _payload(run, created=True)


def _active_run(session: Session, ticket_id: uuid.UUID) -> WorkflowRun | None:
    return session.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.ticket_id == ticket_id,
            WorkflowRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .order_by(WorkflowRun.started_at)
    )


def _payload(run: WorkflowRun, *, created: bool) -> RunStart:
    return RunStart(run.id, run.ticket_id, run.thread_id, run.status, created)


def _is_named_index(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None) == ACTIVE_RUN_INDEX
