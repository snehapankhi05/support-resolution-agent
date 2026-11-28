"""Read-only staff view of escalated tickets. This module does not propose or pay."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_db, require_reviewer
from app.db.models import OrderItem, Ticket

router = APIRouter()


class EscalationView(BaseModel):
    """Escalated ticket fields a reviewer may see. Amounts and complaints are absent."""

    ticket_id: UUID
    category: str
    product_names: list[str]
    resolution_message: str | None


def _product_names(db: Session, order_ids: list[UUID]) -> dict[UUID, list[str]]:
    """Catalog names from order_items. Customer text is not a product name."""
    unique = list(dict.fromkeys(order_ids))
    if not unique:
        return {}
    rows = db.execute(
        select(OrderItem.order_id, OrderItem.product_name)
        .where(OrderItem.order_id.in_(unique))
        .order_by(OrderItem.product_name)
    ).all()
    grouped: dict[UUID, list[str]] = {order_id: [] for order_id in unique}
    for order_id, name in rows:
        if isinstance(name, str) and name.strip():
            grouped.setdefault(order_id, []).append(name.strip())
    return grouped


def _view(ticket: Ticket, product_names: list[str]) -> EscalationView:
    """Copy the stored sentence. This does not derive a refund message."""
    return EscalationView(
        ticket_id=ticket.id,
        category=ticket.category,
        product_names=product_names,
        resolution_message=ticket.resolution_message,
    )


@router.get("/escalations", response_model=list[EscalationView])
def list_escalations(
    reviewer: Principal = Depends(require_reviewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[EscalationView]:
    """List every escalated ticket for a reviewer or admin.

    The caller is the staff row behind the token. The request cannot name a customer
    or widen the status filter.
    """
    del reviewer
    tickets = db.scalars(
        select(Ticket).where(Ticket.status == "escalated").order_by(Ticket.created_at)
    ).all()
    order_ids = [ticket.order_id for ticket in tickets if ticket.order_id is not None]
    names = _product_names(db, order_ids)
    return [
        _view(ticket, names.get(ticket.order_id, []) if ticket.order_id is not None else [])
        for ticket in tickets
    ]


@router.get("/escalations/{ticket_id}", response_model=EscalationView)
def get_escalation(
    ticket_id: UUID,
    reviewer: Principal = Depends(require_reviewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> EscalationView:
    """Return one escalated ticket. A missing or non-escalated id is the same 404.

    The caller is the staff row behind the token. This read does not create a proposal
    or a refund operation.
    """
    del reviewer
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.status == "escalated"))
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    names = _product_names(db, [ticket.order_id] if ticket.order_id is not None else [])
    return _view(ticket, names.get(ticket.order_id, []) if ticket.order_id is not None else [])
