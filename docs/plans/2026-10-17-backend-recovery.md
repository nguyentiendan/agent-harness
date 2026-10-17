# Session recovery and backend refactor

## Scope agreed with user
Restore multi-message sessions and deletion first, then reorganize the backend
without changing public API URLs. Update docs/STATUS.md and commit each milestone.
Keep Tailwind UI, slash commands, metrics, redaction, approvals and MCP behavior.
Do not implement Auth or change deployment security policy in this change.

## Implementation
- [x] Reproduce the six existing regression failures using temporary data.
- [x] Restore persisted user events, conversation history, stable title/workspace,
      conflict protection, per-run metrics and cursor-based SSE replay.
- [x] Restore session deletion (running sessions protected; workspace files retained).
- [x] Connect UI send/load/delete flows, preserving commands and metrics.
- [x] Run regression tests and frontend build; update STATUS and commit milestone 1.
- [ ] Extract app/config.py, API schemas/routes, session storage, run/approval services,
      metrics, agent orchestration/session/goals/context/state/hooks, tool registry
      and filesystem/shell/web tools, permission/path guards, LLM/MCP integrations,
      and existing capabilities into packages. Move tests to backend/tests.
- [ ] Change Docker and documented entry points to app.main:app; retain thin legacy
      launch/import adapters where useful, with no duplicated implementation.
- [ ] Verify imports, API contracts, session/history/SSE, filesystem guards,
      commands/metrics/redaction and tool registrations after extraction.
- [ ] Build Docker images, update running services, verify HTTP, update STATUS and
      README with actual test results and code map; commit milestone 2.

## Review focus
- Follow-up after backend restart must preserve history and workspace.
- Old sessions without user events must migrate without duplicate messages.
- Stop/delete must not race a still-finalizing run.
- SSE replay must not close on a previous run's terminal event.
- Refactor must not bypass permission checks or change tool registrations.

## Validation
Use unittest with temporary directories and fake LLM/harness execution; no real
provider calls or destructive commands. Use Docker build for production TypeScript
and dependency checks. Verify live HTTP after deployment. Browser interaction
verification is limited by unavailable Computer Use permission.
