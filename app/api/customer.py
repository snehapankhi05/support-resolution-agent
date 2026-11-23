"""Customer orders and tickets. The customer id comes from the token's database row."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_db, require_customer
from app.db.models import ActionProposal, Order, OrderItem, RefundOperation, Ticket, WorkflowRun
from app.schemas.customer import OrderResponse, TicketCreate, TicketResponse
from app.services.messages import verified_customer_message

router = APIRouter()


def _product_names_by_order(db: Session, order_ids: list[UUID]) -> dict[UUID, list[str]]:
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


def _names_for(db: Session, order_id: UUID | None) -> list[str]:
    if order_id is None:
        return []
    return _product_names_by_order(db, [order_id]).get(order_id, [])


def _order_response(order: Order, product_names: list[str]) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        status=order.status,
        currency=order.currency.strip(),
        paid_amount_minor=order.paid_amount_minor,
        refund_committed_minor=order.refund_committed_minor,
        delivered_at=order.delivered_at,
        product_names=product_names,
    )


def _own_order(db: Session, customer_id: UUID, order_id: UUID) -> Order:
    order = db.scalar(
        select(Order).where(Order.id == order_id, Order.customer_id == customer_id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


def _latest_refund(db: Session, ticket_id: UUID) -> RefundOperation | None:
    return db.scalar(
        select(RefundOperation)
        .join(ActionProposal, ActionProposal.id == RefundOperation.proposal_id)
        .join(WorkflowRun, WorkflowRun.id == ActionProposal.run_id)
        .where(WorkflowRun.ticket_id == ticket_id)
        .order_by(RefundOperation.created_at.desc())
    )


def _latest_proposal_status(db: Session, ticket_id: UUID) -> str | None:
    return db.scalar(
        select(ActionProposal.status)
        .join(WorkflowRun, WorkflowRun.id == ActionProposal.run_id)
        .where(WorkflowRun.ticket_id == ticket_id)
        .order_by(ActionProposal.created_at.desc())
    )


def _ticket_response(db: Session, ticket: Ticket, product_names: list[str]) -> TicketResponse:
    refund = _latest_refund(db, ticket.id)
    refund_status = None if refund is None else refund.status
    amount = None if refund is None else refund.amount_minor
    ledger = verified_customer_message(
        ticket.status,
        refund_status,
        amount,
        proposal_status=_latest_proposal_status(db, ticket.id),
    )
    if refund is not None:
        message = ledger
    elif ticket.resolution_message:
        message = ticket.resolution_message
    else:
        message = ledger
    return TicketResponse(
        id=ticket.id,
        order_id=ticket.order_id,
        category=ticket.category,
        status=ticket.status,
        summary=ticket.summary,
        refund_status=refund_status,
        message=message,
        product_names=product_names,
    )


@router.get("/orders", response_model=list[OrderResponse])
def list_orders(
    customer: Principal = Depends(require_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[OrderResponse]:
    """List orders for the authenticated customer. A query customer id is ignored."""
    orders = db.scalars(
        select(Order).where(Order.customer_id == customer.id).order_by(Order.created_at)
    ).all()
    names = _product_names_by_order(db, [order.id for order in orders])
    return [_order_response(order, names.get(order.id, [])) for order in orders]


@router.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: UUID,
    customer: Principal = Depends(require_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> OrderResponse:
    """Return one own order. Another customer's order is the same 404 as a missing one."""
    order = _own_order(db, customer.id, order_id)
    return _order_response(order, _names_for(db, order.id))


@router.post("/tickets", response_model=TicketResponse, status_code=201)
def create_ticket(
    body: TicketCreate,
    customer: Principal = Depends(require_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> TicketResponse:
    """Open a ticket on an order this customer owns. The body cannot name a customer."""
    summary = body.summary.strip()
    if not summary:
        raise HTTPException(status_code=422, detail="summary is required")
    order = _own_order(db, customer.id, body.order_id)
    ticket = Ticket(
        customer_id=customer.id,
        order_id=order.id,
        category=body.category,
        status="open",
        summary=summary,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return _ticket_response(db, ticket, _names_for(db, ticket.order_id))


@router.get("/tickets", response_model=list[TicketResponse])
def list_tickets(
    customer: Principal = Depends(require_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[TicketResponse]:
    """List tickets owned by the authenticated customer."""
    tickets = db.scalars(
        select(Ticket).where(Ticket.customer_id == customer.id).order_by(Ticket.created_at)
    ).all()
    names = _product_names_by_order(
        db, [ticket.order_id for ticket in tickets if ticket.order_id is not None]
    )
    return [
        _ticket_response(
            db,
            ticket,
            names.get(ticket.order_id, []) if ticket.order_id is not None else [],
        )
        for ticket in tickets
    ]


@router.get("/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket(
    ticket_id: UUID,
    customer: Principal = Depends(require_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> TicketResponse:
    """Return one own ticket. Another customer's ticket is a 404."""
    ticket = db.scalar(
        select(Ticket).where(Ticket.id == ticket_id, Ticket.customer_id == customer.id)
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return _ticket_response(db, ticket, _names_for(db, ticket.order_id))
