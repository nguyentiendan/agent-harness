# AI Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement task-by-task. Steps use checkbox syntax. Không tự bắt đầu implementation từ tài liệu này.

**Goal:** User dùng chat/project cá nhân với Admin/User, file bền vững và Docker sandbox, không cần workspace assignment.

**Architecture:** FastAPI giữ auth, policy, agent loop và LLM calls; PostgreSQL giữ ownership/events. Runner riêng điều phối Docker chỉ mount scope của chat/project; frontend dùng same-origin cookie.

**Tech Stack:** FastAPI/Python, PostgreSQL + SQLAlchemy/Alembic (đề xuất mới), Next.js/React/Tailwind hiện có, Docker, LiteLLM.

**Spec:** [2026-10-17-ai-platform-design.md](../specs/2026-10-17-ai-platform-design.md)

**Status:** User yêu cầu thực hiện mốc A ngày 2026-10-18. Task 2 import legacy được hủy theo chỉ dẫn: reset dữ liệu test, không backup/migration dữ liệu; schema Alembic vẫn thực hiện. User đã yêu cầu viết cả spec và plan trong lượt này; không coi đó là phê duyệt các mặc định kỹ thuật chưa thảo luận hoặc yêu cầu deploy.

## Execution record — 2026-10-18

Mốc A đã triển khai trên branch `feat/auth-ownership`; frontend tương tác do anh test, backend 35 tests đạt cả local và Docker/PostgreSQL.

| Task | Kết quả thực tế |
|---|---|
| 1 | Hoàn tất DB/schema/bootstrap; tables project/scope/usage chưa cần được chuyển sang các mốc B–D. |
| 2 | Import bị hủy theo yêu cầu user; đã reset chỉ dữ liệu test harness. Session repository DB nằm trong platform chat/routes. |
| 3 | Hoàn tất auth, admin user management, cookie/CSRF, audit, throttle và revoke. |
| 4 | Hoàn tất ownership mọi API, idempotency, SSE; single-worker admission lock + persisted run state thay distributed lease. Không bật tools. |
| 5 | Code/UI và production build hoàn tất; browser acceptance chờ user theo yêu cầu mới. |
| 6–11 | Chưa thực hiện. |

Ruling: gom public platform boundary vào các module nhỏ `backend/app/platform/` thay vì sửa trực tiếp legacy harness services; legacy không expose HTTP. Tránh vô tình chạy công cụ chưa sandbox. Không worktree riêng: branch mới trong checkout chung để anh test Docker/IDE tại đường dẫn quen thuộc. CLI/schema migrations vẫn có; data import/backup không có. Tài liệu triển khai cụ thể: [runbook](../../runbooks/auth-milestone-1.md).

Checklist thiết kế gốc bên dưới được giữ để đối chiếu; execution record này ghi các điều chỉnh đã thực hiện. Không dùng những bước migration/browser automation cũ để yêu cầu lại phần user đã bỏ hoặc nhận test.

## Global constraints

- Chỉ `admin`/`user`; không workspace membership, không sharing/impersonation.
- Owner từ principal server-side; chat độc lập hoặc project xác định scope bất biến.
- Một backend worker; sandbox chưa sẵn sàng thì tool execution fail closed.
- Không shell trong backend khi multi-user; không Docker socket/LLM key trong sandbox.
- Giữ nhiều message/chat, event cursor và dữ liệu cũ; migration copy, không xóa nguồn.
- Giới hạn mặc định, auth lifetime, trash và network theo spec; ghi rõ hạn chế offline/disk quota trong UI/docs.
- Mỗi task hoàn tất phải cập nhật `docs/STATUS.md` với bằng chứng kiểm tra và giới hạn, rồi commit phần liên quan. Không commit secrets/data.

## Review focus

1. User bị disable khi SSE/run đang mở: task 3 và 7 kiểm tra revoke/stop trong 5 giây.
2. Hai chat cùng project cùng start: task 4 và 9 kiểm tra đúng một lease, request còn lại 409.
3. Symlink bị đổi trong lúc download: task 6 kiểm tra không mở file ngoài scope kể cả race.
4. Backend chết sau tạo container trước persist kết quả: task 7 kiểm tra reconciliation và không process mồ côi.
5. Import chạy lại sau copy dở: task 2 kiểm tra idempotency, count/checksum và rollback.

