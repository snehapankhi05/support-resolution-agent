"""Look up uncertain refunds. This module never creates a provider payment."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActionProposal, RefundOperation, Ticket, WorkflowRun
from app.services.payments import PaymentProvider, ProviderUnavailable
from app.services.refunds import (
    _audit_only,
    _lock_keys,
    _ProviderCall,
    _save_result,
)

RECONCILE_STATUSES = frozenset({"reserved", "unknown", "submitted", "processing"})
ABSENCE_GRACE = timedelta(minutes=2)
RETRY_SECONDS = 30


@dataclass(frozen=True)
class ReconcileResult:
    """One lookup outcome. retry_after_seconds is None when the row should be left alone."""

    operation_id: uuid.UUID
    outcome: str
    status: str | None
    retry_after_seconds: int | None


def reconcile_operation(
    sessions: sessionmaker[Session],
    operation_id: uuid.UUID,
    provider: PaymentProvider,
    *,
    now: datetime | None = None,
) -> ReconcileResult:
    """Compare one stored operation with the provider. A missing or failed lookup does not POST.

    A matching record is copied onto `reserved`, `unknown`, `submitted`, or `processing`.
    `unknown` becomes `failed` and its reservation is released only after the absence
    grace and a confirmed 404. `reserved`, `submitted`, and `processing` keep their
    reservation when the provider has no record.
    """
    moment = now or datetime.now(UTC)
    plan = _load_plan(sessions, operation_id)
    if plan is None:
        return ReconcileResult(operation_id, "missing", None, None)
    if plan.status not in RECONCILE_STATUSES:
        return ReconcileResult(operation_id, "settled", plan.status, None)

    engine = sessions.kw["bind"]
    key1, key2 = _lock_keys(operation_id)
    connection = engine.connect()
    try:
        with connection.begin():
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:k1, :k2)"), {"k1": key1, "k2": key2}
            ).scalar_one()
        if not acquired:
            return ReconcileResult(operation_id, "retry", plan.status, RETRY_SECONDS)
        current = _load_plan(sessions, operation_id)
        if current is None:
            return ReconcileResult(operation_id, "missing", None, None)
        if current.status not in RECONCILE_STATUSES:
            return ReconcileResult(operation_id, "settled", current.status, None)
        try:
            found = provider.lookup_refund(idempotency_key=current.idempotency_key)
        except ProviderUnavailable:
            _audit_only(
                sessions,
                current.call,
                "refund_reconcile_lookup_failed",
                {
                    "operation_id": str(current.operation_id),
                    "amount_minor": current.amount_minor,
                    "status": current.status,
                },
            )
            return ReconcileResult(operation_id, "retry", current.status, RETRY_SECONDS)
        if found is None:
            return _confirmed_absence(sessions, current, moment)
        if found.amount_minor != current.amount_minor or found.order_id != str(current.order_id):
            _audit_only(
                sessions,
                current.call,
                "refund_payload_conflict",
                {
                    "order_id": str(current.order_id),
                    "amount_minor": current.amount_minor,
                    "provider_amount_minor": found.amount_minor,
                    "provider_order_id": found.order_id,
                },
            )
            return ReconcileResult(operation_id, "conflict", current.status, None)
        if current.status == found.status and current.provider_reference == found.provider_reference:
            return ReconcileResult(operation_id, "unchanged", current.status, None)
        _save_result(
            sessions,
            current.call,
            found.status,
            found.provider_reference,
            from_statuses=RECONCILE_STATUSES,
        )
        outcome = "released" if found.status == "failed" else "adopted"
        return ReconcileResult(operation_id, outcome, found.status, None)
    finally:
        with connection.begin():
            connection.execute(
                text("SELECT pg_advisory_unlock(:k1, :k2)"), {"k1": key1, "k2": key2}
            )
        connection.close()


def open_reconciliation_ids(sessions: sessionmaker[Session]) -> list[uuid.UUID]:
    """Return operations whose provider outcome is still open."""
    with sessions() as session:
        return list(
            session.scalars(
                select(RefundOperation.id).where(RefundOperation.status.in_(RECONCILE_STATUSES))
            )
        )


@dataclass(frozen=True)
class _Plan:
    operation_id: uuid.UUID
    order_id: uuid.UUID
    amount_minor: int
    idempotency_key: str
    status: str
    provider_reference: str | None
    updated_at: datetime
    call: _ProviderCall


def _load_plan(sessions: sessionmaker[Session], operation_id: uuid.UUID) -> _Plan | None:
    with sessions() as session:
        operation = session.get(RefundOperation, operation_id)
        if operation is None:
            return None
        proposal = session.get(ActionProposal, operation.proposal_id)
        run = session.get(WorkflowRun, proposal.run_id) if proposal is not None else None
        ticket_id = run.ticket_id if run is not None else None
        if proposal is None or run is None or ticket_id is None:
            return None
        if session.get(Ticket, ticket_id) is None:
            return None
        updated = operation.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return _Plan(
            operation_id=operation.id,
            order_id=operation.order_id,
            amount_minor=operation.amount_minor,
            idempotency_key=operation.idempotency_key,
            status=operation.status,
            provider_reference=operation.provider_reference,
            updated_at=updated,
            call=_ProviderCall(
                operation_id=operation.id,
                order_id=operation.order_id,
                proposal_id=proposal.id,
                run_id=run.id,
                ticket_id=ticket_id,
                amount_minor=operation.amount_minor,
                idempotency_key=operation.idempotency_key,
            ),
        )


def _confirmed_absence(
    sessions: sessionmaker[Session], plan: _Plan, now: datetime
) -> ReconcileResult:
    _audit_only(
        sessions,
        plan.call,
        "refund_reconcile_absent",
        {
            "operation_id": str(plan.operation_id),
            "amount_minor": plan.amount_minor,
            "status": plan.status,
        },
    )
    aged = now - plan.updated_at >= ABSENCE_GRACE
    if plan.status == "unknown" and aged:
        _save_result(sessions, plan.call, "failed", None, from_statuses=frozenset({"unknown"}))
        return ReconcileResult(plan.operation_id, "released", "failed", None)
    if aged:
        return ReconcileResult(plan.operation_id, "held", plan.status, None)
    return ReconcileResult(plan.operation_id, "retry", plan.status, RETRY_SECONDS)
