"""Customer order and ticket bodies. Unknown fields are rejected."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TicketCategory = Literal["order_status", "refund_request", "damaged_item"]


class OrderResponse(BaseModel):
    id: UUID
    status: str
    currency: str
    paid_amount_minor: int
    refund_committed_minor: int
    delivered_at: datetime | None
    # Catalog names for display. Not used for eligibility or refund amounts.
    product_names: list[str]


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: UUID
    category: TicketCategory
    summary: str = Field(min_length=1, max_length=2000)


class TicketResponse(BaseModel):
    id: UUID
    order_id: UUID | None
    category: str
    status: str
    summary: str
    refund_status: str | None
    message: str | None
    # Catalog names for the linked order. Display only.
    product_names: list[str]
