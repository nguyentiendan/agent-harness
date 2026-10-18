"""Authenticated platform entry point. Legacy service modules are not HTTP routes."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.platform import auth, routes, chat


@asynccontextmanager
async def lifespan(app):
    chat.reconcile()
    from app.cli.cleanup import purge

    purge()
    yield
    for rid in list(chat.running):
        await chat.stop_run(rid)


app = FastAPI(title="AI Platform API", version="2.0.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(routes.router)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def invalid_request(request, error):
    # Pydantic includes raw input by default, which can expose passwords.
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Invalid request fields.",
            "errors": [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in error.errors()
            ],
        },
    )
