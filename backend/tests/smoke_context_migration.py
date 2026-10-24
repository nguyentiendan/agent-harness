"""Rehearse additive migration in an explicitly disposable empty PostgreSQL DB."""

import os
from urllib.parse import urlsplit

url = os.environ.get("PLATFORM_DATABASE_URL", "")
if "test" not in urlsplit(url).path.rsplit("/", 1)[-1]:
    raise RuntimeError("Explicit test database required")
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.platform.db import DB, User, Chat, Run, Project, UserLimit, ExecutionScope

cfg = Config("alembic.ini")
command.upgrade(cfg, "20261022_local_workspaces")
with DB.begin() as db:
    db.add(
        User(
            id="migration-user",
            username="migration-user",
            password_hash="preserve-hash",
            role="user",
            active=True,
            must_change_password=False,
        )
    )
    db.flush()
    db.add(
        Project(
            id="migration-project", owner_id="migration-user", name="preserve-project"
        )
    )
    db.flush()
    db.add(
        Chat(
            id="migration-chat",
            owner_id="migration-user",
            title="preserve-chat",
            project_id="migration-project",
        )
    )
    db.flush()
    db.add(
        Run(
            id="migration-run",
            session_id="migration-chat",
            request_id="migration",
            body_hash="preserve",
            model="fixture",
            event_offset=0,
            status="completed",
        )
    )
    db.add(UserLimit(user_id="migration-user", monthly_budget=7, models=["fixture"]))
    db.add(
        ExecutionScope(
            id="migration-project",
            owner_id="migration-user",
            project_id="migration-project",
            workspace_mode="managed",
            workspace_revision=3,
        )
    )
for _ in range(2):
    command.upgrade(cfg, "head")
with DB() as db:
    assert db.get(User, "migration-user").password_hash == "preserve-hash"
    assert db.get(Chat, "migration-chat").title == "preserve-chat"
    assert db.get(Run, "migration-run").body_hash == "preserve"
    assert db.get(UserLimit, "migration-user").monthly_budget == 7
    assert db.get(ExecutionScope, "migration-project").workspace_revision == 3
    assert (
        db.execute(text("select version_num from alembic_version")).scalar()
        == "20261025_tool_outputs"
    )
print(
    "Migration upgraded twice; user/password/chat/run/budget/workspace fixtures preserved."
)
