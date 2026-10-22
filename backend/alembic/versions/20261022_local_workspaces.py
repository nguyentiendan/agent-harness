"""Persist workspace selection without moving or deleting files."""

from alembic import op
import sqlalchemy as sa

revision = "20261022_local_workspaces"
down_revision = "20261021_catalog"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "execution_scopes",
        sa.Column(
            "workspace_mode", sa.String(10), nullable=False, server_default="managed"
        ),
    )
    op.add_column(
        "execution_scopes", sa.Column("local_path", sa.String(4096), nullable=True)
    )
    op.add_column(
        "execution_scopes",
        sa.Column(
            "workspace_revision", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_check_constraint(
        "workspace_revision_positive", "execution_scopes", "workspace_revision >= 0"
    )
    op.create_check_constraint(
        "workspace_mode_path",
        "execution_scopes",
        "(workspace_mode = 'managed' AND local_path IS NULL) OR (workspace_mode = 'local' AND local_path LIKE '/%')",
    )
    op.add_column("runs", sa.Column("workspace_snapshot", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("runs", "workspace_snapshot")
    op.drop_constraint("workspace_mode_path", "execution_scopes", type_="check")
    op.drop_constraint("workspace_revision_positive", "execution_scopes", type_="check")
    for field in ("workspace_revision", "local_path", "workspace_mode"):
        op.drop_column("execution_scopes", field)