## Bản đồ thay đổi

Giữ packages hiện có, thêm module nhỏ theo trách nhiệm, không gom vào `harness.py` hoặc `page.tsx`.

| Nhóm | File mới / trách nhiệm |
|---|---|
| DB | `backend/app/storage/db.py`, `models.py`, `repositories.py`; `backend/alembic/`, `backend/alembic.ini` |
| Identity | `backend/app/security/auth.py`, `authorization.py`; `backend/app/services/users.py`; `backend/app/api/routes/auth.py`, `admin.py` |
| Migration/admin CLI | `backend/app/cli/bootstrap_admin.py`, `import_legacy.py` |
| Scope/project | `backend/app/services/scopes.py`, `projects.py`; `backend/app/api/routes/projects.py` |
| Sandbox client | `backend/app/execution/types.py`, `client.py`, `lifecycle.py`; agent tools nhận ExecutionContext |
| Runner | `sandbox_runner/app/{main,schemas,policy,docker_runtime,files}.py`, `sandbox_runner/Dockerfile`, `sandbox_runner/requirements.txt` |
| Sandbox image | `sandbox/Dockerfile`; image có Python/Node, chạy non-root, pin version/digest khi build |
| Usage | `backend/app/services/limits.py`, `usage.py`; `backend/app/integrations/litellm_admin.py` |
| UI | `frontend/app/components/auth/`, `admin/`, `projects/`; `frontend/lib/auth.ts`; cập nhật `lib/api.ts` và `app/page.tsx` |

Module mới ở bảng là đường dẫn dự kiến, chưa tồn tại. Mỗi task bên dưới xác định file sửa và test đi kèm. Interfaces dùng UUID/ID string ở HTTP; typed internal DTO, không raw Docker config.

## Mốc A — Auth và dữ liệu cá nhân

### Task 1: DB schema và bootstrap Admin

**Files:** tạo storage/DB, models, repositories, Alembic và `cli/bootstrap_admin.py` như bảng; sửa `backend/requirements.txt`, `app/config.py`, `docker-compose.yml`, `.env.example`; test `backend/tests/test_platform_db.py`.

**Interfaces:** `get_db() -> Session`; `Principal(user_id: str, role: str)`; `create_user(username, password, role) -> User`; `bootstrap_admin(username, password) -> User`. Model fields/constraints theo spec §4. Repository chịu transaction, không commit rải rác từ route.

- [ ] Viết test `test_unique_normalized_username`, `test_scope_exactly_one_parent`, `test_password_never_stored_plaintext`, `test_bootstrap_not_overwrite_existing_admin`: duplicate bị reject, scope sai parent bị reject, hash khác password, không reset Admin hiện có.
- [ ] Chạy từ backend: `.venv/bin/python -m unittest discover -s tests -p 'test_platform_db.py' -v`; xác nhận fail vì module/schema chưa có.
- [ ] Tạo migrations và repository, thêm platform-db riêng, CLI đọc password qua stdin; dependency versions pin sau kiểm tra tương thích. Test DB riêng qua `TEST_DATABASE_URL`, cấm trỏ DB production.
- [ ] Chạy migration upgrade trên DB rỗng và lại lần hai, chạy test đạt; kiểm tra LiteLLM DB không bị thay đổi.
- [ ] Update STATUS và commit `feat: add platform database and admin bootstrap`.

### Task 2: Import legacy và repository session — SUPERSEDED 2026-10-18

**Files:** tạo `cli/import_legacy.py`; sửa `storage/sessions.py`, `services/sessions.py`; test `tests/test_legacy_import.py`.

**Interfaces:** `import_legacy(source_data, source_workspaces, owner_id, dry_run=True) -> ImportReport`; `SessionRepository.list_for(principal)`, `get(session_id)`, `append_events(session_id, run_id, events)`. Giữ session ID/title/events, sequence tăng; JSON chỉ còn nguồn import.

