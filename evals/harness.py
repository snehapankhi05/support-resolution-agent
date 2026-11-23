"""Phase 9 ledger harness. It never calls the payment execution function.

Run with: uv run python -m evals.harness
The database name must be support_eval. support and support_test are refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import NullPool

from evals.cases import CASES, EXECUTION_ORDER, HOSTILE_POLICY_TEXT

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = ROOT / "evals" / "expected.json"
REPORT_PATH = ROOT / "evals" / "reports" / "phase9.json"
EVAL_DATABASE = "support_eval"
MAINTENANCE_DATABASE = "postgres"
FORBIDDEN_MAINTENANCE = frozenset({"support", "support_test", "support_eval"})
OPEN_STATUSES = ("reserved", "unknown", "submitted", "processing")
JWT_SECRET = "eval-harness-jwt-secret-32-characters"
EVAL_PASSWORD = "eval-password"
DEFAULT_URL = "postgresql+psycopg://app:app@localhost:5433/support"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
EVAL_REDIS_DATABASE = "/15"


class CountingProvider:
    """In-process provider for cases that do not need the HTTP mock."""

    def __init__(self) -> None:
        self.attempts = 0
        self.records: dict[str, tuple[int, str]] = {}
        self._lock = threading.Lock()

    def lookup_refund(self, *, idempotency_key: str):
        from app.services.payments import ProviderRecord

        with self._lock:
            found = self.records.get(idempotency_key)
        if found is None:
            return None
        amount, order_id = found
        return ProviderRecord(
            status="succeeded",
            amount_minor=amount,
            order_id=order_id,
            provider_reference="eval_local",
        )

    def create_refund(self, *, idempotency_key: str, amount_minor: int, order_id: str):
        from app.services.payments import ProviderResult

        with self._lock:
            if idempotency_key in self.records:
                return ProviderResult(status="succeeded", provider_reference="eval_local")
            self.records[idempotency_key] = (amount_minor, order_id)
            self.attempts += 1
        return ProviderResult(status="succeeded", provider_reference="eval_local")

    @property
    def record_count(self) -> int:
        return len(self.records)


def main() -> int:
    _refuse_payment_call()
    eval_url = _prepare_database()
    os.environ["DATABASE_URL"] = eval_url
    os.environ["JWT_SECRET"] = JWT_SECRET
    _isolate_redis()
    _import_application()
    _assert_database_name()
    _upgrade_and_assert_head()
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    by_number = {case["number"]: case for case in CASES}
    results: dict[str, dict] = {}
    failures: list[dict] = []
    checkpoints_checked = False
    for number in EXECUTION_ORDER:
        case = by_number[number]
        if number in {24, 25}:
            _assert_only_open_row(None)
        actual = _run_case(case)
        results[str(number)] = actual
        failures.extend(_compare(str(number), expected[str(number)], actual, expected))
        if not checkpoints_checked and actual.get("workflow_result") is not None:
            _assert_checkpoint_tables()
            checkpoints_checked = True
    metrics = _metrics(expected, results)
    if metrics["unauthorized_refunds"] != 0 or metrics["duplicate_payments"] != 0:
        failures.append({"metric": metrics})
    report = {
        "expected_sha256": hashlib.sha256(EXPECTED_PATH.read_bytes()).hexdigest(),
        "database": EVAL_DATABASE,
        "results": results,
        "metrics": metrics,
        "failures": failures,
        "passed": not failures,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"unauthorized_refunds={metrics['unauthorized_refunds']}")
    print(f"duplicate_payments={metrics['duplicate_payments']}")
    print(f"passed={report['passed']} failures={len(failures)}")
    return 0 if report["passed"] else 1


def _refuse_payment_call() -> None:
    # Build the needle at runtime so this check does not match itself.
    source = Path(__file__).read_text(encoding="utf-8")
    if "execute_approved_refund" + "(" in source:
        raise SystemExit("harness must not call the payment execution function")


def _prepare_database() -> str:
    base = make_url(os.environ.get("DATABASE_URL", DEFAULT_URL))
    maintenance = base.set(database=MAINTENANCE_DATABASE)
    engine = create_engine(maintenance, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            current = connection.execute(text("SELECT current_database()")).scalar_one()
            if current in FORBIDDEN_MAINTENANCE:
                raise SystemExit(
                    f"Refusing to create {EVAL_DATABASE} from database {current!r}."
                )
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": EVAL_DATABASE},
            ).scalar()
            if exists is None:
                connection.execute(text(f"CREATE DATABASE {EVAL_DATABASE}"))
    finally:
        engine.dispose()
    return base.set(database=EVAL_DATABASE).render_as_string(hide_password=False)


def _isolate_redis() -> None:
    """Use Redis database 15 before Celery is constructed. Database 0 stays untouched."""
    raw = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
    os.environ["REDIS_URL"] = urlunsplit(urlsplit(raw)._replace(path=EVAL_REDIS_DATABASE))


def _import_application() -> None:
    import importlib

    from app.config import get_settings

    get_settings.cache_clear()
    # Side-effect imports: bind settings-backed engine / app modules for the harness.
    importlib.import_module("app.db.session")
    importlib.import_module("app.main")


def _assert_database_name() -> None:
    from app.config import get_settings

    name = make_url(get_settings().database_url).database
    if name != EVAL_DATABASE:
        raise SystemExit(f"Harness URL names {name!r}, not {EVAL_DATABASE!r}.")


def _upgrade_and_assert_head() -> None:
    from alembic import command
    from alembic.config import Config

    from app.db.session import SessionLocal

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    with SessionLocal() as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        current = session.execute(text("SELECT current_database()")).scalar_one()
    if current != EVAL_DATABASE or version != "0005":
        raise SystemExit(f"Migration check failed for database {current!r} at {version!r}.")


def _assert_checkpoint_tables() -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        names = set(
            session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'langgraph'"
                )
            ).scalars()
        )
    if "checkpoints" not in names:
        raise SystemExit("Checkpoint tables are not in schema langgraph.")


def _assert_only_open_row(allowed_order_id: uuid.UUID | None) -> None:
    from app.db.models import RefundOperation
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        rows = session.execute(
            select(RefundOperation.order_id, RefundOperation.status).where(
                RefundOperation.status.in_(OPEN_STATUSES)
            )
        ).all()
    unexpected = [row for row in rows if row.order_id != allowed_order_id]
    if allowed_order_id is None and rows:
        raise SystemExit(f"Open refunds exist before the process case: {len(rows)}")
    if unexpected:
        raise SystemExit("Another open refund is visible to discovery.")


def _run_case(case: dict) -> dict:
    provider_kind = case["provider"]
    if provider_kind in {"timeout_after_accept", "error_500", "mismatch", "kill_after", "kill_before"}:
        return _run_http_case(case)
    return _run_local_case(case)


def _run_local_case(case: dict) -> dict:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.workflows.runner import execute_workflow

    provider = CountingProvider()
    with TestClient(app) as client:
        seeded = _seed(case)
        if case.get("http_check") == "other_order":
            status = _other_customer_ready(client, seeded, f"/orders/{seeded['order_id']}")
            return _snapshot(client, seeded, provider, http_status=status)
        ticket_id = _post_ticket(client, seeded)
        if case.get("http_check") == "other_ticket":
            status = _other_customer_ready(client, seeded, f"/tickets/{ticket_id}")
            return _snapshot(client, seeded, provider, http_status=status, ticket_id=ticket_id)
        run_id = _post_run(client, seeded, ticket_id)
        first = execute_workflow(uuid.UUID(run_id), provider=provider)
        http_status = None
        if case["decision"] == "approve":
            _decide(client, seeded, run_id, "approve")
            if case.get("concurrent"):
                _concurrent(run_id, provider)
                second = "finished"
            else:
                second = execute_workflow(uuid.UUID(run_id), provider=provider)
                if case.get("replay"):
                    execute_workflow(uuid.UUID(run_id), provider=provider)
        elif case["decision"] == "reject":
            _decide(client, seeded, run_id, "reject")
            second = execute_workflow(uuid.UUID(run_id), provider=provider)
        elif case["decision"] == "customer_forbidden":
            http_status = _customer_decision(client, seeded, run_id)
            second = first
        elif case["decision"] == "extra_amount":
            http_status = _extra_amount(client, seeded, run_id)
            second = first
        else:
            second = first
        observed = first if case["decision"] == "resume_early" else second
        return _snapshot(
            client,
            seeded,
            provider,
            http_status=http_status,
            ticket_id=ticket_id,
            run_id=run_id,
            workflow_result=observed,
        )


def _run_http_case(case: dict) -> dict:
    import httpx
    import uvicorn
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    from app.workflows.runner import execute_workflow
    from mock_services.payments.app import create_app

    port = _free_port()
    store = Path(tempfile.mkdtemp(prefix="eval-provider-")) / "provider.sqlite"
    server = uvicorn.Server(
        uvicorn.Config(create_app(store), host="127.0.0.1", port=port, log_level="warning")
    )
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    threading.Thread(target=server.run, daemon=True).start()
    _wait_healthy(port)
    os.environ["PAYMENT_PROVIDER_URL"] = f"http://127.0.0.1:{port}"
    get_settings.cache_clear()
    processes: list[subprocess.Popen[str]] = []
    try:
        with TestClient(app) as client:
            seeded = _seed(case)
            ticket_id = _post_ticket(client, seeded)
            run_id = _post_run(client, seeded, ticket_id)
            assert execute_workflow(uuid.UUID(run_id)) == "paused"
            proposal_id = _proposal_id(run_id)
            after_beat = None
            if case["provider"] == "mismatch":
                _seed_wrong_provider_row(store, proposal_id, seeded)
            elif case["provider"] == "timeout_after_accept":
                _control(port, "timeout_after_accept", 6)
            elif case["provider"] == "error_500":
                _control(port, "error_500", 0)
            elif case["provider"] == "kill_after":
                _control(port, "timeout_after_accept", 30)
            elif case["provider"] == "kill_before":
                _control(port, "delay", 30)
            _decide(client, seeded, run_id, "approve")
            workflow_result = "finished"
            second_run_create_attempts = None
            if case["provider"] in {"kill_after", "kill_before"}:
                after_beat = _kill_and_recover(case, port, store, run_id, processes)
            else:
                execute_workflow(uuid.UUID(run_id))
                if case.get("second_workflow"):
                    before = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=5).json()
                    workflow_result = execute_workflow(uuid.UUID(run_id))
                    after = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=5).json()
                    second_run_create_attempts = after["attempts"] - before["attempts"]
            stats = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=5).json()
            provider = _Stats(stats["attempts"], stats["operations"])
            return _snapshot(
                client,
                seeded,
                provider,
                ticket_id=ticket_id,
                run_id=run_id,
                workflow_result=workflow_result,
                after_beat=after_beat,
                second_run_create_attempts=second_run_create_attempts,
            )
    finally:
        server.should_exit = True
        for process in processes:
            _stop(process)


class _Stats:
    def __init__(self, attempts: int, record_count: int) -> None:
        self.attempts = attempts
        self.record_count = record_count


def _kill_and_recover(
    case: dict,
    port: int,
    store: Path,
    run_id: str,
    processes: list[subprocess.Popen[str]],
) -> dict:
    from app.config import get_settings
    from app.worker import run_workflow

    _require_redis()
    pay_queue = f"eval-pay-{case['number']}-{uuid.uuid4().hex}"
    env = os.environ.copy()
    env["DATABASE_URL"] = get_settings().database_url
    env["JWT_SECRET"] = JWT_SECRET
    env["PAYMENT_PROVIDER_URL"] = f"http://127.0.0.1:{port}"
    env["RECONCILE_QUEUE"] = pay_queue
    env["REDIS_URL"] = os.environ["REDIS_URL"]
    if make_url(env["DATABASE_URL"]).database != EVAL_DATABASE:
        raise SystemExit("Worker environment is not support_eval.")
    if urlsplit(env["REDIS_URL"]).path != EVAL_REDIS_DATABASE:
        raise SystemExit("Worker Redis is not database 15.")
    schedule_dir = Path(tempfile.mkdtemp(prefix="eval-beat-"))
    payer = _worker(pay_queue, env, schedule_dir / "payer.log")
    processes.append(payer)
    _wait_log(payer, schedule_dir / "payer.log", " ready", 45)
    run_workflow.apply_async(args=[run_id], queue=pay_queue)
    _wait_until_reserved(run_id, port, kill_when_stored=case["provider"] == "kill_after")
    _stop(payer)
    operation = _operation_for_run(run_id)
    if operation is None or operation["status"] != "reserved":
        raise SystemExit(f"Case {case['number']} was not reserved when the worker stopped.")
    _assert_only_open_row(uuid.UUID(operation["order_id"]))
    adopt_queue = f"eval-adopt-{case['number']}-{uuid.uuid4().hex}"
    env["RECONCILE_QUEUE"] = adopt_queue
    env["RECONCILE_DISCOVER_SECONDS"] = "2"
    worker = _worker(adopt_queue, env, schedule_dir / "worker.log")
    beat = _beat(env, schedule_dir)
    processes.extend([worker, beat])
    _wait_log(worker, schedule_dir / "worker.log", " ready", 45)
    _wait_log(beat, schedule_dir / "beat.log", "Sending due task discover-open-refunds", 25)
    if case["provider"] == "kill_before":
        after_beat = _wait_for_confirmed_absence(run_id, port, 60)
        if not _absence_confirmed(run_id, after_beat):
            return after_beat
        _control(port, "succeed", 0)
        _stop(worker)
        _stop(beat)
        later = f"eval-later-{uuid.uuid4().hex}"
        env["RECONCILE_QUEUE"] = later
        later_worker = _worker(later, env, schedule_dir / "later.log")
        processes.append(later_worker)
        _wait_log(later_worker, schedule_dir / "later.log", " ready", 45)
        run_workflow.apply_async(args=[run_id], queue=later)
        _wait_status(run_id, "succeeded", 60)
        return after_beat
    _wait_status(run_id, "succeeded", 180)
    return {}


def _wait_for_confirmed_absence(run_id: str, port: int, seconds: float) -> dict:
    """Wait until a 404 lookup is audited, or until the deadline. This does not POST."""
    import httpx

    deadline = time.time() + seconds
    after_beat = {"ledger_status": None, "provider_records": None, "create_attempts": None}
    while True:
        interim = _operation_for_run(run_id)
        stats = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=5).json()
        after_beat = {
            "ledger_status": None if interim is None else interim["status"],
            "provider_records": stats["operations"],
            "create_attempts": stats["attempts"],
        }
        confirmed = _absence_confirmed(run_id, after_beat)
        unsafe = (
            after_beat["ledger_status"] != "reserved"
            or after_beat["provider_records"] != 0
            or after_beat["create_attempts"] != 0
        )
        if confirmed or unsafe or time.time() >= deadline:
            return after_beat
        time.sleep(0.2)


def _absence_confirmed(run_id: str, after_beat: dict) -> bool:
    return (
        after_beat["ledger_status"] == "reserved"
        and after_beat["provider_records"] == 0
        and after_beat["create_attempts"] == 0
        and _absence_audits(run_id) >= 1
    )


def _absence_audits(run_id: str) -> int:
    from app.db.models import AuditEvent
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        count = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.run_id == uuid.UUID(run_id),
                AuditEvent.event_type == "refund_reconcile_absent",
            )
        )
    return int(count or 0)


def _operation_for_run(run_id: str) -> dict | None:
    from app.db.models import ActionProposal, RefundOperation
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        row = session.execute(
            select(
                RefundOperation.status,
                RefundOperation.order_id,
                RefundOperation.provider_reference,
            )
            .join(ActionProposal, ActionProposal.id == RefundOperation.proposal_id)
            .where(ActionProposal.run_id == uuid.UUID(run_id))
        ).first()
    if row is None:
        return None
    return {
        "status": row.status,
        "order_id": str(row.order_id),
        "provider_reference": row.provider_reference,
    }


def _wait_until_reserved(run_id: str, port: int, *, kill_when_stored: bool) -> None:
    import httpx

    deadline = time.time() + 40
    while time.time() < deadline:
        operation = _operation_for_run(run_id)
        stats = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=2).json()
        if kill_when_stored and stats["operations"] == 1 and operation and operation["status"] == "reserved":
            return
        if not kill_when_stored and operation and operation["status"] == "reserved" and stats["operations"] == 0:
            return
        time.sleep(0.05)
    raise SystemExit("Timed out waiting to stop the payment worker.")


def _wait_status(run_id: str, status: str, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        operation = _operation_for_run(run_id)
        if operation and operation["status"] == status:
            return
        time.sleep(0.25)
    raise SystemExit(f"Refund did not become {status}.")


def _seed(case: dict) -> dict:
    from app.auth.passwords import hash_password
    from app.db.models import Customer, LoginAccount, Order, OrderItem, Return, StaffUser
    from app.db.session import SessionLocal

    _replace_policy(case)
    marker = uuid.uuid4().hex
    email = f"eval-case-{case['number']}-{marker}@eval.test"
    now = datetime.now(UTC)
    delivered = None
    if case["delivered_days_ago"] is not None:
        delivered = now - timedelta(days=case["delivered_days_ago"])
    with SessionLocal.begin() as session:
        reviewer = session.scalar(select(StaffUser).where(StaffUser.name == "Eval Reviewer"))
        if reviewer is None:
            reviewer = StaffUser(
                external_auth_subject=f"eval|reviewer-{marker}",
                name="Eval Reviewer",
                role="reviewer",
            )
            session.add(reviewer)
            session.flush()
            session.add(
                LoginAccount(
                    email="eval-reviewer@eval.test",
                    password_hash=hash_password(EVAL_PASSWORD),
                    staff_user_id=reviewer.id,
                )
            )
        customer = Customer(
            external_auth_subject=f"eval|customer-{marker}",
            name=f"Case {case['number']}",
            email=email,
        )
        other = Customer(
            external_auth_subject=f"eval|other-{marker}",
            name="Other Customer",
            email=f"other-{marker}@eval.test",
        )
        session.add_all([customer, other])
        session.flush()
        session.add_all(
            [
                LoginAccount(
                    email=email,
                    password_hash=hash_password(EVAL_PASSWORD),
                    customer_id=customer.id,
                ),
                LoginAccount(
                    email=f"other-{marker}@eval.test",
                    password_hash=hash_password(EVAL_PASSWORD),
                    customer_id=other.id,
                ),
            ]
        )
        order = Order(
            customer_id=customer.id,
            status=case["order_status"],
            paid_amount_minor=case["amount_minor"],
            refund_committed_minor=case["amount_minor"] if case.get("preseed_reserved") else 0,
            delivered_at=delivered,
        )
        session.add(order)
        session.flush()
        session.add(
            OrderItem(
                order_id=order.id,
                product_id="sku",
                product_name="Item",
                quantity=1,
                paid_amount_minor=case["amount_minor"],
            )
        )
        _add_return(session, Return, order.id, case["return_state"], now)
        if case.get("preseed_reserved"):
            _preseed_reserved(session, customer.id, order.id, case["amount_minor"])
        ids = {
            "email": email,
            "other_email": f"other-{marker}@eval.test",
            "order_id": str(order.id),
            "customer_id": str(customer.id),
            "category": case["category"],
            "summary": case.get("summary") or "Please look at this order.",
            "preseed_reserved": bool(case.get("preseed_reserved")),
        }
    return ids


def _add_return(session, return_model, order_id, state: str, now: datetime) -> None:
    if state == "none":
        return
    if state == "in_transit":
        session.add(
            return_model(
                order_id=order_id,
                status="in_transit",
                inspection_status="pending",
                created_at=now,
            )
        )
        return
    if state == "inspection_failed":
        session.add(
            return_model(
                order_id=order_id,
                status="received",
                inspection_status="failed",
                created_at=now - timedelta(days=1),
            )
        )
        return
    session.add(
        return_model(
            order_id=order_id,
            status="received",
            inspection_status="passed",
            created_at=now - timedelta(days=1),
        )
    )


def _preseed_reserved(session, customer_id, order_id, amount: int) -> None:
    from app.db.models import ActionProposal, RefundOperation, Ticket, WorkflowRun

    ticket = Ticket(
        customer_id=customer_id,
        order_id=order_id,
        category="refund_request",
        status="open",
        summary="Earlier reserved refund",
    )
    session.add(ticket)
    session.flush()
    run_id = uuid.uuid4()
    session.add(
        WorkflowRun(
            id=run_id,
            ticket_id=ticket.id,
            thread_id=str(run_id),
            status="completed",
        )
    )
    session.flush()
    proposal_id = uuid.uuid4()
    session.add(
        ActionProposal(
            id=proposal_id,
            run_id=run_id,
            action_type="request_refund_approval",
            payload={"amount_minor": amount},
            payload_hash="eval-preseed",
            status="approved",
        )
    )
    session.flush()
    session.add(
        RefundOperation(
            order_id=order_id,
            proposal_id=proposal_id,
            amount_minor=amount,
            idempotency_key=f"eval-preseed-{proposal_id}",
            status="reserved",
        )
    )


def _replace_policy(case: dict) -> None:
    from app.db.models import PolicyChunk, PolicyDocument
    from app.db.session import SessionLocal

    with SessionLocal.begin() as session:
        session.execute(text("DELETE FROM policy_chunks"))
        session.execute(text("DELETE FROM policy_documents"))
        policy = case["policy"]
        if policy == "none":
            return
        document = PolicyDocument(
            policy_key=f"eval-case-{case['number']}",
            version=1,
            effective_from=date.today(),
            source_uri=f"memory://eval-case-{case['number']}",
        )
        session.add(document)
        session.flush()
        if policy == "conflict":
            rows = [
                ("allow", "Refunds that pass inspection are allowed.", "allow"),
                ("deny", "A conflicting refund rule denies payment.", "deny"),
            ]
        elif policy == "allow_hostile_text":
            rows = [("decision", HOSTILE_POLICY_TEXT, "allow")]
        else:
            rows = [("decision", "Eligible refunds require approval.", "allow")]
        for section, content, effect in rows:
            session.add(
                PolicyChunk(
                    document_id=document.id,
                    section_ref=section,
                    content=content,
                    chunk_metadata={"applies_to": "refund", "effect": effect},
                )
            )


def _token(client, email: str) -> str:
    response = client.post("/auth/token", json={"email": email, "password": EVAL_PASSWORD})
    if response.status_code != 200:
        raise SystemExit(f"Login failed for {email}: {response.status_code}")
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post_ticket(client, seeded: dict) -> str:
    token = _token(client, seeded["email"])
    response = client.post(
        "/tickets",
        headers=_auth(token),
        json={
            "order_id": seeded["order_id"],
            "category": seeded["category"],
            "summary": seeded.get("summary") or "Please look at this order.",
        },
    )
    if response.status_code != 201:
        raise SystemExit(f"Ticket create failed: {response.status_code} {response.text}")
    seeded["customer_token"] = token
    return response.json()["id"]


def _post_run(client, seeded: dict, ticket_id: str) -> str:
    response = client.post(
        "/runs",
        headers=_auth(seeded["customer_token"]),
        json={"ticket_id": ticket_id},
    )
    if response.status_code not in {200, 201}:
        raise SystemExit(f"Run create failed: {response.status_code} {response.text}")
    return response.json()["run_id"]


def _decide(client, seeded: dict, run_id: str, decision: str) -> None:
    proposal_id = _proposal_id(run_id)
    token = _token(client, "eval-reviewer@eval.test")
    response = client.post(
        f"/proposals/{proposal_id}/decision",
        headers=_auth(token),
        json={"decision": decision},
    )
    if response.status_code != 200:
        raise SystemExit(f"Decision failed: {response.status_code} {response.text}")


def _customer_decision(client, seeded: dict, run_id: str) -> int:
    proposal_id = _proposal_id(run_id)
    response = client.post(
        f"/proposals/{proposal_id}/decision",
        headers=_auth(seeded["customer_token"]),
        json={"decision": "approve"},
    )
    return response.status_code


def _extra_amount(client, seeded: dict, run_id: str) -> int:
    proposal_id = _proposal_id(run_id)
    token = _token(client, "eval-reviewer@eval.test")
    response = client.post(
        f"/proposals/{proposal_id}/decision",
        headers=_auth(token),
        json={"decision": "approve", "amount_minor": 1},
    )
    return response.status_code


def _proposal_id(run_id: str) -> str:
    from app.db.models import ActionProposal
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        proposal_id = session.scalar(
            select(ActionProposal.id).where(ActionProposal.run_id == uuid.UUID(run_id))
        )
    if proposal_id is None:
        raise SystemExit("The workflow did not store a proposal.")
    return str(proposal_id)


def _concurrent(run_id: str, provider: CountingProvider) -> None:
    from app.workflows.runner import execute_workflow

    errors: list[BaseException] = []

    def _resume() -> None:
        try:
            execute_workflow(uuid.UUID(run_id), provider=provider)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_resume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise SystemExit(f"Concurrent resume failed: {errors[0]!r}")


def _snapshot(
    client,
    seeded: dict,
    provider,
    *,
    http_status: int | None = None,
    ticket_id: str | None = None,
    run_id: str | None = None,
    workflow_result: str | None = None,
    after_beat: dict | None = None,
    second_run_create_attempts: int | None = None,
) -> dict:
    from app.db.models import (
        ActionProposal,
        Approval,
        AuditEvent,
        Order,
        RefundOperation,
        WorkflowRun,
    )
    from app.db.session import SessionLocal

    order_id = uuid.UUID(seeded["order_id"])
    with SessionLocal() as session:
        order = session.get(Order, order_id)
        refunds = session.scalars(
            select(RefundOperation)
            .where(RefundOperation.order_id == order_id)
            .order_by(RefundOperation.created_at)
        ).all()
        fresh = [row for row in refunds if not row.idempotency_key.startswith("eval-preseed-")]
        shown = fresh[-1] if fresh else (refunds[-1] if refunds else None)
        proposal_status = None
        approval_status = None
        run_status = None
        conflict_audits = 0
        if run_id is not None:
            run = session.get(WorkflowRun, uuid.UUID(run_id))
            run_status = None if run is None else run.status
            proposal = session.scalar(
                select(ActionProposal).where(ActionProposal.run_id == uuid.UUID(run_id))
            )
            proposal_status = None if proposal is None else proposal.status
            if proposal is not None:
                approval = session.scalar(
                    select(Approval).where(Approval.proposal_id == proposal.id)
                )
                approval_status = None if approval is None else approval.status
            conflict_audits = session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.run_id == uuid.UUID(run_id),
                    AuditEvent.event_type == "refund_payload_conflict",
                )
            )
        committed = 0 if order is None else order.refund_committed_minor
        ledger_status = None if shown is None else shown.status
        reference = None if shown is None else shown.provider_reference
        if fresh:
            ledger_status = fresh[-1].status
            reference = fresh[-1].provider_reference
        elif not seeded.get("preseed_reserved"):
            ledger_status = None
            reference = None
        refund_rows = len(refunds)
    message = None
    if ticket_id is not None and seeded.get("customer_token"):
        response = client.get(f"/tickets/{ticket_id}", headers=_auth(seeded["customer_token"]))
        if response.status_code == 200:
            message = response.json()["message"]
    return {
        "ledger_status": ledger_status,
        "provider_reference": reference,
        "refund_rows": refund_rows,
        "committed_paise": committed,
        "provider_records": provider.record_count,
        "create_attempts": provider.attempts,
        "proposal_status": proposal_status,
        "approval_status": approval_status,
        "run_status": run_status,
        "message": message,
        "http_status": http_status,
        "workflow_result": workflow_result,
        "second_run_create_attempts": second_run_create_attempts,
        "conflict_audits": int(conflict_audits or 0),
        "after_beat": after_beat or {},
        "ticket_status": _ticket_status(ticket_id),
    }


def _ticket_status(ticket_id: str | None) -> str | None:
    from app.db.models import Ticket
    from app.db.session import SessionLocal

    if ticket_id is None:
        return None
    with SessionLocal() as session:
        ticket = session.get(Ticket, uuid.UUID(ticket_id))
    return None if ticket is None else ticket.status


def _compare(number: str, expected: dict, actual: dict, all_expected: dict) -> list[dict]:
    failures = []
    if (
        "allowed_run_statuses" in expected
        and actual.get("run_status") not in expected["allowed_run_statuses"]
    ):
        failures.append(
            {
                "case": number,
                "field": "run_status",
                "expected": expected["allowed_run_statuses"],
                "actual": actual.get("run_status"),
            }
        )
    if (
        "forbidden_amount_minor" in expected
        and actual.get("committed_paise") == expected["forbidden_amount_minor"]
    ):
        failures.append(
            {
                "case": number,
                "field": "committed_paise",
                "expected": f"not {expected['forbidden_amount_minor']}",
                "actual": actual.get("committed_paise"),
            }
        )
    if expected.get("clean_case"):
        clean = all_expected[str(expected["clean_case"])]
        for field in ("ledger_status", "provider_records", "create_attempts"):
            if actual.get(field) != clean[field]:
                failures.append(
                    {
                        "case": number,
                        "field": field,
                        "expected": clean[field],
                        "actual": actual.get(field),
                    }
                )
    skip = {
        "name",
        "clean_case",
        "forbidden_amount_minor",
        "seeded_refund_rows",
        "allowed_run_statuses",
    }
    for field, want in expected.items():
        if field in skip:
            continue
        if actual.get(field) != want:
            failures.append(
                {"case": number, "field": field, "expected": want, "actual": actual.get(field)}
            )
    return failures


def _metrics(expected: dict, results: dict) -> dict:
    unauthorized = 0
    duplicates = 0
    for number, want in expected.items():
        actual = results.get(number)
        if actual is None:
            continue
        unexpected_rows = max(0, actual["refund_rows"] - want["refund_rows"])
        unexpected_attempts = max(0, actual["create_attempts"] - want["create_attempts"])
        unexpected_records = max(0, actual["provider_records"] - want["provider_records"])
        unauthorized += unexpected_rows
        if want["create_attempts"] == 0:
            unauthorized += unexpected_attempts
        duplicates += unexpected_attempts + unexpected_records
    return {"unauthorized_refunds": unauthorized, "duplicate_payments": duplicates}


def _seed_wrong_provider_row(path: Path, proposal_id: str, seeded: dict) -> None:
    from evals.cases import CASES

    amount = next(case["amount_minor"] for case in CASES if case["number"] == 23)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO provider_operations (
                idempotency_key, amount_minor, order_id, status, provider_reference
            ) VALUES (?, ?, ?, 'succeeded', 'eval_wrong')
            """,
            (f"refund-{proposal_id}", amount + 1, seeded["order_id"]),
        )
        connection.commit()
    finally:
        connection.close()


