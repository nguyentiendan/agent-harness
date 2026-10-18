"""Identity, password hashing, and ownership policy. No transport secrets in DTOs."""

import hashlib
import re
import secrets
import time
import uuid
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException
from sqlalchemy import select, delete
from .db import User, LoginSession, Chat, Audit

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def verify(password, encoded):
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def password_hash(password):
    if not 12 <= len(password) <= 128:
        raise HTTPException(422, "Password must contain 12–128 characters.")
    return hasher.hash(password)


def user_dto(user):
    return {
        k: getattr(user, k)
        for k in (
            "id",
            "username",
            "role",
            "active",
            "must_change_password",
            "created_at",
        )
    }


def create_user(db, username, password, role="user", must_change=True):
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,79}", username):
        raise HTTPException(
            422, "Username must be 3–80 letters, digits, dots, underscores or hyphens."
        )
    if role not in ("admin", "user"):
        raise HTTPException(422, "Invalid role.")
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "Username already exists.")
    u = User(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=password_hash(password),
        role=role,
        must_change_password=must_change,
    )
    db.add(u)
    db.flush()
    return user_dto(u)


def audit(db, actor, action, target):
    db.add(Audit(actor_id=actor.id, action=action, target_id=target))


def authenticate(db, token):
    session = db.get(LoginSession, digest(token or ""))
    if not session or session.expires_at <= time.time():
        raise HTTPException(401, "Please sign in.")
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Please sign in.")
    return user, session


def revoke(db, user_id):
    db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))


def owned_chat(db, actor, sid, action="read"):
    c = db.get(Chat, sid)
    if not c or c.deleted_at or (c.owner_id != actor.id and actor.role != "admin"):
        raise HTTPException(404, "Chat not found.")
    if c.owner_id != actor.id:
        if action == "continue":
            raise HTTPException(
                403, "Administrators cannot send messages as another user."
            )
        audit(db, actor, "chat." + action, sid)
    return c
