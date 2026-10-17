"""FastAPI application composition and public ASGI entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import approvals, commands, config, runs, sessions, workspaces

app = FastAPI(title="Agent Harness API", description="Backend API wrapper for 7-Layer Agent Harness (Long Tasks)", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])
for module in (config, workspaces, sessions, runs, approvals, commands):
    app.include_router(module.router)
