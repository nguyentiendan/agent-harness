"""Private projects; preserve existing scope IDs and file volume mappings."""

from alembic import op
import sqlalchemy as sa

revision = "20261019_projects"
down_revision = "20261018_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("instructions", sa.String(16000), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("deleted_at", sa.Float(), nullable=True),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    with op.batch_alter_table("sessions") as b:
        b.add_column(sa.Column("project_id", sa.String(36), nullable=True))
        b.create_foreign_key("fk_sessions_project", "projects", ["project_id"], ["id"])
        b.create_index("ix_sessions_project_id", ["project_id"])
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    fks = sa.inspect(op.get_bind()).get_foreign_keys("execution_scopes")
    old = next(f for f in fks if f["constrained_columns"] == ["id"])
    with op.batch_alter_table("execution_scopes", naming_convention=naming) as b:
        b.drop_constraint(
            old["name"] or "fk_execution_scopes_id_sessions", type_="foreignkey"
        )
        b.add_column(sa.Column("project_id", sa.String(36), nullable=True))
        b.add_column(sa.Column("standalone_session_id", sa.String(36), nullable=True))
        b.create_foreign_key("fk_scope_project", "projects", ["project_id"], ["id"])
        b.create_foreign_key(
            "fk_scope_chat", "sessions", ["standalone_session_id"], ["id"]
        )
        b.create_unique_constraint("uq_scope_project", ["project_id"])
        b.create_unique_constraint("uq_scope_chat", ["standalone_session_id"])
    op.execute("UPDATE execution_scopes SET standalone_session_id=id")
    op.execute(
        "INSERT INTO execution_scopes(id,owner_id,has_files,standalone_session_id) SELECT s.id,s.owner_id,false,s.id FROM sessions s WHERE NOT EXISTS (SELECT 1 FROM execution_scopes e WHERE e.id=s.id)"
    )
    with op.batch_alter_table("execution_scopes") as b:
        b.create_check_constraint(
            "scope_exactly_one_parent",
            "(project_id IS NULL) != (standalone_session_id IS NULL)",
        )


def downgrade():
    raise RuntimeError(
        "Project downgrade requires an explicit data export; do not discard project ownership."
    )
