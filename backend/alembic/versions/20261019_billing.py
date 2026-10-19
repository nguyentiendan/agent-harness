"""Per-user limits, encrypted virtual keys and durable call accounting."""

from alembic import op
import sqlalchemy as sa

revision = "20261019_billing"
down_revision = "20261019_projects"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_limits",
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True
        ),
        sa.Column("monthly_budget", sa.Numeric(18, 10), nullable=True),
        sa.Column("models", sa.JSON(), nullable=False),
        sa.Column("concurrent_runs", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "gateway_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_gateway_keys_user_id", "gateway_keys", ["user_id"])
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("reserved", sa.Numeric(18, 10), nullable=False),
        sa.Column("cost", sa.Numeric(18, 10), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("gateway_request_id", sa.String(200), nullable=True),
        sa.Column("reconciliation_note", sa.String(500), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    for field in ("user_id", "run_id", "period"):
        op.create_index("ix_usage_records_" + field, "usage_records", [field])


def downgrade():
    op.drop_table("usage_records")
    op.drop_table("gateway_keys")
    op.drop_table("user_limits")
