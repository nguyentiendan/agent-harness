"""Authenticated platform entry point. Legacy service modules are not HTTP routes."""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.platform import auth, routes, chat, projects, files, workspaces
from app.billing import routes as billing_routes


@asynccontextmanager
async def lifespan(app):
    from app.config import load_config
    from app.context.policy import load_context_policy

    load_context_policy(load_config(strict=True))
    from app.observability.tracing import interrupt_unfinished

    interrupt_unfinished()
    chat.reconcile()
    from app.cli.cleanup import purge

    purge()
    from app.execution.lifecycle import retry_pending

    async def retry_loop():
        ticks = 0
        while True:
            await asyncio.sleep(5)
            try:
                await asyncio.to_thread(retry_pending)
                ticks += 1
                if ticks % 12 == 0:
                    from app.billing.reconcile import reconcile

                    await asyncio.to_thread(reconcile)
                if ticks % 60 == 0:
                    from app.billing import catalog

                    await asyncio.to_thread(catalog.refresh)
                if ticks >= 720:
                    await asyncio.to_thread(purge)
                    ticks = 0
            except Exception:
                # Keep the durable leases; retry after temporary DB/runner outage.
                pass

    cleanup_task = asyncio.create_task(retry_loop())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    for rid in list(chat.running):
        await chat.stop_run(rid)


app = FastAPI(title="AI Platform API", version="2.0.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(routes.router)
app.include_router(projects.router)
app.include_router(workspaces.router)
app.include_router(files.router)
app.include_router(billing_routes.router)
from app.observability.routes import router as trace_router

app.include_router(trace_router)


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


from app.billing.gateway import GatewayError


@app.exception_handler(GatewayError)
async def gateway_failure(request, error):
    return JSONResponse(status_code=503, content={"detail": str(error)})
