"""Platform persistence. Production uses PostgreSQL; tests use an isolated database."""

import os
import time
from sqlalchemy import (
    create_engine,
    String,
    Boolean,
    Float,
    JSON,
    ForeignKey,
    Integer,
    UniqueConstraint,
    CheckConstraint,
    Numeric,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class LoginSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    instructions: Mapped[str] = mapped_column(String(16000), default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Chat(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    status: Mapped[str] = mapped_column(String(20), default="idle")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("session_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(100))
    body_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="running")
    model: Mapped[str] = mapped_column(String(120))
    workspace_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    concurrency_cap: Mapped[int] = mapped_column(Integer, default=1)
    event_offset: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("session_id", "sequence"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class Audit(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    reset_at: Mapped[float] = mapped_column(Float)


class ExecutionScope(Base):
    __tablename__ = "execution_scopes"
    __table_args__ = (
        CheckConstraint(
            "(project_id IS NULL) != (standalone_session_id IS NULL)",
            name="scope_exactly_one_parent",
        ),
        CheckConstraint("workspace_revision >= 0", name="workspace_revision_positive"),
        CheckConstraint(
            "(workspace_mode = 'managed' AND local_path IS NULL) OR (workspace_mode = 'local' AND local_path LIKE '/%')",
            name="workspace_mode_path",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, unique=True
    )
    standalone_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, unique=True
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_mode: Mapped[str] = mapped_column(
        String(10), default="managed", server_default="managed"
    )
    local_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    workspace_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    has_files: Mapped[bool] = mapped_column(Boolean, default=False)


class RunLease(Base):
    __tablename__ = "run_leases"
    scope_id: Mapped[str] = mapped_column(
        ForeignKey("execution_scopes.id"), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    sandbox_started: Mapped[bool] = mapped_column(Boolean, default=False)
    terminal_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    terminal_reason: Mapped[str] = mapped_column(String(500), default="")


class UserLimit(Base):
    __tablename__ = "user_limits"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    monthly_budget: Mapped[float | None] = mapped_column(Numeric(18, 10), nullable=True)
    models: Mapped[list] = mapped_column(JSON, default=list)
    concurrent_runs: Mapped[int] = mapped_column(Integer, default=1)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class GatewayKey(Base):
    __tablename__ = "gateway_keys"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))
    revision: Mapped[int] = mapped_column(Integer)
    encrypted_key: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(24), default="provisioning")
    expires_at: Mapped[float] = mapped_column(Float)
    model_scope: Mapped[str] = mapped_column(String(200), default="legacy")


class UsageRecord(Base):
    __tablename__ = "usage_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    model: Mapped[str] = mapped_column(String(120))
    price_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reserved: Mapped[float] = mapped_column(Numeric(18, 10))
    cost: Mapped[float | None] = mapped_column(Numeric(18, 10), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    reconciliation_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gateway_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class CatalogModel(Base):
    __tablename__ = "model_catalog"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[float] = mapped_column(Float, default=0)
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    route_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)


# No production fallback to a local JSON/SQLite file.
url = os.environ.get("PLATFORM_DATABASE_URL")
if not url:
    raise RuntimeError("PLATFORM_DATABASE_URL must be configured")
engine = create_engine(
    url,
    pool_pre_ping=True,
    **(
        {"connect_args": {"check_same_thread": False}}
        if url.startswith("sqlite")
        else {}
    ),
)
DB = sessionmaker(engine, expire_on_commit=False)