- [ ] Test `test_shared_workspace_becomes_one_project`, `test_partial_import_retry`, `test_symlink_outside_root_rejected`, `test_source_unchanged`: hai chat cùng project import, chạy hai lần không duplicate, source checksum không đổi, không copy symlink escape.
- [ ] Chạy `.venv/bin/python -m unittest discover -s tests -p 'test_legacy_import.py' -v`; xác nhận fail.
- [ ] Implement dry-run, manifest/checksum, transaction, maintenance flag và owner bắt buộc; copy file trước cutover, chỉ activate import khi kiểm kê đủ. Fixture có workspace rỗng và event legacy thiếu user event.
- [ ] Chạy test đạt; diễn tập backup/restore trên dữ liệu tạm, chứng minh file/hội thoại và ID giữ nguyên. Chưa chạy import dữ liệu thật.
- [ ] Update STATUS và commit `feat: migrate legacy sessions into owned projects`.

### Task 3: Authentication, tài khoản và policy tập trung

**Files:** tạo auth/authorization/users/routes theo bảng; sửa `app/main.py`, `api/schemas.py`; tests `tests/test_auth.py`, `test_authorization.py`.

**Interfaces:** `current_principal(request) -> Principal`, `require_admin(principal)`, `authorize_session(principal, session_id) -> SessionRecord`, `authorize_project(principal, project_id) -> ProjectRecord`. Auth routes `POST /api/auth/login`, `/logout`, `/password`; `GET /api/auth/me`; admin `GET/POST /api/admin/users`, `PATCH /api/admin/users/{id}`, `POST .../{id}/reset-password`.

- [ ] Test `test_login_logout_cookie`, `test_csrf_and_origin`, `test_login_throttle`, `test_password_reset_revokes_all`, `test_last_admin_protected`, `test_disable_revokes_stream_access`: 12h expiry, HttpOnly, 401 after logout/reset, 403 CSRF, throttle lần thứ 6/15 phút, không disable Admin cuối.
- [ ] Chạy unittest pattern `test_auth*.py`, xác nhận fail.
- [ ] Implement token hash/Argon2id, same-origin CSRF, throttle, temporary password flow, audit admin actions; `must_change_password` chỉ cho me/password/logout. Sanitize config response. Session validation định kỳ dùng cho SSE.
- [ ] Chạy tests đạt và chứng minh password/token không vào log/JSON response; cookie Secure trong profile HTTPS.
- [ ] Update STATUS và commit `feat: add cookie authentication and admin user management`.

### Task 4: Ownership toàn API và run admission

**Files:** sửa toàn bộ `api/routes/{runs,sessions,approvals,commands,config,workspaces}.py`, services tương ứng, `services/runtime.py`, `api/schemas.py`; tạo `services/scopes.py`; test `tests/test_owned_api.py`.

**Interfaces:** `resolve_scope(principal, session_id) -> ExecutionScope`; `acquire_run(principal, session_id, request_id) -> RunRecord`; `release_run(run_id, status)`. `POST /api/sessions {project_id?, title?}` tạo server ID; start dùng ID đã tồn tại, không còn `workspace_path`. `request_id` chống retry tạo hai message/run; cùng key khác body trả 409.

- [ ] Test matrix A/B/Admin cho list/get/delete/status/files/SSE/stop/approval/command/config; assert User A nhận 404 khi dùng ID của B, Admin action có audit, owner payload bị reject.
- [ ] Test `test_concurrent_scope_lease`, `test_start_retry_one_message`, `test_followup_keeps_session`, `test_unisolated_tools_disabled`; chạy pattern `test_owned_api.py` thấy fail.
- [ ] Áp dụng policy trước mọi side effect, DB lease unique scope + per-user/global admission, gỡ public workspace routes. Giữ SSE sequence/cursor; recheck revoke tối đa 5s. Tool execution mặc định disabled trước mốc B.
- [ ] Test đạt, cập nhật regression session/delete hiện có sang principal/DB fixtures; chạy toàn bộ backend unittest.
- [ ] Update STATUS và commit `feat: enforce ownership across chat APIs`.

