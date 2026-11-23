"""A real Celery worker on Redis adopts one stored refund and does not post another."""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
import redis
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.engine.url import make_url

from app.config import get_settings
from app.db.models import ActionProposal, Order, RefundOperation, WorkflowRun
from app.db.session import SessionLocal
from app.worker import reconcile_payment, run_workflow
from app.workflows.runner import execute_workflow
from mock_services.payments.app import create_app
from tests.db_safety import TEST_DATABASE
from tests.integration.test_reconciliation import _operation
from tests.integration.test_refund_execution import PAID, RecordingProvider, _factory
from tests.integration.test_support_graph import _case
from tests.integration.test_worker import _decide

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_a_celery_worker_adopts_a_provider_record_without_reposting(engine):
    database = make_url(get_settings().database_url).database
    assert database == TEST_DATABASE
    client = redis.Redis.from_url(
        get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    try:
        client.ping()
    except redis.RedisError as exc:
        pytest.fail(f"Redis is not reachable: {exc}")

    bind, sessions = _factory()
    with sessions.begin() as session:
        operation_id, order_id = _operation(session, "unknown")
    with SessionLocal() as session:
        key = session.get(RefundOperation, operation_id).idempotency_key
    port = _free_port()
    store = ROOT / "mock_services" / "payments" / "data" / f"phase7-{uuid.uuid4().hex}.sqlite"
    app = create_app(store)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        pytest.fail("mock provider did not start")
    seeded = httpx.post(
        f"http://127.0.0.1:{port}/refunds",
        json={"idempotency_key": key, "amount_minor": PAID, "order_id": str(order_id)},
        timeout=5,
    )
    assert seeded.status_code == 200
    queue = f"phase7-{uuid.uuid4().hex}"
    env = os.environ.copy()
    env["DATABASE_URL"] = get_settings().database_url
    env["PAYMENT_PROVIDER_URL"] = f"http://127.0.0.1:{port}"
    worker = subprocess.Popen(
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
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []

    def _read() -> None:
        if worker.stdout is None:
            return
        lines.extend(worker.stdout)

    threading.Thread(target=_read, daemon=True).start()
    try:
        ready = time.time() + 45
        while time.time() < ready and "ready" not in "".join(lines).lower():
            if worker.poll() is not None:
                pytest.fail("celery worker exited early: " + "".join(lines)[-2000:])
            time.sleep(0.2)
        if "ready" not in "".join(lines).lower():
            pytest.fail("celery worker did not become ready: " + "".join(lines)[-2000:])
        reconcile_payment.apply_async(args=[str(operation_id)], queue=queue)
        status = "unknown"
        seen = time.time() + 30
        while time.time() < seen:
            with SessionLocal() as session:
                status = session.scalar(
                    select(RefundOperation.status).where(RefundOperation.id == operation_id)
                )
            if status == "succeeded":
                break
            time.sleep(0.25)
        stats = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=5).json()
        assert status == "succeeded"
        assert stats["attempts"] == 1
        assert stats["operations"] == 1
    finally:
        server.should_exit = True
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
        bind.dispose()
        try:
            store.unlink(missing_ok=True)
        except OSError:
            pass


def test_beat_discovers_an_open_refund_without_a_manual_publish(engine):
    database = make_url(get_settings().database_url).database
    assert database == TEST_DATABASE
    client = redis.Redis.from_url(
        get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    try:
        client.ping()
    except redis.RedisError as exc:
        pytest.fail(f"Redis is not reachable: {exc}")

    bind, sessions = _factory()
    with sessions.begin() as session:
        operation_id, order_id = _operation(session, "unknown")
    with SessionLocal() as session:
        key = session.get(RefundOperation, operation_id).idempotency_key
    port = _free_port()
    store = ROOT / "mock_services" / "payments" / "data" / f"phase7-beat-{uuid.uuid4().hex}.sqlite"
    app = create_app(store)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        pytest.fail("mock provider did not start")
    seeded = httpx.post(
        f"http://127.0.0.1:{port}/refunds",
        json={"idempotency_key": key, "amount_minor": PAID, "order_id": str(order_id)},
        timeout=5,
    )
    assert seeded.status_code == 200
    queue = f"phase7-beat-{uuid.uuid4().hex}"
    env = os.environ.copy()
    env["DATABASE_URL"] = get_settings().database_url
    env["PAYMENT_PROVIDER_URL"] = f"http://127.0.0.1:{port}"
    env["RECONCILE_QUEUE"] = queue
    env["RECONCILE_DISCOVER_SECONDS"] = "2"
    schedule_dir = Path(tempfile.mkdtemp(prefix="beat-"))
    worker_log = schedule_dir / "worker.log"
    beat_log = schedule_dir / "beat.log"
    worker = _process(
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
        worker_log,
    )
    beat = _process(
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
        beat_log,
    )
    try:
        _wait_for(worker, worker_log, " ready", 45, "celery worker")
        _wait_for(beat, beat_log, "Sending due task discover-open-refunds", 25, "celery beat")
        status = "unknown"
        seen = time.time() + 180
        while time.time() < seen:
            with SessionLocal() as session:
                status = session.scalar(
                    select(RefundOperation.status).where(RefundOperation.id == operation_id)
                )
            if status == "succeeded":
                break
            time.sleep(0.25)
        stats = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=5).json()
        assert status == "succeeded"
        assert stats["attempts"] == 1
        assert stats["operations"] == 1
    finally:
        server.should_exit = True
        for process in (beat, worker):
            _stop(process)
        bind.dispose()
        try:
            store.unlink(missing_ok=True)
        except OSError:
            pass


def test_celery_workflows_run_finishes_an_order_status_thread(engine):
    database = make_url(get_settings().database_url).database
    assert database == TEST_DATABASE
    _require_redis()
    bind, sessions = _factory()
    with sessions.begin() as session:
        _ticket_id, run_id, _thread_id = _case(
            session, category="order_status", with_policy=False
        )
    queue = f"phase7-run-{uuid.uuid4().hex}"
    env = os.environ.copy()
    env["DATABASE_URL"] = get_settings().database_url
    env["RECONCILE_QUEUE"] = queue
    assert make_url(env["DATABASE_URL"]).database == TEST_DATABASE
    schedule_dir = Path(tempfile.mkdtemp(prefix="run-"))
    worker_log = schedule_dir / "worker.log"
    worker = _worker(queue, env, worker_log)
    try:
        _wait_for(worker, worker_log, " ready", 45, "celery worker")
        run_workflow.apply_async(args=[str(run_id)], queue=queue)
        status = "running"
        seen = time.time() + 45
        while time.time() < seen:
            with SessionLocal() as session:
                status = session.scalar(select(WorkflowRun.status).where(WorkflowRun.id == run_id))
                proposals = session.scalar(
                    select(func.count())
                    .select_from(ActionProposal)
                    .where(ActionProposal.run_id == run_id)
                )
            if status == "completed":
                break
            time.sleep(0.25)
        _wait_for(worker, worker_log, "Task workflows.run", 5, "workflows.run")
        assert status == "completed"
        assert proposals == 0
        assert make_url(get_settings().database_url).database == TEST_DATABASE
    finally:
        _stop(worker)
        bind.dispose()


def test_a_killed_worker_is_adopted_by_scheduled_discovery(engine):
    database = make_url(get_settings().database_url).database
    assert database == TEST_DATABASE
    _require_redis()
    bind, sessions = _factory()
    with sessions.begin() as session:
        _ticket_id, run_id, _thread_id = _case(session, category="refund_request")
    assert execute_workflow(run_id, provider=RecordingProvider()) == "paused"
    with SessionLocal() as session:
        proposal_id = session.scalar(
            select(ActionProposal.id).where(ActionProposal.run_id == run_id)
        )
    _decide(sessions, proposal_id, "approve")
    port = _free_port()
    store = ROOT / "mock_services" / "payments" / "data" / f"phase7-kill-{uuid.uuid4().hex}.sqlite"
    app = create_app(store)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    threading.Thread(target=server.run, daemon=True).start()
    _wait_until_healthy(port)
    httpx.post(
        f"http://127.0.0.1:{port}/control",
        json={"mode": "timeout_after_accept", "delay_seconds": 30},
        timeout=5,
    ).raise_for_status()
    pay_queue = f"phase7-kill-{uuid.uuid4().hex}"
    adopt_queue = f"phase7-adopt-{uuid.uuid4().hex}"
    env = os.environ.copy()
    env["DATABASE_URL"] = get_settings().database_url
    env["PAYMENT_PROVIDER_URL"] = f"http://127.0.0.1:{port}"
    env["RECONCILE_QUEUE"] = pay_queue
    assert make_url(env["DATABASE_URL"]).database == TEST_DATABASE
    schedule_dir = Path(tempfile.mkdtemp(prefix="kill-"))
    payer_log = schedule_dir / "payer.log"
    payer = _worker(pay_queue, env, payer_log)
    worker = None
    beat = None
    try:
        _wait_for(payer, payer_log, " ready", 45, "payment worker")
        run_workflow.apply_async(args=[str(run_id)], queue=pay_queue)
        accepted = time.time() + 40
        while time.time() < accepted:
            stats = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=2).json()
            if stats["operations"] == 1:
                break
            time.sleep(0.05)
        else:
            pytest.fail("provider did not accept the refund before the worker could be stopped")
        _stop(payer)
        with SessionLocal() as session:
            operation = session.scalar(
                select(RefundOperation).where(RefundOperation.proposal_id == proposal_id)
            )
        assert payer.poll() is not None
        assert operation is not None and operation.status == "reserved"
        with SessionLocal() as session:
            committed = session.scalar(
                select(Order.refund_committed_minor).where(Order.id == operation.order_id)
            )
        assert committed == PAID
        stats = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=5).json()
        assert stats["attempts"] == 1
        assert stats["operations"] == 1
        env["RECONCILE_QUEUE"] = adopt_queue
        env["RECONCILE_DISCOVER_SECONDS"] = "2"
        worker_log = schedule_dir / "worker.log"
        beat_log = schedule_dir / "beat.log"
        worker = _worker(adopt_queue, env, worker_log)
        beat = _process(
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
            beat_log,
        )
        _wait_for(worker, worker_log, " ready", 45, "recovery worker")
        _wait_for(beat, beat_log, "Sending due task discover-open-refunds", 25, "celery beat")
        status = operation.status
        seen = time.time() + 180
        while time.time() < seen:
            with SessionLocal() as session:
                status = session.scalar(
                    select(RefundOperation.status).where(RefundOperation.id == operation.id)
                )
                rows = session.scalar(
                    select(func.count())
                    .select_from(RefundOperation)
                    .where(RefundOperation.proposal_id == proposal_id)
                )
            if status == "succeeded":
                break
            time.sleep(0.25)
        stats = httpx.get(f"http://127.0.0.1:{port}/stats", timeout=5).json()
        with SessionLocal() as session:
            committed = session.scalar(
                select(Order.refund_committed_minor).where(Order.id == operation.order_id)
            )
        assert status == "succeeded"
        assert rows == 1
        assert committed == PAID
        assert stats["attempts"] == 1
        assert stats["operations"] == 1
    finally:
        server.should_exit = True
        for process in (beat, worker, payer):
            if process is not None:
                _stop(process)
        bind.dispose()
        try:
            store.unlink(missing_ok=True)
        except OSError:
            pass


def _require_redis() -> None:
    client = redis.Redis.from_url(
        get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    try:
        client.ping()
    except redis.RedisError as exc:
        pytest.fail(f"Redis is not reachable: {exc}")


def _wait_until_healthy(port: int) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    pytest.fail("mock provider did not start")


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


def _process(args: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen[str]:
    child_env = env.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    log = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        args,
        cwd=ROOT,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for(
    process: subprocess.Popen[str], log_path: Path, needle: str, seconds: float, label: str
) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        if needle.lower() in text.lower():
            return
        if process.poll() is not None:
            pytest.fail(f"{label} exited early: {text[-2000:]}")
        time.sleep(0.2)
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    pytest.fail(f"{label} did not report {needle}: {text[-2000:]}")


def _stop(process: subprocess.Popen[str]) -> None:
    """Stop one worker or beat process and reap it.

    Windows kills the process tree. Other systems kill immediately so an
    in-flight provider call cannot record a timeout. A process that has
    already exited is left alone.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        try:
            process.kill()
        except OSError:
            if process.poll() is not None:
                return
            raise
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
