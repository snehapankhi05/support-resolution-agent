"""Reconciliation scheduling publishes an operation id and a retry delay."""

import uuid

import pytest

from app.services.reconciliation import ReconcileResult
from app.worker import (
    celery_app,
    complete_reconcile_attempt,
    enqueue_reconciliation,
    reconcile_payment,
    reconciliation_queue,
)


def test_enqueue_publishes_the_operation_id(monkeypatch):
    published: list[tuple[list[str], str]] = []

    def apply_async(*, args: list[str], queue: str) -> None:
        published.append((args, queue))

    monkeypatch.setattr(reconcile_payment, "apply_async", apply_async)
    operation_id = uuid.uuid4()
    enqueue_reconciliation(operation_id)
    assert published == [([str(operation_id)], reconciliation_queue())]
    assert reconcile_payment.name == "payments.reconcile"
    assert reconcile_payment.max_retries == 8


def test_beat_publishes_discovery_on_an_interval():
    entry = celery_app.conf.beat_schedule["discover-open-refunds"]
    assert entry["task"] == "payments.discover"
    assert entry["schedule"] == 60.0
    assert entry["options"]["queue"] == reconciliation_queue()


def test_a_retry_outcome_requests_another_lookup():
    seen: dict[str, int] = {}

    def retry(*, countdown: int) -> None:
        seen["countdown"] = countdown
        raise RuntimeError("scheduled")

    result = ReconcileResult(uuid.uuid4(), "retry", "unknown", 30)
    with pytest.raises(RuntimeError, match="scheduled"):
        complete_reconcile_attempt(retry, result)
    assert seen["countdown"] == 30


def test_a_settled_outcome_does_not_schedule_another_lookup():
    def retry(*, countdown: int) -> None:
        raise AssertionError(countdown)

    result = ReconcileResult(uuid.uuid4(), "adopted", "succeeded", None)
    assert complete_reconcile_attempt(retry, result) == "adopted"
