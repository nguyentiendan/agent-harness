"""Add scoped context and local telemetry without changing existing data."""

from alembic import op
import sqlalchemy as sa

revision = "20261023_context_tracing"
down_revision = "20261022_local_workspaces"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "context_messages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("group_id", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("workspace_revision", sa.Integer, nullable=True),
        sa.Column("created_at", sa.Float, server_default=sa.text("0"), nullable=False),
        sa.UniqueConstraint("scope_id", "sequence"),
    )
    op.create_index("ix_context_messages_scope_id", "context_messages", ["scope_id"])
    op.create_index("ix_context_messages_run_id", "context_messages", ["run_id"])
    op.create_table(
        "context_summaries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, server_default=sa.text("0"), nullable=False),
    )
    op.create_index("ix_context_summaries_scope_id", "context_summaries", ["scope_id"])
    op.create_table(
        "trace_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("data", sa.JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status", sa.String(24), server_default="running", nullable=False),
        sa.Column("created_at", sa.Float, server_default=sa.text("0"), nullable=False),
        sa.Column("finished_at", sa.Float, nullable=True),
    )
    op.create_index("ix_trace_records_scope_id", "trace_records", ["scope_id"])
    op.create_table(
        "trace_spans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("data", sa.JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status", sa.String(24), server_default="running", nullable=False),
        sa.Column("usage_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.Float, server_default=sa.text("0"), nullable=False),
        sa.Column("finished_at", sa.Float, nullable=True),
    )
    op.create_index("ix_trace_spans_trace_id", "trace_spans", ["trace_id"])
    op.create_table(
        "trace_payloads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, server_default=sa.text("0"), nullable=False),
    )
    op.create_index("ix_trace_payloads_trace_id", "trace_payloads", ["trace_id"])
    op.create_table(
        "trace_export_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("next_attempt_at", sa.Float, server_default="0", nullable=False),
    )
    op.create_index("ix_trace_export_jobs_trace_id", "trace_export_jobs", ["trace_id"])


def downgrade():
    op.drop_table("trace_export_jobs")
    op.drop_table("trace_payloads")
    op.drop_table("trace_spans")
    op.drop_table("trace_records")
    op.drop_table("context_summaries")
    op.drop_table("context_messages")
