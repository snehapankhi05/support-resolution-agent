"""Start a workflow run. The customer id comes from the token's database row."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_db, require_customer
from app.services.runs import RunStartError, start_workflow_run
from app.worker import enqueue_workflow

router = APIRouter()


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID


class RunResponse(BaseModel):
    run_id: UUID
    ticket_id: UUID
    thread_id: str
    status: str
    created: bool


@router.post("/runs", response_model=RunResponse)
def create_run(
    body: RunCreate,
    response: Response,
    customer: Principal = Depends(require_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> RunResponse:
    """Open one run for the caller's ticket. An active run is returned as it is."""
    try:
        result = start_workflow_run(db, customer.id, body.ticket_id)
        db.commit()
    except RunStartError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    enqueue_workflow(result.run_id)
    response.status_code = 201 if result.created else 200
    return RunResponse(
        run_id=result.run_id,
        ticket_id=result.ticket_id,
        thread_id=result.thread_id,
        status=result.status,
        created=result.created,
    )