### Task 5: UI login và quản trị user

**Files:** tạo `components/auth/LoginForm.tsx`, `PasswordForm.tsx`, `AccountMenu.tsx`, `components/admin/UsersPanel.tsx`, `lib/auth.ts`; sửa `lib/api.ts`, `app/page.tsx`, Settings modal.

**Interfaces:** shared API client gửi cookie/CSRF cùng origin, error có status; auth state `loading | anonymous | password_change | authenticated`. Không giữ session data khi đổi tài khoản.

- [ ] Tạo integration cases cho login→chat→logout→Back, password tạm, disable, User không có Admin menu và API vẫn từ chối. Dùng browser test runner phù hợp repo lúc triển khai; ghi command vào STATUS.
- [ ] Implement UI bằng controls/dialog hiện có; bỏ workspace assignment và API base URL tùy chỉnh. Sidebar tạm chỉ chats; imported project hiển thị nhãn cho đến mốc C.
- [ ] Chạy browser cases, `npm --prefix frontend run build -- --webpack`; xác nhận refresh login không nháy dữ liệu người cũ, SSE đóng khi logout/401.
- [ ] Update STATUS và commit `feat: add sign-in and user administration UI`.

## Mốc B — Docker sandbox và files

### Task 6: Runner và file gateway

**Kết quả 2026-10-18:** hoàn tất pilot offline; xem `docs/runbooks/sandbox-milestone-2.md`. Dùng named volume riêng theo chat; image tag resolve sang immutable ID khi cấp container. Hard disk quota chưa đạt, giữ rollout gate. Tests thực tế ở `sandbox_runner/tests/{test_files,test_runtime,test_docker}.py` và `backend/tests/test_execution.py`.

**Files:** tạo execution types/client, runner package và sandbox image theo bảng; sửa Compose/config; tests `backend/tests/test_runner_client.py`, `sandbox_runner/tests/test_runner.py`.

**Interfaces:** `ExecutionContext(owner_id, scope_id, run_id)`; `RunnerClient.ensure(ctx) -> SandboxHandle`, `execute(ctx, command, timeout_seconds) -> ToolResult`, `file_operation(ctx, operation, relative_path, payload) -> FileResult`, `destroy(run_id)`. Internal endpoints `/runs/{id}/ensure`, `/exec`, `/files`, DELETE `/runs/{id}`; service auth bắt buộc, scope mapping từ registry tin cậy, không client host path/image.

- [x] Test runner rejects unknown run/scope, arbitrary mount/image/options; symlink/traversal/race không đọc file ngoài scope; timeout/OOM/unavailable là lỗi có loại, không fallback.
- [x] Chạy unit tests với fake Docker adapter, xác nhận fail rồi implement restricted profiles, mount, secret-free environment, filesystem gateway và safe descriptor-based opens. Backend Files dùng gateway, không raw host read.
- [x] Docker integration: A ghi marker, B không đọc được; `/proc`, mount, environment không lộ backend key/socket; shell ghi ngoài mount không sửa host; CPU/RAM/PID constraints inspect đúng; network none không kết nối internal services.
- [x] Kiểm tra volume mapping thật giữa Compose/daemon và Docker Desktop; pin sandbox image digest, tài liệu hóa platform support. Hard disk quota chỉ đánh dấu đạt sau host test, chưa có thì pilot-only.
- [x] Update STATUS và commit `feat: add restricted Docker sandbox runner`.

### Task 7: Chuyển toàn bộ tool execution và cleanup

**Điều chỉnh đã triển khai:** public API dùng `app/execution/{agent,client,lifecycle}.py` với 5 tool an toàn; các module legacy bên dưới vẫn bị loại khỏi execution path thay vì chuyển toàn bộ capability. Kiểm thử same-project/checkpoint dời sang mốc C vì chưa có project; hiện scope/chat riêng. 40 backend + 3 file + 2 runner unit + 5 Docker tests đạt, thêm smoke SIGKILL backend worker với runner/Postgres thật. Commit gộp task 6–7 vì lifecycle/gateway phụ thuộc chung schema.

