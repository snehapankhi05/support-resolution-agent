"""Mock payment provider with idempotency stored in SQLite.

The SQLite file is the provider's memory across process restarts. It is not the
refund ledger. Postgres still records reservation and the verified result.
"""

import asyncio
import sqlite3
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

Mode = Literal["succeed", "timeout_after_accept", "error_500", "delay"]
DEFAULT_STORE = Path(__file__).resolve().parent / "data" / "idempotency.sqlite"


class RefundRequest(BaseModel):
    idempotency_key: str = Field(min_length=1)
    amount_minor: int = Field(gt=0)
    order_id: str = Field(min_length=1)


class RefundOperationBody(BaseModel):
    idempotency_key: str
    amount_minor: int
    order_id: str
    status: str
    provider_reference: str


class ControlBody(BaseModel):
    mode: Mode
    delay_seconds: float = Field(default=0, ge=0, le=30)


class ProviderStore:
    """One row per idempotency key. The row is committed before a response is returned."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_operations (
                idempotency_key TEXT PRIMARY KEY,
                amount_minor INTEGER NOT NULL,
                order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                provider_reference TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, idempotency_key: str) -> RefundOperationBody | None:
        row = self._conn.execute(
            """
            SELECT idempotency_key, amount_minor, order_id, status, provider_reference
            FROM provider_operations WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return RefundOperationBody(
            idempotency_key=row["idempotency_key"],
            amount_minor=row["amount_minor"],
            order_id=row["order_id"],
            status=row["status"],
            provider_reference=row["provider_reference"],
        )

    def insert(self, body: RefundRequest) -> RefundOperationBody:
        record = RefundOperationBody(
            idempotency_key=body.idempotency_key,
            amount_minor=body.amount_minor,
            order_id=body.order_id,
            status="succeeded",
            provider_reference=f"mock_{uuid.uuid4().hex}",
        )
        try:
            self._conn.execute(
                """
                INSERT INTO provider_operations (
                    idempotency_key, amount_minor, order_id, status, provider_reference
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.idempotency_key,
                    record.amount_minor,
                    record.order_id,
                    record.status,
                    record.provider_reference,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            self._conn.rollback()
            raise
        return record

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM provider_operations").fetchone()[0])

    def close(self) -> None:
        self._conn.close()


def create_app(store_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Mock payment provider")
    app.state.store = ProviderStore(Path(store_path) if store_path else DEFAULT_STORE)
    app.state.mode = "succeed"
    app.state.delay_seconds = 0.0
    app.state.attempts = 0
    app.state.lock = asyncio.Lock()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/control")
    async def control(body: ControlBody) -> dict:
        async with app.state.lock:
            app.state.mode = body.mode
            app.state.delay_seconds = body.delay_seconds
        return {"mode": body.mode, "delay_seconds": body.delay_seconds}

    @app.get("/stats")
    async def stats() -> dict:
        async with app.state.lock:
            return {"attempts": app.state.attempts, "operations": app.state.store.count()}

    @app.get("/refunds/{idempotency_key}")
    async def get_refund(idempotency_key: str) -> RefundOperationBody:
        async with app.state.lock:
            found = app.state.store.get(idempotency_key)
        if found is None:
            raise HTTPException(status_code=404, detail="refund operation not found")
        return found

    @app.post("/refunds")
    async def create_refund(body: RefundRequest) -> RefundOperationBody:
        async with app.state.lock:
            existing = _existing_or_conflict(app.state.store, body)
            if existing is not None:
                return existing
            mode = app.state.mode
            delay = app.state.delay_seconds
            if mode == "error_500":
                app.state.attempts += 1
                raise HTTPException(status_code=500, detail="mock provider failure")
            if mode != "delay":
                record = _store_new(app, body)
            else:
                record = None
        if mode == "delay":
            if delay > 0:
                await asyncio.sleep(delay)
            async with app.state.lock:
                existing = _existing_or_conflict(app.state.store, body)
                if existing is not None:
                    return existing
                return _store_new(app, body)
        if mode == "timeout_after_accept" and delay > 0:
            await asyncio.sleep(delay)
        return record

    return app


def _existing_or_conflict(store: ProviderStore, body: RefundRequest) -> RefundOperationBody | None:
    existing = store.get(body.idempotency_key)
    if existing is None:
        return None
    if existing.amount_minor != body.amount_minor or existing.order_id != body.order_id:
        raise HTTPException(
            status_code=409, detail="idempotency key reused with a different payload"
        )
    return existing


def _store_new(app: FastAPI, body: RefundRequest) -> RefundOperationBody:
    try:
        record = app.state.store.insert(body)
    except sqlite3.IntegrityError:
        existing = _existing_or_conflict(app.state.store, body)
        if existing is None:
            raise
        return existing
    app.state.attempts += 1
    return record
