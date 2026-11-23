"""Execute an approved refund. This module is ordinary backend code, not an MCP tool.

The caller passes a proposal id. Amount, customer and approval are read from the database.
Reservation commits before any provider call. A timeout is stored as unknown and is not retried here.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    ActionProposal,
    Approval,
    AuditEvent,
    Order,
    OrderItem,
    RefundOperation,
    Return,
    Ticket,
    WorkflowRun,
)
from app.services.eligibility import (
    RefundFacts,
    approval_is_current,
    evaluate_refund_eligibility,
)
from app.services.payments import (
    PaymentProvider,
    ProviderPayloadConflict,
    ProviderRecord,
    ProviderUnavailable,
)
from app.services.policy import RefundPolicySnapshot, current_refund_policy, policy_effect_name

REFUND_ACTION = "request_refund_approval"
RECOVERABLE = frozenset({"reserved", "unknown"})


@dataclass(frozen=True)
class RefundExecutionResult:
    outcome: str
    operation_id: uuid.UUID | None
    amount_minor: int | None
    reason: str
    provider_called: bool


@dataclass(frozen=True)
class _ProviderCall:
    operation_id: uuid.UUID
    order_id: uuid.UUID
    proposal_id: uuid.UUID
    run_id: uuid.UUID
    ticket_id: uuid.UUID
    amount_minor: int
    idempotency_key: str


def refund_payload(
    order_id: uuid.UUID,
    amount_minor: int,
    *,
    policy_effect: str,
    policy_versions: list[dict],
) -> dict:
    """Canonical proposal body. The hash is bound to this exact content."""
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise TypeError("amount_minor must be an int number of paise")
    if policy_effect not in {"allow", "conflict", "none"}:
        raise ValueError("policy_effect must be allow, conflict, or none")
    return {
        "action_type": REFUND_ACTION,
        "amount_minor": amount_minor,
        "order_id": str(order_id),
        "policy_effect": policy_effect,
        "policy_versions": canonical_policy_versions(policy_versions),
    }


def canonical_policy_versions(versions: list[dict]) -> list[dict]:
    """Sorted key and version pairs. Citation text is not part of the hash."""
    if not isinstance(versions, list):
        raise TypeError("policy_versions must be a list")
    normalized: list[tuple[str, int]] = []
    for item in versions:
        if not isinstance(item, dict):
            raise TypeError("policy version must include policy_key and version")
        key = item.get("policy_key")
        version = item.get("version")
        if not isinstance(key, str) or not key.strip():
            raise TypeError("policy_key must be a non-empty string")
        if isinstance(version, bool) or not isinstance(version, int):
            raise TypeError("policy version must be an int")
        normalized.append((key, version))
    return [
        {"policy_key": key, "version": version}
        for key, version in sorted(set(normalized))
    ]


def hash_payload(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def refund_idempotency_key(proposal_id: uuid.UUID) -> str:
    """Stable key for one proposal. Retries reuse it; they never mint a second key."""
    return f"refund-{proposal_id}"


def execute_approved_refund(
    sessions: sessionmaker[Session],
    proposal_id: uuid.UUID,
    provider: PaymentProvider,
    *,
    now: datetime | None = None,
) -> RefundExecutionResult:
    """Reserve a refund, then look the idempotency key up before any new payment post.

    A confirmed provider record is copied onto the ledger. A new post happens only
    for a reserved row whose provider has no record and whose approval still matches.
    """
    moment = now or datetime.now(UTC)
    reserved = _reserve(sessions, proposal_id, moment)
    if isinstance(reserved, RefundExecutionResult):
        return reserved
    return _call_provider_and_save(sessions, reserved, provider, moment)


def _reserve(
    sessions: sessionmaker[Session], proposal_id: uuid.UUID, now: datetime
) -> RefundExecutionResult | _ProviderCall:
    with sessions.begin() as session:
        proposal = session.get(ActionProposal, proposal_id)
        if proposal is None or proposal.action_type != REFUND_ACTION:
            return _rejected(None, "proposal_not_found")
        run = session.get(WorkflowRun, proposal.run_id)
        ticket = session.get(Ticket, run.ticket_id) if run is not None else None
        if ticket is None or ticket.order_id is None:
            return _rejected(None, "ticket_not_found")

        order = session.execute(
            select(Order).where(Order.id == ticket.order_id).with_for_update()
        ).scalar_one()
        proposal = session.execute(
            select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update()
        ).scalar_one()
        existing = session.scalar(
            select(RefundOperation).where(RefundOperation.proposal_id == proposal.id)
        )
        if existing is not None:
            if existing.status in RECOVERABLE:
                return _call_for(existing, proposal, ticket)
            return RefundExecutionResult(
                "already_recorded",
                existing.id,
                existing.amount_minor,
                existing.status,
                False,
            )
        if order.customer_id != ticket.customer_id:
            return _rejected(None, "not_owner")

        approval = session.scalar(select(Approval).where(Approval.proposal_id == proposal.id))
        approval_reason = approval_is_current(
            status=None if approval is None else approval.status,
            expires_at=None if approval is None else approval.expires_at,
            reviewer_present=approval is not None and approval.reviewer_id is not None,
            decided_at=None if approval is None else approval.decided_at,
            now=now,
        )
        if approval_reason is not None:
            return _rejected(None, approval_reason)
        if proposal.status != "approved":
            return _rejected(None, "approval_required")

        item_amounts = session.scalars(
            select(OrderItem.paid_amount_minor).where(OrderItem.order_id == order.id)
        ).all()
        returned_paid = sum(item_amounts, 0)
        latest_return = session.scalar(
            select(Return).where(Return.order_id == order.id).order_by(Return.created_at.desc())
        )
        blocking = session.scalar(
            select(RefundOperation)
            .where(
                RefundOperation.order_id == order.id,
                RefundOperation.status.in_(("reserved", "processing", "succeeded")),
            )
            .order_by(RefundOperation.created_at.desc())
        )
        snapshot = current_refund_policy(session)
        decision = evaluate_refund_eligibility(
            RefundFacts(
                order_status=order.status,
                order_customer_id=order.customer_id,
                requesting_customer_id=ticket.customer_id,
                delivered_at=order.delivered_at,
                return_requested_at=None if latest_return is None else latest_return.created_at,
                return_status=None if latest_return is None else latest_return.status,
                inspection_status=None if latest_return is None else latest_return.inspection_status,
                paid_amount_minor=order.paid_amount_minor,
                refund_committed_minor=order.refund_committed_minor,
                returned_items_paid_minor=returned_paid,
                existing_refund_status=None if blocking is None else blocking.status,
                existing_refund_amount_minor=None if blocking is None else blocking.amount_minor,
                policy_found=snapshot.policy_found,
                policy_conflict=snapshot.policy_conflict,
            )
        )
        if not decision.eligible:
            return _rejected(None, decision.reason)

        expected = _expected_payload(order.id, decision.amount_minor, snapshot)
        mismatch = _hash_reason(proposal, expected)
        if mismatch is not None:
            return _rejected(None, mismatch)

        operation = RefundOperation(
            order_id=order.id,
            proposal_id=proposal.id,
            amount_minor=decision.amount_minor,
            idempotency_key=refund_idempotency_key(proposal.id),
            status="reserved",
        )
        order.refund_committed_minor = order.refund_committed_minor + decision.amount_minor
        session.add(operation)
        session.flush()
        _audit(
            session,
            run.id,
            "refund_reserved",
            {
                "order_id": str(order.id),
                "proposal_id": str(proposal.id),
                "amount_minor": decision.amount_minor,
                "status": "reserved",
            },
        )
        return _call_for(operation, proposal, ticket)


def _call_for(operation: RefundOperation, proposal: ActionProposal, ticket: Ticket) -> _ProviderCall:
    return _ProviderCall(
        operation_id=operation.id,
        order_id=operation.order_id,
        proposal_id=proposal.id,
        run_id=proposal.run_id,
        ticket_id=ticket.id,
        amount_minor=operation.amount_minor,
        idempotency_key=operation.idempotency_key,
    )


def _call_provider_and_save(
    sessions: sessionmaker[Session],
    plan: _ProviderCall,
    provider: PaymentProvider,
    now: datetime,
) -> RefundExecutionResult:
    """Look up the idempotency key, then post only when that lookup confirms no record.

    The advisory lock covers lookup and the possible post. The order-row transaction
    is not held open during either network call.
    """
    engine = sessions.kw["bind"]
    key1, key2 = _lock_keys(plan.operation_id)
    connection = engine.connect()
    try:
        with connection.begin():
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:k1, :k2)"), {"k1": key1, "k2": key2}
            ).scalar_one()
        if not acquired:
            return _already(plan, "reserved")
        current = _operation_status(sessions, plan.operation_id)
        if current not in RECOVERABLE:
            return _already(plan, current or "reserved")
        try:
            found = provider.lookup_refund(idempotency_key=plan.idempotency_key)
        except ProviderUnavailable:
            return RefundExecutionResult(
                current, plan.operation_id, plan.amount_minor, "lookup_unavailable", False
            )
        if found is not None:
            return _adopt_or_conflict(sessions, plan, found)
        if current == "unknown":
            return RefundExecutionResult(
                "unknown", plan.operation_id, plan.amount_minor, "provider_has_no_record", False
            )
        block = _block_new_submission(sessions, plan, now)
        if block is not None:
            return block
        try:
            result = provider.create_refund(
                idempotency_key=plan.idempotency_key,
                amount_minor=plan.amount_minor,
                order_id=str(plan.order_id),
            )
        except ProviderPayloadConflict:
            _audit_only(
                sessions,
                plan,
                "refund_payload_conflict",
                {"order_id": str(plan.order_id), "amount_minor": plan.amount_minor},
            )
            return RefundExecutionResult(
                "conflict", plan.operation_id, plan.amount_minor, "payload_conflict", True
            )
        except ProviderUnavailable:
            _save_result(sessions, plan, "unknown", None)
            return RefundExecutionResult(
                "unknown", plan.operation_id, plan.amount_minor, "unknown", True
            )
        _save_result(sessions, plan, result.status, result.provider_reference)
        return RefundExecutionResult(
            result.status, plan.operation_id, plan.amount_minor, result.status, True
        )
    finally:
        with connection.begin():
            connection.execute(
                text("SELECT pg_advisory_unlock(:k1, :k2)"), {"k1": key1, "k2": key2}
            )
        connection.close()


def _adopt_or_conflict(
    sessions: sessionmaker[Session], plan: _ProviderCall, found: ProviderRecord
) -> RefundExecutionResult:
    if found.amount_minor != plan.amount_minor or found.order_id != str(plan.order_id):
        _audit_only(
            sessions,
            plan,
            "refund_payload_conflict",
            {
                "order_id": str(plan.order_id),
                "amount_minor": plan.amount_minor,
                "provider_amount_minor": found.amount_minor,
            },
        )
        return RefundExecutionResult(
            "conflict", plan.operation_id, plan.amount_minor, "payload_conflict", False
        )
    _save_result(
        sessions,
        plan,
        found.status,
        found.provider_reference,
        from_statuses=RECOVERABLE,
    )
    return RefundExecutionResult(
        found.status, plan.operation_id, plan.amount_minor, found.status, False
    )


def _block_new_submission(
    sessions: sessionmaker[Session], plan: _ProviderCall, now: datetime
) -> RefundExecutionResult | None:
    """Return a failure result when a reserved row must not be submitted. None means post."""
    with sessions.begin() as session:
        reason = _submission_block_reason(session, plan, now)
    if reason is None:
        return None
    _save_result(sessions, plan, "failed", None)
    return RefundExecutionResult("rejected", plan.operation_id, plan.amount_minor, reason, False)


def _save_result(
    sessions: sessionmaker[Session],
    plan: _ProviderCall,
    status: str,
    provider_reference: str | None,
    *,
    from_statuses: frozenset[str] = frozenset({"reserved"}),
) -> None:
    with sessions.begin() as session:
        operation = session.get(RefundOperation, plan.operation_id, with_for_update=True)
        if operation is None or operation.status not in from_statuses:
            return
        if (
            operation.proposal_id != plan.proposal_id
            or operation.order_id != plan.order_id
            or operation.amount_minor != plan.amount_minor
            or operation.idempotency_key != plan.idempotency_key
        ):
            return
        order = session.get(Order, operation.order_id, with_for_update=True)
        operation.status = status
        operation.provider_reference = provider_reference
        operation.updated_at = datetime.now(UTC)
        if status == "failed" and order is not None:
            order.refund_committed_minor = order.refund_committed_minor - operation.amount_minor
        proposal = session.get(ActionProposal, plan.proposal_id)
        if proposal is not None and status == "succeeded":
            proposal.status = "executed"
        elif proposal is not None and status == "failed":
            proposal.status = "failed"
        _audit(
            session,
            plan.run_id,
            "refund_provider_result",
            {
                "order_id": str(plan.order_id),
                "proposal_id": str(plan.proposal_id),
                "amount_minor": plan.amount_minor,
                "status": status,
            },
        )
    if status in {"unknown", "submitted", "processing"}:
        _enqueue_open_operation(sessions, plan)
    if status != "succeeded":
        return
    try:
        with sessions.begin() as session:
            ticket = session.get(Ticket, plan.ticket_id)
            if ticket is not None:
                ticket.status = "resolved"
                ticket.updated_at = datetime.now(UTC)
    except SQLAlchemyError:
        with sessions.begin() as session:
            _audit(
                session,
                plan.run_id,
                "ticket_update_failed",
                {"proposal_id": str(plan.proposal_id), "status": "succeeded"},
            )


def _enqueue_open_operation(sessions: sessionmaker[Session], plan: _ProviderCall) -> None:
    """Publish the operation id after the status commit. A broker failure leaves the row open."""
    try:
        from app.worker import BROKER_ERRORS, enqueue_reconciliation

        enqueue_reconciliation(plan.operation_id)
    except BROKER_ERRORS:
        _audit_only(
            sessions,
            plan,
            "refund_reconcile_enqueue_failed",
            {
                "operation_id": str(plan.operation_id),
                "amount_minor": plan.amount_minor,
            },
        )


def _submission_block_reason(session: Session, plan: _ProviderCall, now: datetime) -> str | None:
    """Re-check approval and amount while this reservation is excluded from the balance."""
    operation = session.get(RefundOperation, plan.operation_id, with_for_update=True)
    order = session.get(Order, plan.order_id, with_for_update=True)
    proposal = session.get(ActionProposal, plan.proposal_id)
    if operation is None or order is None or proposal is None or operation.status != "reserved":
        return "proposal_not_found"
    run = session.get(WorkflowRun, proposal.run_id)
    ticket = session.get(Ticket, plan.ticket_id)
    if run is None or ticket is None or ticket.customer_id != order.customer_id:
        return "not_owner"
    approval = session.scalar(select(Approval).where(Approval.proposal_id == proposal.id))
    approval_reason = approval_is_current(
        status=None if approval is None else approval.status,
        expires_at=None if approval is None else approval.expires_at,
        reviewer_present=approval is not None and approval.reviewer_id is not None,
        decided_at=None if approval is None else approval.decided_at,
        now=now,
    )
    if approval_reason is not None:
        return approval_reason
    if proposal.status != "approved":
        return "approval_required"
    item_amounts = session.scalars(
        select(OrderItem.paid_amount_minor).where(OrderItem.order_id == order.id)
    ).all()
    latest_return = session.scalar(
        select(Return).where(Return.order_id == order.id).order_by(Return.created_at.desc())
    )
    blocking = session.scalar(
        select(RefundOperation)
        .where(
            RefundOperation.order_id == order.id,
            RefundOperation.id != operation.id,
            RefundOperation.status.in_(("reserved", "processing", "succeeded")),
        )
        .order_by(RefundOperation.created_at.desc())
    )
    snapshot = current_refund_policy(session)
    decision = evaluate_refund_eligibility(
        RefundFacts(
            order_status=order.status,
            order_customer_id=order.customer_id,
            requesting_customer_id=ticket.customer_id,
            delivered_at=order.delivered_at,
            return_requested_at=None if latest_return is None else latest_return.created_at,
            return_status=None if latest_return is None else latest_return.status,
            inspection_status=None if latest_return is None else latest_return.inspection_status,
            paid_amount_minor=order.paid_amount_minor,
            refund_committed_minor=order.refund_committed_minor - operation.amount_minor,
            returned_items_paid_minor=sum(item_amounts, 0),
            existing_refund_status=None if blocking is None else blocking.status,
            existing_refund_amount_minor=None if blocking is None else blocking.amount_minor,
            policy_found=snapshot.policy_found,
            policy_conflict=snapshot.policy_conflict,
        )
    )
    if not decision.eligible:
        return decision.reason
    if decision.amount_minor != operation.amount_minor:
        return "amount_changed"
    expected = _expected_payload(order.id, decision.amount_minor, snapshot)
    return _hash_reason(proposal, expected)


def _expected_payload(order_id: uuid.UUID, amount_minor: int, snapshot: RefundPolicySnapshot) -> dict:
    return refund_payload(
        order_id,
        amount_minor,
        policy_effect=policy_effect_name(snapshot.policy_found, snapshot.policy_conflict),
        policy_versions=list(snapshot.versions),
    )


def _hash_reason(proposal: ActionProposal, expected: dict) -> str | None:
    """None when the stored hash is the rebuilt payload. A policy change is not an amount change."""
    if hash_payload(proposal.payload) != proposal.payload_hash:
        return "payload_hash_mismatch"
    if proposal.payload_hash == hash_payload(expected):
        return None
    if proposal.payload.get("amount_minor") != expected["amount_minor"]:
        return "amount_changed"
    if proposal.payload.get("policy_effect") != expected["policy_effect"]:
        return "policy_changed"
    if proposal.payload.get("policy_versions") != expected["policy_versions"]:
        return "policy_version_changed"
    return "amount_changed"


def _audit_only(
    sessions: sessionmaker[Session], plan: _ProviderCall, event_type: str, payload: dict
) -> None:
    with sessions.begin() as session:
        _audit(session, plan.run_id, event_type, payload)


def _operation_status(sessions: sessionmaker[Session], operation_id: uuid.UUID) -> str | None:
    with sessions() as session:
        return session.scalar(
            select(RefundOperation.status).where(RefundOperation.id == operation_id)
        )


def _already(plan: _ProviderCall, status: str) -> RefundExecutionResult:
    return RefundExecutionResult(
        "already_recorded", plan.operation_id, plan.amount_minor, status, False
    )


def _rejected(operation_id: uuid.UUID | None, reason: str) -> RefundExecutionResult:
    return RefundExecutionResult("rejected", operation_id, None, reason, False)


def _audit(session: Session, run_id: uuid.UUID | None, event_type: str, payload: dict) -> None:
    session.add(
        AuditEvent(
            run_id=run_id,
            actor="system",
            event_type=event_type,
            redacted_payload=payload,
        )
    )


def _lock_keys(operation_id: uuid.UUID) -> tuple[int, int]:
    raw = operation_id.bytes
    return int.from_bytes(raw[:4], "big", signed=True), int.from_bytes(raw[4:8], "big", signed=True)