**Files:** sửa `agent/{harness,workspace,state}.py`, `tools/{registry,shell,filesystem}.py`, `capabilities/{background,memory,tasks,skills,scheduler,teams,workflow}.py`, `integrations/mcp.py`, `services/{runs,files,approvals}.py`; tạo `execution/lifecycle.py`; tests `tests/test_execution_lifecycle.py`.

**Interfaces:** ToolRegistry nhận `ExecutionContext` + `RunnerClient`; `reconcile_runs() -> ReconcileReport`; state/checkpoint theo run, project file theo scope. MCP/tool chưa có adapter an toàn không được đăng ký.

- [x] Test `test_no_host_shell_paths`, `test_background_killed_on_stop`, `test_disable_stops_run`, `test_restart_cleans_orphan`, `test_same_project_separate_checkpoints`, `test_plain_chat_no_container`.
- [x] Chạy tests thấy fail; implement lazy ensure, cancellation/finally, kill background, idempotent destroy/release; reconcile startup trước nhận run. Audit mọi capability, disable bypass chưa chuyển xong, công bố rõ trong config UI.
- [x] Test Docker thật: kill backend giữa run, restart, không process mồ côi; file marker còn; container mất thì run failed, không lặp side effect; approval không mở quyền mount/network.
- [x] Regression 21 tests cũ (đã adapt) và tests mới đạt; cập nhật STATUS capability nào disabled, commit `feat: route agent tools through isolated execution`.

## Mốc C — Project cá nhân

### Task 8: Project API và file lifecycle

**Files:** tạo services/routes projects; sửa schemas, scopes, files, sessions; tạo `services/cleanup.py`; tests `tests/test_projects.py`, `test_file_lifecycle.py`.

**Interfaces:** `create_project(principal, name, instructions) -> ProjectRecord`, `update_project(...)`, `trash_project(...)`; REST `/api/projects` GET/POST, `/{id}` PATCH/DELETE. Session create nhận project_id; membership không tồn tại. Files list/read/upload/download/delete theo authorized scope. `purge_expired_trash(now) -> CleanupReport` chạy bằng maintenance CLI định kỳ, idempotent.

- [ ] Test hai chat cùng project thấy cùng file nhưng không trộn history; khác project cùng user không thấy file; ID project của B bị 404; moving existing chat bị reject.
- [ ] Test delete chat project giữ file, delete standalone/project trash 7 ngày, purge đúng hạn; busy trả 409; upload >25 MiB bị reject trong streaming, preview >1 MiB bị truncate có nhãn, binary không render HTML.
- [ ] Chạy tests thấy fail; implement API/lifecycle và instructions vào prompt từng run; giữ instructions là user data, không quyền thay auth/tool policy.
- [ ] Chạy tests đạt và import fixtures từ task 2 hoạt động qua project API; update STATUS, commit `feat: add private projects and persistent file lifecycle`.

### Task 9: Project/file UI

**Files:** tạo `components/projects/ProjectList.tsx`, `ProjectDialog.tsx`; sửa sidebar trong page, Files modal, lib/api.ts; frontend integration tests.

**Interfaces:** New chat có optional project_id, project selection chỉ lọc chat chủ sở hữu; UI server error 409 giữ draft và thông báo project đang chạy.

- [ ] Browser cases: tạo project→hai chat→file chung→refresh; standalone file riêng; A logout/B login không thấy project A; Admin All chats có owner rõ.
- [ ] Implement project create/rename/instructions/delete, upload/download/trash copy; giữ sidebar rail, keyboard focus, light/dark và mobile dialog. Không thêm chat chia sẻ.
- [ ] Run browser cases và production build; xác nhận hai tab start cùng project chỉ một run, draft của request bị chặn còn nguyên.
- [ ] Update STATUS và commit `feat: add personal projects and file management UI`.

## Mốc D — Quota, vận hành và rollout

### Task 10: LiteLLM identity và admission limits

**Files:** tạo limits/usage/litellm_admin modules; sửa `integrations/llm.py`, `agent/harness.py`, `services/runs.py`, admin routes; tests `tests/test_usage_limits.py`.

