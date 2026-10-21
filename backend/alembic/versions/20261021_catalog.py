"""Verified model catalog and per-model accounting/key scope."""

from alembic import op
import sqlalchemy as sa

revision = "20261021_catalog"
down_revision = "20261019_billing"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "model_catalog",
        sa.Column("id", sa.String(200), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("route_revision", sa.String(64), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("concurrency_cap", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "gateway_keys",
        sa.Column(
            "model_scope", sa.String(200), nullable=False, server_default="legacy"
        ),
    )
    op.add_column(
        "usage_records", sa.Column("price_snapshot", sa.JSON(), nullable=True)
    )
    op.execute(
        "UPDATE gateway_keys SET state='revoke_pending' WHERE state IN ('active','provisioning')"
    )


def downgrade():
    op.drop_column("usage_records", "price_snapshot")
    op.drop_column("gateway_keys", "model_scope")
    op.drop_column("runs", "concurrency_cap")
    op.drop_table("model_catalog")
