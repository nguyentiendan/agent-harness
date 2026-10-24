"""Add scoped expiring artifacts for recoverable context compaction."""

from alembic import op
import sqlalchemy as sa

revision = "20261025_tool_outputs"
down_revision = "20261023_context_tracing"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "tool_outputs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("workspace_revision", sa.Integer, nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("chars", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
    )
    op.create_index("ix_tool_outputs_scope_id", "tool_outputs", ["scope_id"])
    op.create_index("ix_tool_outputs_expires_at", "tool_outputs", ["expires_at"])


def downgrade():
    op.drop_table("tool_outputs")