**Interfaces:** `get_user_gateway_key(user_id) -> Secret`, `authorize_model_call(principal, model, run_id) -> Reservation`, `record_usage(reservation, usage)`, `reconcile_usage(user_id, period)`. Key lưu mã hóa server-side, key mã hóa từ secret deployment; không nằm trong DTO/log. HTTP admin `/api/admin/users/{id}/limits`, user `/api/usage`.

- [ ] Kiểm tra API của LiteLLM version thực tế, pin image; contract tests key provision/rotate/revoke/model budget. Không dựa trực tiếp schema DB LiteLLM.
- [ ] Tests: disallowed reviewer/primary rejected; primary/reviewer/subagent đều user identity; cost unknown=pending; retry usage không double-count; month UTC rollover; gateway unavailable fail closed; no master-key fallback.
- [ ] Implement server-only user keys, per-call reservations, policy model override (sửa việc `default_model` chưa được áp dụng đúng trong Harness), usage reconciliation và resource limits đúng spec.
- [ ] Test ngân sách nhỏ với gateway kiểm soát, concurrent requests/stream cancellation; đo overshoot thay vì khẳng định budget tuyệt đối; backend suite đạt.
- [ ] Update STATUS và commit `feat: enforce user model budgets and usage attribution`.

### Task 11: Admin limits, rollout và recovery

**Files:** tạo `components/admin/LimitsPanel.tsx`, `UsagePanel.tsx`; sửa Compose/nginx/config, README, STATUS; tạo `docs/runbooks/platform-rollout.md`; tests `tests/test_platform_acceptance.py`.

- [ ] UI limits/usage: validation, pending cost rõ, User chỉ thấy của mình; Admin chỉnh limits nhưng không sửa Docker options/raw path.
- [ ] Acceptance A/B/Admin qua API và browser; hostile shell cross-scope; disable trong run; disk-full/timeout/runner-down; project lease; upload symlink; migration dry-run và repeated import.
- [ ] Build backend/frontend/runner/sandbox; backend full suite, runner suite và Docker integration suite phải đạt. Pin command thực tế trong runbook; không đánh dấu browser test nếu thiếu quyền/tool.
- [ ] Runbook: backup/restore, bootstrap, import owner mapping, maintenance, cleanup scheduler, HTTPS/same-origin, đóng public DB/backend/LiteLLM/runner ports, secrets không mặc định. Chỉ nginx công khai theo cấu hình nội bộ.
- [ ] Chốt host resource/disk quota bằng bằng chứng; không đạt hard quota thì ghi pilot-only và chưa rollout nhiều user. Thực hiện cutover dữ liệu thật theo runbook ở bước triển khai riêng, không gộp vào viết spec/plan.
- [ ] Update STATUS kèm số test, giới hạn, kết quả rollback rehearsal; commit `feat: complete platform administration and rollout checks`.

## Kiểm tra và handoff

Mỗi task backend chạy test đích trước, rồi `.venv/bin/python -m unittest discover -s tests -v` khi thay interface chung. PostgreSQL integration dùng DB test riêng. Runner suite chạy từ `sandbox_runner` bằng `python -m unittest discover -s tests -v`; Docker integration phải dùng labels/volume test riêng và cleanup, không dùng file thật của user. Frontend dùng `npm --prefix frontend run build -- --webpack` và browser integration ở các task UI. Không cần chạy code tests cho lần chỉ viết tài liệu này.

Đã đối chiếu coverage: auth/policy→1–5; migration→2; sandbox/file security→6–7; projects/lifecycle→8–9; quotas→10; vận hành/release gates→11. Các lựa chọn chưa được user chốt riêng nằm rõ ở spec §2, không giả làm implementation đã hoàn thành.

Bước tiếp theo đề xuất: review spec/plan, xác nhận mặc định username/password; triển khai task 1–5 trước, tiếp đó 6–7 trước khi mở tool execution nhiều user. Thực hiện tuần tự trong session hiện tại phù hợp các interface phụ thuộc nhau; không yêu cầu nhiều agent để bắt đầu.