def _control(port: int, mode: str, delay: float) -> None:
    import httpx

    httpx.post(
        f"http://127.0.0.1:{port}/control",
        json={"mode": mode, "delay_seconds": delay},
        timeout=5,
    ).raise_for_status()


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_healthy(port: int) -> None:
    import httpx

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise SystemExit("Mock provider did not start.")


def _require_redis() -> None:
    import redis

    from app.config import get_settings

    client = redis.Redis.from_url(
        get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    try:
        client.ping()
    except redis.RedisError as exc:
        raise SystemExit(f"Redis is not reachable: {exc}") from exc


def _worker(queue: str, env: dict[str, str], log_path: Path) -> subprocess.Popen[str]:
    return _process(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.worker",
            "worker",
            "--pool=solo",
            "-Q",
            queue,
            "--concurrency=1",
            "--loglevel=INFO",
        ],
        env,
        log_path,
    )


def _beat(env: dict[str, str], schedule_dir: Path) -> subprocess.Popen[str]:
    return _process(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.worker",
            "beat",
            "--loglevel=INFO",
            "--schedule",
            str(schedule_dir / "schedule"),
            "--pidfile",
            str(schedule_dir / "beat.pid"),
        ],
        env,
        schedule_dir / "beat.log",
    )


def _process(args: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen[str]:
    child = env.copy()
    child["PYTHONUNBUFFERED"] = "1"
    log = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        args,
        cwd=ROOT,
        env=child,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_log(process: subprocess.Popen[str], log_path: Path, needle: str, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        body = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        if needle.lower() in body.lower():
            return
        if process.poll() is not None:
            raise SystemExit(f"Process exited before {needle}: {body[-2000:]}")
        time.sleep(0.2)
    body = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    raise SystemExit(f"Did not see {needle}: {body[-2000:]}")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _other_customer_ready(client, seeded: dict, path: str) -> int:
    token = _token(client, seeded["other_email"])
    return client.get(path, headers=_auth(token)).status_code


if __name__ == "__main__":
    raise SystemExit(main())
