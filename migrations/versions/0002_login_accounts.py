"""login accounts: one email, exactly one customer or staff owner

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_accounts",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("customer_id", pg.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column(
            "staff_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("staff_users.id"), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("email", name="uq_login_email"),
        sa.UniqueConstraint("customer_id", name="uq_login_customer"),
        sa.UniqueConstraint("staff_user_id", name="uq_login_staff"),
        sa.CheckConstraint(
            "(customer_id IS NOT NULL) <> (staff_user_id IS NOT NULL)",
            name="ck_login_exactly_one_owner",
        ),
    )


def downgrade() -> None:
    op.drop_table("login_accounts")
