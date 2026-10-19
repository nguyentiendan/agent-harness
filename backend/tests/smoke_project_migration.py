"""PostgreSQL-only migration preservation test in an isolated temporary schema."""

import os, subprocess, uuid
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

url = make_url(os.environ["PLATFORM_DATABASE_URL"])
if url.database != "platform_test":
    raise RuntimeError("Requires platform_test.")
engine = create_engine(url)
schema = "migration_" + uuid.uuid4().hex
owner, old, empty = [str(uuid.uuid4()) for _ in range(3)]
with engine.begin() as db:
    db.execute(text("CREATE SCHEMA " + schema))
scoped = url.update_query_dict({"options": "-csearch_path=" + schema})
env = {
    **os.environ,
    "PLATFORM_DATABASE_URL": scoped.render_as_string(hide_password=False),
}
local = create_engine(scoped)
try:
    subprocess.run(["alembic", "upgrade", "20261018_execution"], env=env, check=True)
    with local.begin() as db:
        db.execute(
            text(
                "INSERT INTO users(id,username,password_hash,role,active,must_change_password,created_at) VALUES(:id,'migration-user','test-hash','user',true,false,1)"
            ),
            {"id": owner},
        )
        for sid in (old, empty):
            db.execute(
                text(
                    "INSERT INTO sessions(id,owner_id,title,status,created_at,updated_at) VALUES(:id,:owner,'Existing chat','idle',1,1)"
                ),
                {"id": sid, "owner": owner},
            )
        db.execute(
            text(
                "INSERT INTO execution_scopes(id,owner_id,has_files) VALUES(:id,:owner,true)"
            ),
            {"id": old, "owner": owner},
        )
    subprocess.run(["alembic", "upgrade", "head"], env=env, check=True)
    subprocess.run(["alembic", "upgrade", "head"], env=env, check=True)
    with local.connect() as db:
        rows = {r.id: r for r in db.execute(text("SELECT * FROM execution_scopes"))}
        assert set(rows) == {old, empty}
        assert rows[old].has_files and rows[old].standalone_session_id == old
        assert not rows[empty].has_files and rows[empty].standalone_session_id == empty
        assert db.execute(text("SELECT count(*) FROM sessions")).scalar() == 2
        assert db.execute(text("SELECT count(*) FROM users")).scalar() == 1
    print(
        "PASS: migration preserves old scope/owner/file marker, backfills empty chat, repeat is idempotent"
    )
finally:
    local.dispose()
    with engine.begin() as db:
        db.execute(text("DROP SCHEMA " + schema + " CASCADE"))
    engine.dispose()
