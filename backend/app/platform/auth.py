"""Same-origin cookie authentication and administrator account lifecycle."""

import os
import secrets
import time
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from .db import DB, User, LoginSession, LoginAttempt
from .identity import (
    authenticate,
    digest,
    verify,
    hasher,
    DUMMY_HASH,
    user_dto,
    create_user,
    password_hash,
    revoke,
    audit,
)

router = APIRouter()
COOKIE = "harness_session"
ORIGIN = os.environ.get("APP_ORIGIN", "http://localhost:3000").rstrip("/")
SECURE = ORIGIN.startswith("https://")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class NewUser(Login):
    role: Literal["admin", "user"] = "user"


class Password(Input):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class ResetPassword(Input):
    password: str = Field(min_length=12, max_length=128)


class UserPatch(Input):
    role: Literal["admin", "user"] | None = None
    active: bool | None = None


def check_origin(request):
    if request.headers.get("origin") != ORIGIN:
        raise HTTPException(403, "Request origin is not allowed.")


def context(request: Request):
    with DB.begin() as db:
        user, login_session = authenticate(db, request.cookies.get(COOKIE))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            check_origin(request)
            if not secrets.compare_digest(
                digest(request.headers.get("x-csrf-token", "")), login_session.csrf_hash
            ):
                raise HTTPException(403, "Invalid CSRF token. Refresh and try again.")
        allowed = ("/api/auth/me", "/api/auth/password", "/api/auth/logout")
        if user.must_change_password and request.url.path not in allowed:
            raise HTTPException(403, "Change your temporary password first.")
        yield db, user, login_session


def admin_context(ctx=Depends(context)):
    if ctx[1].role != "admin":
        raise HTTPException(403, "Administrator access required.")
    return ctx


@router.post("/api/auth/login")
async def login(body: Login, request: Request, response: Response):
    check_origin(request)
    username = body.username.strip().lower()
    # Account failures here; trusted nginx applies per-source-IP throttling.
    # Proxy peer IP is shared by every user and must not be used as their IP.
    keys = [digest("account:" + username)]
    ok = False
    with DB.begin() as db:
        # Single worker: login is serialized until its transaction finishes.
        buckets = []
        for key in keys:
            b = db.get(LoginAttempt, key)
            if not b:
                b = LoginAttempt(key=key, count=0, reset_at=time.time() + 900)
                db.add(b)
            if b.reset_at <= time.time():
                b.count = 0
                b.reset_at = time.time() + 900
            if b.count >= 5:
                raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
            buckets.append(b)
        user = db.scalar(select(User).where(User.username == username))
        valid = verify(body.password, user.password_hash if user else DUMMY_HASH)
        ok = bool(user and user.active and valid)
        if not ok:
            for b in buckets:
                b.count += 1
        else:
            for b in buckets:
                b.count = 0
            if hasher.check_needs_rehash(user.password_hash):
                user.password_hash = hasher.hash(body.password)
            # A new login replaces only the current browser's old session.
            old = db.get(LoginSession, digest(request.cookies.get(COOKIE, "")))
            if old:
                db.delete(old)
            db.execute(
                delete(LoginSession).where(LoginSession.expires_at < time.time())
            )
            token = secrets.token_urlsafe(32)
            csrf = digest(token + ":csrf")
            db.add(
                LoginSession(
                    token_hash=digest(token),
                    user_id=user.id,
                    csrf_hash=digest(csrf),
                    expires_at=time.time() + 43200,
                )
            )
            result = {"user": user_dto(user), "csrf_token": csrf}
    if not ok:
        raise HTTPException(401, "Invalid username or password.")
    response.set_cookie(
        COOKIE,
        token,
        max_age=43200,
        httponly=True,
        secure=SECURE,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/api/auth/me")
def me(request: Request, ctx=Depends(context)):
    return {
        "user": user_dto(ctx[1]),
        "csrf_token": digest(request.cookies[COOKIE] + ":csrf"),
    }


@router.post("/api/auth/logout")
def logout(response: Response, ctx=Depends(context)):
    db, _, session = ctx
    db.delete(session)
    response.delete_cookie(
        COOKIE, path="/", secure=SECURE, httponly=True, samesite="lax"
    )
    return {"ok": True}


@router.post("/api/auth/password")
def change_password(body: Password, response: Response, ctx=Depends(context)):
    db, user, _ = ctx
    if not verify(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    if body.current_password == body.new_password:
        raise HTTPException(422, "Choose a different password.")
    user.password_hash = password_hash(body.new_password)
    user.must_change_password = False
    revoke(db, user.id)
    audit(db, user, "password.change", user.id)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/admin/users")
def list_users(ctx=Depends(admin_context)):
    db, _, _ = ctx
    return {
        "users": [user_dto(u) for u in db.scalars(select(User).order_by(User.username))]
    }


@router.post("/api/admin/users")
def add_user(body: NewUser, ctx=Depends(admin_context)):
    db, actor, _ = ctx
    try:
        result = create_user(db, body.username, body.password, body.role)
        audit(db, actor, "user.create", result["id"])
        return result
    except IntegrityError:
        raise HTTPException(409, "Username already exists.")


@router.patch("/api/admin/users/{uid}")
async def edit_user(
    uid: str, body: UserPatch, request: Request, ctx=Depends(admin_context)
):
    from .chat import admission

    async with admission:
        db, _, _ = ctx
        db.expire_all()
        actor, _ = authenticate(db, request.cookies.get(COOKIE))
        if actor.role != "admin" or actor.must_change_password:
            raise HTTPException(403, "Administrator access required.")
        # Serialize changes to all admins so two requests cannot remove the last admin.
        admins = list(
            db.scalars(
                select(User)
                .where(User.role == "admin", User.active == True)
                .order_by(User.id)
                .with_for_update()
            )
        )
        user = db.get(User, uid)
        if not user:
            raise HTTPException(404, "User not found.")
        if (
            user.role == "admin"
            and user.active
            and len(admins) == 1
            and (body.role == "user" or body.active is False)
        ):
            raise HTTPException(
                409, "The last active administrator must remain enabled."
            )
        if body.role is not None:
            user.role = body.role
        if body.active is not None:
            user.active = body.active
        revoke(db, uid)
        audit(db, actor, "user.update", uid)
        db.commit()  # revoke before cancelling tasks/streams
        if not user.active:
            from .chat import stop_user_runs

            await stop_user_runs(uid)
        return user_dto(user)


@router.post("/api/admin/users/{uid}/reset-password")
def reset_password(uid: str, body: ResetPassword, ctx=Depends(admin_context)):
    db, actor, _ = ctx
    user = db.get(User, uid)
    if not user:
        raise HTTPException(404, "User not found.")
    user.password_hash = password_hash(body.password)
    user.must_change_password = True
    revoke(db, uid)
    audit(db, actor, "password.reset", uid)
    return {"ok": True}
