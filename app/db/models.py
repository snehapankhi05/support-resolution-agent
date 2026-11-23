"""SQLAlchemy models for the tables created in migration 0001.

The database owns the constraints. These mappings do not create or alter schema.
"""

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 384


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    external_auth_subject: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class StaffUser(Base):
    __tablename__ = "staff_users"
    __table_args__ = (CheckConstraint("role IN ('reviewer','admin')", name="ck_staff_role"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    external_auth_subject: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class LoginAccount(Base):
    __tablename__ = "login_accounts"
    __table_args__ = (
        UniqueConstraint("email", name="uq_login_email"),
        UniqueConstraint("customer_id", name="uq_login_customer"),
        UniqueConstraint("staff_user_id", name="uq_login_staff"),
        CheckConstraint(
            "(customer_id IS NOT NULL) <> (staff_user_id IS NOT NULL)",
            name="ck_login_exactly_one_owner",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True
    )
    staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('placed','shipped','delivered','cancelled')", name="ck_orders_status"
        ),
        CheckConstraint("paid_amount_minor >= 0", name="ck_orders_paid_nonneg"),
        CheckConstraint(
            "refund_committed_minor >= 0 AND refund_committed_minor <= paid_amount_minor",
            name="ck_orders_refund_within_paid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    status: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="INR")
    paid_amount_minor: Mapped[int] = mapped_column(BigInteger)
    refund_committed_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_items_qty_pos"),
        CheckConstraint("paid_amount_minor >= 0", name="ck_items_paid_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE")
    )
    product_id: Mapped[str] = mapped_column(Text)
    product_name: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer)
    paid_amount_minor: Mapped[int] = mapped_column(BigInteger)


class Return(Base):
    __tablename__ = "returns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','in_transit','received','rejected')", name="ck_returns_status"
        ),
        CheckConstraint(
            "inspection_status IN ('pending','passed','failed')", name="ck_returns_inspection"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"))
    status: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inspection_status: Mapped[str] = mapped_column(Text, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(
            "category IN ('order_status','refund_request','damaged_item')", name="ck_tickets_cat"
        ),
        CheckConstraint(
            "status IN ('open','awaiting_customer','awaiting_approval','resolved','escalated')",
            name="ck_tickets_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"))
    category: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    summary: Mapped[str] = mapped_column(Text)
    resolution_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','awaiting_approval','completed','escalated','failed')",
            name="ck_runs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"))
    thread_id: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    __table_args__ = (
        UniqueConstraint("policy_key", "version", name="uq_policy_key_version"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from", name="ck_policy_dates"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    policy_key: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_uri: Mapped[str] = mapped_column(Text)


class PolicyChunk(Base):
    __tablename__ = "policy_chunks"
    __table_args__ = (UniqueConstraint("document_id", "section_ref", name="uq_chunk_section"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policy_documents.id", ondelete="CASCADE")
    )
    section_ref: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    chunk_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )


class ActionProposal(Base):
    __tablename__ = "action_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_approval','approved','rejected','expired','superseded',"
            "'executed','failed')",
            name="ck_proposals_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_runs.id"))
    action_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending_approval")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_approval_per_proposal"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired')", name="ck_approvals_status"
        ),
        CheckConstraint(
            "status NOT IN ('approved','rejected') "
            "OR (decided_at IS NOT NULL AND reviewer_id IS NOT NULL)",
            name="ck_approvals_decision_has_reviewer",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("action_proposals.id")
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_users.id")
    )
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class RefundOperation(Base):
    __tablename__ = "refund_operations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_refund_idempotency_key"),
        UniqueConstraint("proposal_id", name="uq_refund_per_proposal"),
        CheckConstraint("amount_minor > 0", name="ck_refund_amount_pos"),
        CheckConstraint(
            "status IN ('reserved','submitted','processing','succeeded','failed','unknown')",
            name="ck_refund_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"))
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("action_proposals.id")
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(Text)
    provider_reference: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id")
    )
    actor: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    redacted_payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
