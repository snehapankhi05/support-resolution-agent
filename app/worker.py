"""Celery entrypoint. Tasks carry ids. They do not approve refunds or create payments."""

import os
import uuid
from collections.abc import Callable

from celery import Celery
from kombu.exceptions import OperationalError

from app.config import get_settings
from app.db.session import SessionLocal
from app.services.payments import HttpPaymentProvider
from app.services.reconciliation import (
    ReconcileResult,
    open_reconciliation_ids,
    reconcile_operation,
)
from app.services.recovery import retry_failed_ticket_updates
from app.workflows.runner import execute_workflow

celery_app = Celery(
    "support_resolution_agent",
    broker=get_settings().redis_url,
)
celery_app.conf.task_ignore_result = True
celery_app.conf.timezone = "UTC"

BROKER_ERRORS = (ConnectionError, OSError, OperationalError)
DEFAULT_DISCOVER_INTERVAL_SECONDS = 60.0


def discover_interval_seconds() -> float:
    """How often Beat publishes payments.discover. The default is 60 seconds."""
    raw = os.environ.get("RECONCILE_DISCOVER_SECONDS", "")
    if raw == "":
        return DEFAULT_DISCOVER_INTERVAL_SECONDS
    seconds = float(raw)
    if seconds <= 0:
        raise RuntimeError("RECONCILE_DISCOVER_SECONDS must be positive")
    return seconds


def reconciliation_queue() -> str:
    """Queue for discovery and lookup. Tests can isolate it without changing the ledger."""
    queue = os.environ.get("RECONCILE_QUEUE", "celery")
    if queue == "":
        return "celery"
    return queue


celery_app.conf.beat_schedule = {
    "discover-open-refunds": {
        "task": "payments.discover",
        "schedule": discover_interval_seconds(),
        "options": {"queue": reconciliation_queue()},
    }
}


@celery_app.task(name="workflows.run")
def run_workflow(run_id: str) -> str:
    """Execute the run id from the queue. The id is the workflow_runs primary key."""
    return execute_workflow(uuid.UUID(run_id))


def enqueue_workflow(run_id: uuid.UUID) -> None:
    """Publish one run id. The broker call happens after the run row commits."""
    run_workflow.delay(str(run_id))


@celery_app.task(name="payments.reconcile", bind=True, max_retries=8)
def reconcile_payment(self, operation_id: str) -> str:
    """Look up one stored operation. The task argument is not an amount or an approval."""
    provider = HttpPaymentProvider(get_settings().payment_provider_url)
    try:
        result = reconcile_operation(SessionLocal, uuid.UUID(operation_id), provider)
    finally:
        provider.close()
    return complete_reconcile_attempt(self.retry, result)


def enqueue_reconciliation(operation_id: uuid.UUID) -> None:
    """Publish one operation id. The worker reads the row before it calls the provider."""
    reconcile_payment.apply_async(args=[str(operation_id)], queue=reconciliation_queue())


def enqueue_open_reconciliations() -> int:
    """Queue every unknown, submitted, or processing operation. One failed publish is skipped."""
    published = 0
    for operation_id in open_reconciliation_ids(SessionLocal):
        published += _publish_reconciliation(operation_id)
    return published


def _publish_reconciliation(operation_id: uuid.UUID) -> int:
    try:
        enqueue_reconciliation(operation_id)
    except BROKER_ERRORS:
        return 0
    return 1


@celery_app.task(name="payments.discover")
def discover_payments() -> int:
    """Retry missed ticket writes and publish open refunds. This task does not POST."""
    retry_failed_ticket_updates(SessionLocal)
    return enqueue_open_reconciliations()


def complete_reconcile_attempt(retry: Callable[..., object], result: ReconcileResult) -> str:
    """Schedule another lookup when the attempt says so. It does not change the ledger."""
    if result.retry_after_seconds is None:
        return result.outcome
    retry(countdown=result.retry_after_seconds)
    return result.outcome
