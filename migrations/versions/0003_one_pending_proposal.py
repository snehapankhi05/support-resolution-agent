"""One pending proposal per run and action. Existing rows are left unchanged.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

CONSTRAINT = "uq_one_pending_proposal_per_action"


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            """
            SELECT run_id::text, action_type, count(*)
            FROM action_proposals
            WHERE status = 'pending_approval'
            GROUP BY run_id, action_type
            HAVING count(*) > 1
            """
        )
    ).fetchall()
    if duplicates:
        detail = ", ".join(f"{run_id} {action_type} {count}" for run_id, action_type, count in duplicates)
        raise RuntimeError(
            "Refusing to add uq_one_pending_proposal_per_action while duplicate "
            f"pending proposals exist: {detail}"
        )
    op.create_index(
        CONSTRAINT,
        "action_proposals",
        ["run_id", "action_type"],
        unique=True,
        postgresql_where=sa.text("status = 'pending_approval'"),
    )


def downgrade() -> None:
    op.drop_index(CONSTRAINT, table_name="action_proposals")
