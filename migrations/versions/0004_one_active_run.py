"""One active workflow run per ticket. Existing rows are left unchanged.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

INDEX = "uq_one_active_run_per_ticket"
ACTIVE = "status IN ('running', 'awaiting_approval')"


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            f"""
            SELECT ticket_id::text, count(*)
            FROM workflow_runs
            WHERE {ACTIVE}
            GROUP BY ticket_id
            HAVING count(*) > 1
            """
        )
    ).fetchall()
    if duplicates:
        detail = ", ".join(f"{ticket_id} {count}" for ticket_id, count in duplicates)
        raise RuntimeError(
            "Refusing to add uq_one_active_run_per_ticket while duplicate "
            f"active runs exist: {detail}"
        )
    op.create_index(
        INDEX,
        "workflow_runs",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="workflow_runs")
