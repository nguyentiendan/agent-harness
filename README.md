# Python Agent Harness for Long Tasks

A complete 7-layer Agent Harness in Python (LiteLLM, FastAPI, MCP Client) with a
Next.js (TypeScript) dashboard served by Nginx.

## Structure

- `backend/`
  - `app/main.py` — FastAPI composition; ASGI entry point `app.main:app`.
  - `app/api/routes/` / `app/api/schemas.py` — HTTP routes and request models.
  - `app/services/` — run lifecycle, session history, files, approvals and commands.
  - `app/storage/` — persisted sessions/events; `app/config.py` — runtime configuration.
  - `app/agent/` — harness orchestration, agent loop, goals, state, context and hooks.
  - `app/tools/` — registry and native filesystem/shell/web tools.
  - `app/security/` — permission pipeline, path guards and secret redaction.
  - `app/integrations/` — LiteLLM client and MCP client.
  - `app/capabilities/` — skills, memory, tasks, background, scheduler, teams and workflow.
  - `app/observability/` — per-run metrics.
  - `tests/` — isolated regression and package-contract tests.
  - `api_server.py` / `harness_core.py` — thin compatibility entry point/exports.
  - `config.yaml` / `requirements.txt` / `Dockerfile` — configuration and deployment.
- `frontend/` — Next.js 16 chat dashboard (TypeScript), built and served by Nginx.
- `litellm/config.yaml` — LiteLLM Proxy (AI Gateway) config: models, keys, logging.
- `docker-compose.yml` — Runs backend + frontend + LiteLLM gateway + Postgres.
- `workspace_data/` — Workspaces (mounted at `/app/workspaces` in the backend container).
- `data/` — Persistent sessions/events (mounted at `/app/data`).
- `.env.example` — Template for API keys and gateway credentials.

## Security model

- **Workspaces are addressed by name only** — the API rejects absolute or escaping
  paths, so a session can never be pointed outside `WORKSPACES_ROOT`.
- **File tools are confined** to the session workspace (`safe_path`), and paths
  matching `security.deny_file_patterns` (`.env`, secrets, credentials) are blocked.
- **Bash is sandboxed by policy** (`security.bash` in `config.yaml`): commands that
  `cd` outside the workspace, or reference absolute paths outside it, are rejected
  (system/temp prefixes are allow-listed). Shells run with a **sanitized
  environment**, so provider API keys are never visible to the agent.
- **Destructive commands** go through the permission pipeline: hard deny-list
  (`rm -rf /`, `sudo`, ...) always blocked; other destructive commands require
  approval (`execution.approval_mode: ask`) which the chat UI surfaces as Allow/Deny.
- **The backend container runs as an unprivileged user** with
  `no-new-privileges` and all Linux capabilities dropped.

## Observability: LiteLLM gateway

All model calls go through the LiteLLM Proxy, which provides an admin dashboard:

- UI: http://localhost:4000/ui — login `admin` / your `LITELLM_MASTER_KEY`.
- **Logs** — spend, tokens, latency, key and team for every request (who did what).
- **Virtual Keys** — issue one key per user/team, each with its own budget and rate
  limit; set it as `LITELLM_VIRTUAL_KEY` so the backend uses it.
- **Teams & Organizations**, **Usage**, **Session logs**.
- Provider API keys live only in the gateway — the backend never receives them.

## Configuration

`backend/config.yaml` is the single source of truth for backend defaults:
workspace layout, LLM models, execution limits and MCP servers. The API reads it
on startup (override the path with the `HARNESS_CONFIG` env var) and exposes it
via `GET /api/config`.

## Quick Start (Docker)

1. Copy `.env.example` to `.env` and fill in your API keys.
2. Build and run:
   ```bash
   docker compose up --build
   ```
3. Open the harness chat at http://localhost:3000, the API at http://localhost:8000,
   and the LiteLLM admin dashboard at http://localhost:4000/ui.

## Local development

Backend:
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```
The dashboard defaults to same-origin `/api`; set the "API base URL" field to
`http://localhost:8000` when running the frontend outside of Docker.

## Backend tests

Run from the backend directory:

```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary data/workspace directories and fake model responses. They do
not read or delete real session history or call model providers. In Docker:

```bash
docker compose exec -T backend python -m unittest discover -s tests -v
```

## Conversations

A session contains multiple user messages (runs). Follow-ups retain the original
title and workspace and use previous user/assistant messages as context. SSE's
`after` cursor skips previously displayed events. Deleting a stopped/completed
session removes its history, while keeping workspace files. Authentication and
per-user workspace isolation are not implemented yet.
