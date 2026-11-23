"""initial schema: business tables, refund ledger constraints, audit log

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5; change together with the embedding model


def _pk() -> sa.Column:
    return sa.Column(
        "id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def _fk(name: str, target: str, nullable: bool = False, ondelete: str | None = None) -> sa.Column:
    return sa.Column(
        name, pg.UUID(as_uuid=True), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # LangGraph checkpoint tables will live here, logically separate from the business ledger.
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")

    # ---- identities -------------------------------------------------------------------
    op.create_table(
        "customers",
        _pk(),
        sa.Column("external_auth_subject", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        _created_at(),
    )
    op.create_table(
        "staff_users",
        _pk(),
        sa.Column("external_auth_subject", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        _created_at(),
        sa.CheckConstraint("role IN ('reviewer','admin')", name="ck_staff_role"),
    )

    # ---- orders -----------------------------------------------------------------------
    op.create_table(
        "orders",
        _pk(),
        _fk("customer_id", "customers.id"),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False, server_default="INR"),
        sa.Column("paid_amount_minor", sa.BigInteger, nullable=False),
        # Refund money that is reserved or already paid out. The CHECK below makes
        # over-refunding impossible even if application code has a bug.
        sa.Column("refund_committed_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('placed','shipped','delivered','cancelled')", name="ck_orders_status"
        ),
        sa.CheckConstraint("paid_amount_minor >= 0", name="ck_orders_paid_nonneg"),
        sa.CheckConstraint(
            "refund_committed_minor >= 0 AND refund_committed_minor <= paid_amount_minor",
            name="ck_orders_refund_within_paid",
        ),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])

    op.create_table(
        "order_items",
        _pk(),
        _fk("order_id", "orders.id", ondelete="CASCADE"),
        sa.Column("product_id", sa.Text, nullable=False),
        sa.Column("product_name", sa.Text, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("paid_amount_minor", sa.BigInteger, nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_items_qty_pos"),
        sa.CheckConstraint("paid_amount_minor >= 0", name="ck_items_paid_nonneg"),
    )

    op.create_table(
        "returns",
        _pk(),
        _fk("order_id", "orders.id"),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("inspection_status", sa.Text, nullable=False, server_default="pending"),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('requested','in_transit','received','rejected')", name="ck_returns_status"
        ),
        sa.CheckConstraint(
            "inspection_status IN ('pending','passed','failed')", name="ck_returns_inspection"
        ),
    )
    op.create_index("ix_returns_order_id", "returns", ["order_id"])

    # ---- tickets and workflow runs ----------------------------------------------------
    op.create_table(
        "tickets",
        _pk(),
        _fk("customer_id", "customers.id"),
        _fk("order_id", "orders.id", nullable=True),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("summary", sa.Text, nullable=False),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint(
            "category IN ('order_status','refund_request','damaged_item')", name="ck_tickets_cat"
        ),
        sa.CheckConstraint(
            "status IN ('open','awaiting_customer','awaiting_approval','resolved','escalated')",
            name="ck_tickets_status",
        ),
    )
    op.create_index("ix_tickets_customer_id", "tickets", ["customer_id"])

    op.create_table(
        "workflow_runs",
        _pk(),
        _fk("ticket_id", "tickets.id"),
        sa.Column("thread_id", sa.Text, nullable=False, unique=True),  # LangGraph thread
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('running','awaiting_approval','completed','escalated','failed')",
            name="ck_runs_status",
        ),
    )

    # ---- policies (RAG) ---------------------------------------------------------------
    op.create_table(
        "policy_documents",
        _pk(),
        sa.Column("policy_key", sa.Text, nullable=False),  # e.g. 'refund_policy'
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("effective_to", sa.Date),
        sa.Column("source_uri", sa.Text, nullable=False),
        sa.UniqueConstraint("policy_key", "version", name="uq_policy_key_version"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from", name="ck_policy_dates"
        ),
    )
    op.create_table(
        "policy_chunks",
        _pk(),
        _fk("document_id", "policy_documents.id", ondelete="CASCADE"),
        sa.Column("section_ref", sa.Text, nullable=False),  # e.g. 'refund_policy_v1:section_3'
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("metadata", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("document_id", "section_ref", name="uq_chunk_section"),
    )
    op.execute(
        "CREATE INDEX ix_policy_chunks_embedding ON policy_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # ---- proposals, approvals, refunds ------------------------------------------------
    op.create_table(
        "action_proposals",
        _pk(),
        _fk("run_id", "workflow_runs.id"),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending_approval"),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('pending_approval','approved','rejected','expired','superseded',"
            "'executed','failed')",
            name="ck_proposals_status",
        ),
    )

    op.create_table(
        "approvals",
        _pk(),
        # One approval per proposal. A changed proposal means a NEW proposal and approval.
        _fk("proposal_id", "action_proposals.id"),
        _fk("reviewer_id", "staff_users.id", nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.UniqueConstraint("proposal_id", name="uq_approval_per_proposal"),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','expired')", name="ck_approvals_status"
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved','rejected') "
            "OR (decided_at IS NOT NULL AND reviewer_id IS NOT NULL)",
            name="ck_approvals_decision_has_reviewer",
        ),
    )

    op.create_table(
        "refund_operations",
        _pk(),
        _fk("order_id", "orders.id"),
        _fk("proposal_id", "action_proposals.id"),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("provider_reference", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="reserved"),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        # The duplicate-refund guard: the same key can never create a second operation.
        sa.UniqueConstraint("idempotency_key", name="uq_refund_idempotency_key"),
        sa.UniqueConstraint("proposal_id", name="uq_refund_per_proposal"),
        sa.CheckConstraint("amount_minor > 0", name="ck_refund_amount_pos"),
        sa.CheckConstraint(
            "status IN ('reserved','submitted','processing','succeeded','failed','unknown')",
            name="ck_refund_status",
        ),
    )
    op.create_index("ix_refund_ops_order_id", "refund_operations", ["order_id"])

    # ---- audit log --------------------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        _fk("run_id", "workflow_runs.id", nullable=True),
        sa.Column("actor", sa.Text, nullable=False),  # 'system', 'agent', 'reviewer:<id>', ...
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("redacted_payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created_at(),
    )
    op.create_index("ix_audit_events_run_id", "audit_events", ["run_id"])

    # ---- tamper protection ------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_audit_append_only BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation()"
    )
    op.execute(
        """
        CREATE FUNCTION forbid_proposal_payload_change() RETURNS trigger AS $$
        BEGIN
            IF NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
               OR NEW.action_type IS DISTINCT FROM OLD.action_type THEN
                RAISE EXCEPTION 'action_proposals payload is immutable; create a new proposal';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_proposal_immutable BEFORE UPDATE ON action_proposals "
        "FOR EACH ROW EXECUTE FUNCTION forbid_proposal_payload_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_proposal_immutable ON action_proposals")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS forbid_proposal_payload_change()")
    op.execute("DROP FUNCTION IF EXISTS forbid_audit_mutation()")
    for table in (
        "audit_events",
        "refund_operations",
        "approvals",
        "action_proposals",
        "policy_chunks",
        "policy_documents",
        "workflow_runs",
        "tickets",
        "returns",
        "order_items",
        "orders",
        "staff_users",
        "customers",
    ):
        op.drop_table(table)
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
