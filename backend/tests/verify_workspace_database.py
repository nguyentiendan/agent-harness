"""Run additive migration and suite in a unique schema in platform_test only."""

import os
import subprocess
import sys
import uuid
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

base = make_url(os.environ["PLATFORM_DATABASE_URL"]).set(database="platform_test")
assert base.database == "platform_test"
admin = create_engine(base)
schema = "workspace_test_" + uuid.uuid4().hex[:12]
with admin.begin() as c:
    c.execute(text(f"CREATE SCHEMA {schema}"))
url = base.update_query_dict({"options": f"-csearch_path={schema}"})
env = dict(
    os.environ,
    PLATFORM_DATABASE_URL=url.render_as_string(hide_password=False),
    TEST_DATABASE_URL=url.render_as_string(hide_password=False),
    OPENROUTER_ENABLED="false",
    PYTHONPATH=".",
)
engine = create_engine(url)


def migrate(rev):
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", rev], env=env, check=True
    )


try:
    migrate("20261021_catalog")
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO users(id,username,password_hash,role,active,must_change_password,created_at) VALUES('u','fixture','hash','user',true,false,1)"
            )
        )
        c.execute(
            text(
                "INSERT INTO sessions(id,owner_id,title,status,created_at,updated_at) VALUES('s','u','old','idle',1,1)"
            )
        )
        c.execute(
            text(
                "INSERT INTO execution_scopes(id,owner_id,standalone_session_id,has_files) VALUES('s','u','s',true)"
            )
        )
        c.execute(
            text(
                "INSERT INTO runs(id,session_id,request_id,body_hash,status,model,event_offset,created_at) VALUES('r','s','req','hash','completed','fake',0,1)"
            )
        )
        c.execute(
            text(
                "INSERT INTO user_limits(user_id,monthly_budget,models,concurrent_runs,max_output_tokens,revision) VALUES('u',5,'[]',1,4096,1)"
            )
        )
    migrate("head")
    migrate("head")
    with engine.connect() as c:
        assert c.execute(
            text(
                "SELECT workspace_mode,local_path,workspace_revision,has_files FROM execution_scopes"
            )
        ).one() == ("managed", None, 0, True)
        assert c.execute(text("SELECT workspace_snapshot FROM runs")).scalar() is None
        assert c.execute(text("SELECT monthly_budget FROM user_limits")).scalar() == 5
        assert c.execute(text("SELECT password_hash FROM users")).scalar() == "hash"
        for table in ("users", "sessions", "execution_scopes", "runs", "user_limits"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 1
    print("Migration twice: existing user/chat/run/files/budget preserved", flush=True)
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        env=env,
        check=True,
    )
finally:
    engine.dispose()
    with admin.begin() as c:
        c.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()
