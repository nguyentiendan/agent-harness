"""Persistent execution scopes and cleanup leases (no user-data reset)."""

from alembic import op
import sqlalchemy as sa

revision = "20261018_execution"
down_revision = "8e8d48c6f462"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "execution_scopes",
        sa.Column("id", sa.String(36), sa.ForeignKey("sessions.id"), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("has_files", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_execution_scopes_owner_id", "execution_scopes", ["owner_id"])
    op.create_table(
        "run_leases",
        sa.Column(
            "scope_id",
            sa.String(36),
            sa.ForeignKey("execution_scopes.id"),
            primary_key=True,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("sandbox_started", sa.Boolean(), nullable=False),
        sa.Column("terminal_status", sa.String(20), nullable=True),
        sa.Column("terminal_reason", sa.String(500), nullable=False),
    )


def downgrade():
    op.drop_table("run_leases")
    op.drop_index("ix_execution_scopes_owner_id", table_name="execution_scopes")
    op.drop_table("execution_scopes")
