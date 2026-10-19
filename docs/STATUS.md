# Agent Harness — Status & Roadmap

> Tổng hợp toàn bộ quá trình refactor từ đầu session.
> Snapshot tại thời điểm viết; cập nhật khi có thay đổi.

## Cập nhật 2026-10-19 — Mốc 4: Limits/usage + LiteLLM identity

- **Chức năng:** budget USD/tháng UTC, model allowlist, concurrent runs 1–4, output cap; Admin **AI limits**, User **My usage**. Chưa gán budget/models thì chặn AI kể cả Admin; không tự cấp ngân sách mặc định.
- **Gateway:** key riêng user/tháng/revision, Fernet encryption server-side; management master không dùng completion, legacy cũng bỏ master fallback. LiteLLM 1.105.1 pin digest, không retry call tự động, tắt prompt logging mới. Rotate/disable queue block key; cấp key mới khi cần.
- **Accounting:** reserve full model context trước mỗi call, cost chưa biết giữ pending; match spend log theo response ID + user; idempotent. Admin reconciliation có audit, pending phân trang tất cả tháng. Đã sửa hai finding review: pending cũ mất khả năng truy cập và sweep bị starvation sau 100 lượt.
- **Kiểm chứng:** 59 backend tests local/PostgreSQL, 4 worker + 3 runner + 6 Docker isolation; gateway contract/mock integration, migration schema lặp, DB test dump/restore đạt. Mock cost 0.00001335 USD, overspend 0; test overspend mô phỏng ghi nhận/chặn lượt sau. Frontend Webpack production build/TypeScript đạt. Smoke ban đầu phát hiện request mock bị bỏ qua nên đã chuyển sang model mock endpoint loopback và invalid key; bộ cuối không gọi provider.
- **Build/deploy:** cả 4 image build đạt, đã deploy Docker local; backend/frontend HTTP 200, anonymous usage/admin API 401. Trước/sau đều 1 user và 1 chat; gateway xác minh được giá/context của 4 model.
- **Vận hành:** schema `20261019_billing`; không reset dữ liệu/password hoặc import data cũ. Giữ gateway secrets đang dùng. Hướng dẫn [pilot/limits/recovery](runbooks/platform-rollout.md).
- **Giới hạn:** vẫn **pilot-only**, chưa hard disk quota, HTTPS/domain công ty, full volume/host recovery hoặc kiểm tra browser thao tác; anh test frontend. Chưa mở legacy reviewer/subagent/MCP. Không coi rollout rộng hoàn tất.

---

## Cập nhật 2026-10-19 — Mốc 3: Project cá nhân + file lifecycle

- **Project:** tạo/đổi tên/instructions/xóa, sidebar Projects và Standalone chats; mỗi project chung scope/file, từng chat giữ lịch sử riêng. Instructions đưa vào prompt từng run như dữ liệu user, không thay policy. Không move chat giữa project.
- **Files:** upload/download binary tối đa 25 MiB với streaming và disk spool bounded; preview text 1 MiB, binary attachment; replace/delete có xác nhận. Helper vẫn readonly rootfs; truyền stdin/stdout, không raw host file path hoặc archive extraction. Project có thể upload trước khi tạo chat.
- **Quyền và lifecycle:** User chỉ dữ liệu của mình, Admin quản lý/xem có audit, không impersonation. Run/transfer/deletion dùng chung scope admission; busy trả 409 và UI giữ draft. Xóa chat project giữ file; project delete trash toàn bộ chat/file 7 ngày. File delete riêng vĩnh viễn có cảnh báo.
- **DB:** migration `20261019_projects` giữ ID scope/volume hiện hữu và backfill chat chưa có scope. Không reset dữ liệu hoặc password. Test schema PostgreSQL tạm chứng minh upgrade lặp giữ owner/file marker/counts.
- **Kiểm chứng:** 47 backend tests local/PostgreSQL; 4 worker + 3 runner unit + 6 Docker tests. Smoke HTTP→runner→Docker thật đạt: binary round-trip, project shared files, agent bash thật, history riêng, A/B/Admin, trash purge. Docker exact 25 MiB round-trip và oversized reject đạt. Reviewer phát hiện transaction upload/Admin update đã sửa, regression đạt. Frontend production build + TypeScript đạt; đã deploy local, backend/runner healthy, frontend 200 và anonymous Projects 401. DB revision `20261019_projects`; counts users/chats trước/sau không đổi.
- **Bàn giao:** anh kiểm tra thao tác frontend/provider thật theo [checklist mốc 3](runbooks/projects-milestone-3.md). Sandbox vẫn offline, pilot-only do chưa có hard disk quota. Mốc tiếp theo là Limits/usage + LiteLLM identity; chưa bật budget enforcement ở mốc 3.

---

## Cập nhật 2026-10-18 — Mốc 2: runner/sandbox + file gateway + cleanup

- Anh đã test login/logout/create user mốc 1 và đồng ý triển khai mốc 2. Không reset dữ liệu hiện tại.
- **Execution:** Docker container riêng theo run, cấp khi tool đầu tiên được gọi; file bền vững theo chat trong volume riêng. Text chat không tạo container. Chỉ bật bash/read/write/edit/list; legacy host tools và MCP/background scheduler/subagent/workflow chưa được đăng ký.
- **Isolation:** offline, non-root, readonly root, no-new-privileges/drop caps, 1 CPU/1 GiB RAM/128 PIDs, timeout command 120s/run 15 phút. Backend không có socket Docker; runner nội bộ có service token và registry owner/scope bất biến. Gateway readonly kiểm tra traversal/symlink/FIFO; tối đa 2 helper đồng thời.
- **Cleanup:** lease DB được giữ đến khi runner xác nhận destroy; retry 5s, reconcile startup, runner watchdog/orphan sweep. Stop không tái tạo sandbox do thread ensure trễ. File giữ qua run/restart; trash purge sau 7 ngày chạy mỗi giờ, kể cả khi tool flag đã tắt. Migration chỉ thêm bảng scope/lease.
- **Kiểm chứng:** 40 backend tests đạt cả local và Docker/PostgreSQL test riêng; 3 gateway tests, 2 runner unit tests, 5 Docker integration tests đạt (cross-user, constraints/network, timeout/background, OOM/container loss, output cap, symlink/FIFO, restart/persistence/purge). Smoke thật SIGKILL backend worker → reconcile → file còn → purge đạt; runner anonymous 401 và arbitrary mount/image 422. Docker SDK có ResourceWarning socket đã đóng trong test; chưa thấy lỗi functional. Frontend/backend/runner production build đạt; đã deploy local, migration `20261018_execution`, backend/runner health đạt, số users/chats trước/sau giữ nguyên. Provider thật và thao tác frontend chờ anh test.
- **Giới hạn:** pilot-only vì chưa có hard disk quota; low-disk guard không thay thế quota. Offline chưa cài package/tải Internet. Project scope UI/quota ở mốc tiếp theo. [Runbook và checklist frontend](runbooks/sandbox-milestone-2.md).

---

## Cập nhật 2026-10-18 — Mốc 1 hoàn tất, chờ anh test frontend

- **Auth + Admin/User:** login/logout bằng cookie HttpOnly + CSRF/Origin; mật khẩu Argon2id; hết hạn phiên 12h; tạo user với password tạm bắt đổi; đổi/reset password, enable/disable và đổi role. Không cho khóa/hạ quyền Admin hoạt động cuối cùng.
- **Ownership:** User chỉ list/read/stop/delete chat của mình; Admin có All users’ chats, owner rõ ràng và audit khi quản trị. Admin không gửi message dưới danh tính người khác. Kiểm tra quyền cả SSE, commands, approvals và Files.
- **DB:** PostgreSQL `platform-db` / volume `platform_db` riêng; Alembic tạo schema. Chat, runs, events, users, login sessions, audit và throttle lưu DB. Không còn JSON persistence ở public API. Gửi nhiều message giữ cùng chat/title; request ID chống gửi lặp; SSE có cursor và kiểm tra revoke khi stream đang mở.
- **API/module:** `backend/app/platform/{db,identity,auth,chat,routes}.py`; CLI bootstrap Admin và cleanup riêng. Legacy agent/services/tools vẫn giữ để tích hợp sandbox, nhưng không được mount thành HTTP API. Một backend worker, admission tuần tự trong process; không hỗ trợ scale worker/replica.
- **UI:** login, bắt đổi password, menu account/logout/password, Manage users (create/role/disable/reset), My chats/All users’ chats. Bỏ workspace assignment và API base URL tùy chỉnh; Settings lấy model được server bật. Giữ sidebar rail, dark/light, modal. Anh sẽ test tương tác frontend như đã thống nhất.
- **Giới hạn mốc 1:** text chat qua LiteLLM hiện hữu; shell/files/MCP/subagent/tools bị khóa đến mốc sandbox. Project UI/schema và user-specific LiteLLM quota/key ở mốc sau. Metrics agent/tool cũ không áp dụng cho text-only run mới.
- **Dữ liệu:** đã xóa dữ liệu test cũ trong `data/` và `workspace_data/` theo chỉ dẫn của anh, không backup/import/migration dữ liệu. Không đụng volume/database LiteLLM. Task import legacy trong plan đã superseded; Alembic vẫn quản lý schema mới.
- **Kiểm chứng:** **35 backend unittest đạt trên local Python 3.14 và Docker Python 3.12 + PostgreSQL test riêng**, gồm isolation A/B/Admin, CSRF/logout/expiry/reset/disable, last Admin, session continuity/idempotency, start/revoke race, Admin queued revoke, SSE cursor/revoke, restart và trash retention. Alembic upgrade chạy lặp đạt. Fake model trong tests; chưa kiểm chứng provider thật lần này.
- **Build/deploy:** frontend production build (local Webpack + Docker Turbopack) và TypeScript đạt; backend/frontend đã chạy bản mới. Smoke test qua nginx: frontend 200, anonymous API 401, login/me 200, password-tạm restriction 403, logout thu hồi thành công. Backend debug port chỉ bind localhost, IP login throttle ở nginx; account throttle ở backend.
- **Review:** đã xử lý race start/delete và disable/start, cached Admin chờ lock, stale auth polling và retry ID của frontend. Lỗi validation không echo password. Secret Admin tạm nằm `.env.admin` (gitignored, mode 0600), không trong Git/tài liệu.
- **Bàn giao:** [runbook và checklist frontend](runbooks/auth-milestone-1.md). Chat delete vào trash 7 ngày; cleanup chạy startup hoặc `python -m app.cli.cleanup` (schedule hằng ngày khi vận hành).
- **Bước kế tiếp:** anh test frontend mốc 1; sửa feedback trước mốc 2 Docker sandbox. Không mở file/code execution nhiều user trước isolation tests.

---

## Cập nhật 2026-10-17 — Spec và plan AI Platform (chưa triển khai)

- Đã thống nhất bỏ workspace assignment: user tự tạo chat hoặc project cá nhân; hai role **Admin/User**, User chỉ thấy dữ liệu của mình, Admin quản trị/xem tất cả.
- Chat độc lập có file riêng; chat trong cùng project dùng chung file và instructions, lịch sử vẫn riêng. Không sharing/chat giữa user ở v1.
- Docker sandbox theo run, cấp khi cần công cụ; file bền vững theo chat/project. Không coi folder riêng hoặc container backend hiện tại là sandbox user.
- Đã viết [spec](superpowers/specs/2026-10-17-ai-platform-design.md) và [implementation plan](superpowers/plans/2026-10-17-ai-platform.md), gồm 4 mốc/11 task: Auth + ownership/DB → sandbox → project UI → quota và rollout.
- Plan có migration workspace cũ thành project import của Admin được chỉ định, backup/dry-run/idempotency; không xóa nguồn hoặc tự đoán owner.
- Username/password do Admin cấp, PostgreSQL platform riêng và các mức tài nguyên là **mặc định đề xuất để review**, chưa phải chức năng đã có. Sandbox đầu offline; hard disk quota cần kiểm chứng trên host trước rollout nhiều user.
- Lần này chỉ thay tài liệu; đã rà soát liên kết nội bộ, coverage spec/plan và whitespace diff. Chưa code Auth/sandbox, chưa migrate hoặc deploy; không chạy lại code tests cho thay đổi tài liệu.
- Bước tiếp theo: review spec/plan và bắt đầu mốc Auth. Tool execution nhiều user chỉ mở sau khi đạt isolation tests của sandbox.

---

## Cập nhật 2026-10-17 — Sidebar và modal/dropdown

- Áp dụng skill local `.agents/skills/ui-ux-pro-max/SKILL.md`, giữ hệ màu teal/zinc và light/dark mode hiện tại.
- Sidebar desktop thu còn thanh icon 64px (không ẩn hoàn toàn), có nút mở lại, New session, New workspace, chọn workspace, Files, Settings và theme toggle.
- Thay `window.prompt`/`window.alert` tạo workspace bằng modal giữa màn hình: form, validation, lỗi inline, trạng thái đang tạo và chống gửi lặp.
- Settings chia nhóm model/execution/workspace, phần kết nối nâng cao thu gọn. Files có tìm kiếm, Refresh, số lượng/dung lượng và panel xem trước.
- Dropdown dùng component chung, có selected state, phím mũi tên/Home/End/Enter/Escape và đóng khi click ngoài. Modal dùng native dialog để quản lý focus và Escape.
- UI dùng các component riêng trong `frontend/app/components/`: controls, icons, workspace-modals; không thay đổi API/backend.
- Kiểm tra: local và Docker production build + TypeScript đạt; đã triển khai frontend, xác minh bundle mới và frontend/API HTTP 200. Chưa kiểm tra tương tác trực tiếp vì Computer Use chưa được cấp quyền.

---

## Cập nhật 2026-10-17 — Khôi phục giao diện Tailwind

- Phục hồi `page.tsx` từ source map còn lưu trong `.next/dev`, ghép lại slash commands và run metrics của source hiện tại.
- Khôi phục dependency Tailwind 4.3.3 và PostCSS; giao diện chat Markdown, Settings/Files modal, sidebar mobile, collapse desktop và dark mode.
- Giữ bản build cũ trong `.backups/tailwind-build-before-recovery.tar.gz` (không đưa vào Git).
- Docker frontend build và TypeScript đã chạy đạt. Chưa kiểm tra tương tác trình duyệt: máy chưa cấp quyền Computer Use.
- Phạm vi lần này là UI. Tại mốc UI, backend còn 6 kiểm thử thất bại; đã được xử lý ở mốc 1 bên dưới.

---

## Cập nhật 2026-10-17 — Mốc 1: session và xóa session hoàn tất

- Nhiều message dùng chung session, giữ tiêu đề/workspace; nạp lịch sử user/assistant cho lượt mới, kể cả sau restart.
- SSE có cursor `after`; UI phát lại lịch sử không đóng stream bởi trạng thái cũ, không hiển thị trùng message.
- Khôi phục nút xóa và dialog xác nhận; API chặn xóa session đang chạy/đang kết thúc, giữ nguyên file workspace.
- API Files đọc được workspace của session đã lưu sau restart. Metrics chỉ tính events của lượt hiện tại.
- `/compact` chặn khi đang chạy và đồng bộ lịch sử trong RAM/đĩa; đây vẫn là thu gọn tool output, không phải LLM summary.
- Kiểm chứng: **10 unittest đạt**, Docker frontend build + TypeScript đạt. Đã triển khai cùng mốc refactor.
- Mốc 2 (chi tiết bên dưới): chuyển backend sang package `app/`, tests sang `tests/`, giữ URL API.

## Cập nhật 2026-10-17 — Mốc 2: backend đã tách package

- `app/main.py`: ghép FastAPI routers; Docker chạy `app.main:app`.
- `app/api/`: schemas và routes; `app/services/`: session, run, files, approvals, commands.
- `app/storage/`: persistence; `app/observability/`: metrics; `app/config.py`: cấu hình.
- `app/agent/`: harness, session loop, goals, context, state, hooks, workspace.
- `app/tools/`: registry, filesystem, shell, web; `app/security/`: permissions, paths, redaction.
- `app/integrations/`: LLM và MCP; `app/capabilities/`: 7 cơ chế mở rộng.
- `api_server.py` và `harness_core.py` chỉ còn adapter tương thích, không chứa logic trùng lặp.
- Tests chuyển vào `backend/tests/`: **21 unittest đạt trên local và Docker (Python 3.12)**. Test dùng dữ liệu tạm và fake LLM.
- Sửa race Stop ngay sau Start: luôn lưu terminal event, cho phép xóa/gửi tiếp sau khi task đã kết thúc.
- Đã build và triển khai backend/frontend. HTTP frontend/API 200, DELETE route và SSE cursor đúng; 5 session hiện có được giữ nguyên. Chưa kiểm tra tương tác browser do thiếu quyền Computer Use.

### Giới hạn còn lại và bước kế tiếp
- Cập nhật 2026-10-18: Auth/Admin/User và ownership chat đã có; file execution tắt, cách ly sandbox là bước tiếp theo.
- State của active runs/approvals vẫn trong RAM một process, chưa hỗ trợ nhiều worker hoặc khôi phục một run đang chạy sau restart.
- Dữ liệu session vẫn lưu JSON; mỗi lượt kế tiếp nhận lịch sử user/assistant, không khôi phục toàn bộ tool transcript cho model.
- Các kiểm thử sử dụng fake LLM/MCP, chưa xác minh gọi provider hoặc MCP server thật trong lần refactor này.

### Lệnh kiểm tra backend
```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v
```

## Tổng quan harness legacy (các capability bên dưới chưa bật lại trong API text-only mốc 1)

- **Frontend**: Next.js 16.3.3 chat UI (Enter-to-send, settings modal, sidebar workspace + sessions, Files, approval Allow/Deny) + Nginx.
- **Backend**: FastAPI + agent loop thật (`AgentSession`) + `ToolRegistry` + `PermissionManager` (3 gates) + `HookSystem` + `GoalController`/`PromptGoalEvaluator` (goal loop) + context compaction.
- **MCP** (`app/integrations/mcp.py`) + **7 cơ chế port từ learn-claude-code**: skills, memory, tasks, background, scheduler, teams, workflow.
- **Serving**: sessions persistence, workspaces list/create, approval broker, structured SSE events.
- **Security**: workspace chỉ nhận tên (chặn absolute path), bash path guard + env sạch, container non-root + `cap_drop: ALL` + `no-new-privileges`.
- **Observability**: LiteLLM Proxy gateway + Admin UI (`:4000/ui`) + Postgres.
- **Robustness (port từ hermes-agent)**: retry/backoff cho LLM, repetition guard (chặn agent lặp tool call), redaction secrets trước khi lưu session.
- **Slash commands**: `/help`, `/new`, `/stop`, `/model <name>`, `/settings`, `/files` (xử lý ở frontend) · `/status`, `/tasks`, `/skills`, `/memory`, `/usage`, `/compact` (backend `POST /api/command`).
- **Observability metric mỗi run**: duration · turns · tool calls · tools breakdown · approvals · model (hiển thị ở chat header).

---

## 0. Đối chiếu 6 components của Agent Harness (theo sơ đồ)

| # | Component (sơ đồ) | Trạng thái | Chi tiết |
|---|---|---|---|
| **1** | **Agent State** | 🟢 Đủ | |
| | – what's the goal? | ✅ | `GoalController.active_condition` + `state.json.goal` |
| | – current step #? | ✅ | `_checkpoint()` persist turn mỗi bước |
| | – observation from previous? | ✅ | tool result nằm trong `messages[]` |
| **2** | **Tools** | 🟡 Thiếu browser | |
| | – terminal | ✅ | `bash`, `bash_background` |
| | – FileSystem | ✅ | `read_file`/`write_file`/`edit_file`/`glob`/`list_dir` |
| | – **browser** | 🟡 Một phần | Có `web_fetch`; browser chạy JS cần MCP Playwright |
| **3** | **Memory** | 🟡 Thiếu Vector DB | |
| | – working memory | ✅ | Conversation + context compaction |
| | – **Vector DB** | ❌ **Chưa có** | `memory.py` là file markdown + keyword search, chưa có embedding/semantic search |
| **4** | **Orchestration** (Agent Architecture) | 🟢 Mạnh nhất | Loop + subagent + teams + worktree + workflow (parallel/pipeline) + goal gate |
| **5** | **Safety** | 🟡 Thiếu rate limit | |
| | – permissions | ✅ | `PermissionManager` + approval Allow/Deny |
| | – tool restrictions | ✅ | `safe_path` + bash path guard + deny patterns + non-root |
| | – **rate limits** | ⚠️ **Chỉ ở gateway** | LiteLLM có budget/rate-limit theo virtual key, nhưng app chưa enforce (chưa có user) |
| **6** | **Observability** | 🟡 Chưa đủ | |
| | – task succeed? | ⚠️ | Có goal evaluator + status; chưa có metric/scoring |
| | – time taken? | ✅ Cơ bản | Có duration mỗi run; chưa có latency từng bước |
| | – tools used? | ✅ | metrics đếm tool calls và breakdown theo tool |
| | – model used? | ✅ Cơ bản | metrics lưu model được yêu cầu cho run; chưa có tracing chi tiết |

**Kết luận:** chưa đầy đủ. Còn thiếu browser chạy JS, vector memory, rate limit tầng app và tracing chi tiết; metrics cơ bản và persist step đã có.

---

## 1. Chức năng CHƯA LÀM

| # | Mục | Ghi chú |
|---|---|---|
| 1 | Auth / đăng nhập | ✅ Mốc 1 hoàn tất 2026-10-18; chờ frontend acceptance |
| 2 | Chat/project cá nhân và ownership | ✅ Chat ownership; project và sandbox chưa triển khai |
| 3 | Team thật (bậc 1) | Chưa có `Team`/`Teammate` định danh (role+model+prompt), chưa isolate worktree, chưa UI |
| 4 | Team bậc 2 (mailbox + typed protocols) | Chưa có giao tiếp giữa teammate |
| 5 | Sandbox thật (OS-level) | bash mới bị siết bằng policy, chưa cách ly container/network/resource |
| 6 | Memory auto-extract/consolidate | Mới có tool `remember`/`recall`, chưa có LLM tự rút ký ức (s09 đầy đủ) |
| 7 | Workflow mẫu | `workflow.py` có runtime, chưa đăng ký workflow mẫu |
| 8 | Sample skills | Chưa có `SKILL.md` mẫu |
| 9 | Expose service mình thành MCP tools (`fastapi_mcp`) | Mình chỉ tiêu thụ MCP, chưa expose endpoint của mình |
| 10 | UI cho tasks/teams/memory/workflow | Là tool của agent, chưa có panel (chỉ có Files) |
| 11 | Cron always-on | Cron chỉ `poll_due()` lúc bắt đầu run, chưa có scheduler nền (heartbeat) |
| 12 | Langfuse-style tracing (agent waterfall) | Có LiteLLM gateway (spend/token), chưa trace kiểu `agent.run → plan → tool → sub-agent` |
| 13 | Unit test | Có 21 unittest hồi quy và contract; chưa dùng pytest |
| 14 | **Browser tool** | 🟡 Một phần — có `web_fetch` (đọc URL + chặn SSRF); browser thật (JS) cần bật MCP Playwright + cài Node |
| 15 | **Vector DB / semantic memory** | ❌ Memory hiện là file + keyword search, chưa có embedding/semantic retrieval (component 3) |
| 16 | ~~**Observability metrics**~~ | ✅ **Đã xong** — metric mỗi run (duration / turns / tool calls / tools / approvals / model) + hiển thị trên UI |
| 17 | **Rate limit ở tầng app** | ❌ Chỉ có ở LiteLLM gateway theo virtual key (component 5) |

---

## 2. Chức năng CẦN LÀM (ưu tiên)

1. **Auth + RBAC acceptance** — backend hoàn tất, anh test frontend; không migrate dữ liệu test cũ.
2. **Docker sandbox** — container theo run, file bền vững theo chat/project; cách ly tool execution, kiểm chứng network/CPU/RAM/PID/disk trước rollout.
3. **Project cá nhân + định mức** — hoàn thiện UI/file lifecycle và LiteLLM usage/budget theo user trước khi phát triển Team.
4. **Browser tool** — cắm MCP browser (Playwright/Browser-Use) vào tool pool.
5. **Tracing nâng cao** — metrics mỗi run đã có; waterfall/latency từng bước chưa có.
6. **UI panel** cho Tasks / Teams / Memory.
7. **Vector memory** — thay/bổ sung keyword search bằng embedding + semantic retrieval.
8. **Rate limit tầng app** — enforce theo user (sau khi có auth).
9. **Workflow + skill mẫu** — 1–2 ví dụ dùng ngay.
10. **Endpoint đóng kín + HTTPS** — chỉ mở nội bộ, thêm TLS nếu deploy.

---

## 3. LỖ HỔNG ĐANG CÓ

| Mức độ | Lỗ hổng |
|---|---|
| ✅ Đã xử lý mốc 1 | API bắt buộc auth/ownership, bỏ CORS wildcard và legacy routes |
| ✅ Đã xử lý mốc 1 | Chat/event theo owner; file/tools khóa đến mốc sandbox |
| 🟠 Cần trước mốc sandbox | Legacy bash guard không phải cách ly OS; public API mốc 1 không chạy bash |
| 🟠 Cao | **Không giới hạn tài nguyên** — không CPU/mem/pids/disk limit |
| 🟠 Cao | **Không kiểm soát network egress** — agent có thể exfiltrate dữ liệu |
| 🟡 Trung bình | **Master key LiteLLM nằm trong env backend** (fallback) |
| 🟡 Trung bình | **Lưu prompt/response** — LiteLLM `store_prompts_in_spend_logs: true`; `data/sessions/*.json` chứa tool output (đã **redact secret** khi lưu, nhưng vẫn lưu nội dung) |
| 🟡 Trung bình | **Không HTTPS** — 3000/8000/4000 HTTP trần, bind `0.0.0.0` |
| 🟢 Nhẹ | MCP servers placeholder key, nếu cài npx sẽ thử kết nối vô ích |

---

## 4. Điểm CẦN CẢI THIỆN

| Mục | Chi tiết |
|---|---|
| ~~Robustness LLM~~ | ✅ **Đã xong** — retry/backoff 3 lần (port từ hermes-agent) |
| ~~Repetition~~ | ✅ **Đã xong** — repetition guard chặn agent lặp tool call 3 lần |
| ~~Redaction~~ | ✅ **Đã xong** — mask secret trước khi lưu session (`app/security/redaction.py`) |
| ~~Persist step #~~ | ✅ **Đã xong** — `turns` ghi vào `state.json` mỗi turn qua `_checkpoint()` |
| Isolation worktree | `run_subagent(workdir)` đang bỏ qua `workdir` → teammate dùng chung workspace |
| Cron | Chỉ chạy lúc bắt đầu run; nên có scheduler nền nếu muốn assistant luôn-on |
| ~~Observability metric~~ | ✅ **Đã xong** — metric mỗi run + hiển thị trên UI (chỉ còn tuỳ chọn Langfuse trace) |
| Testing | Đã có unittest cho path guard, permission, task tools, agent loop, session, API; mở rộng integration test với provider/MCP thật khi cần |
| Storage | Sessions/tasks/memory đang file JSON; có thể dùng chung Postgres |
| Code | Đã tách backend thành package `app/` theo trách nhiệm; harness/core API cũ chỉ còn adapter |
| Approval UX | Mới hỏi destructive bash; mở rộng + thêm "ghi nhớ quyết định" |
| Config | Thêm env-override cho nhiều mục hơn |

---

## 5. ✅ Fix ngay (nhỏ, dễ) — ĐÃ XONG

| # | Việc | Trạng thái | Ghi chú |
|---|---|---|---|
| 1 | **Persist `current step #`** | ✅ Xong | `AgentSession._checkpoint()` ghi `turn` vào `state.json` mỗi turn; UI hiện đúng Turn |
| 2 | **Browser tool** | 🟡 Một phần | Thêm native tool **`web_fetch`** (đọc URL → text, **chặn SSRF** private/loopback/link-local); thêm entry MCP **Playwright browser** (`enabled: false`) cho browser thật — cần Node/npx trong image |
| 3 | **Observability metric** | ✅ Xong | `_compute_metrics()` tổng hợp từ events: **duration, turns, tool calls, tools breakdown, approvals, model** → lưu session + hiển thị trên chat header |

---

## 6. Ý tưởng từ Hermês Agent

| Tính năng | Ý nghĩa |
|---|---|
| Closed learning loop | Tự tạo skill từ trải nghiệm, skill tự cải thiện, memory có "nudge" (tự nhắc lưu ký ức), tìm kiếm session cũ (FTS5) + user model (Honcho)|
| Cron thật | Scheduler chạy nền, giao việc tới mọi platform|
| Subagent isolate + RPC | Spawn subagent cách ly, viết script gọi tool qua RPC → "zero-context-cost"   |
| 7 terminal backends | Local/Docker/SSH/Singularity/Modal/Daytona/Vercel Sandbox → đây chính là "sandbox thật" mình đang thiếu|
| Multi-channel gateway | Telegram/Discord/Slack/WhatsApp/Signal + CLI|
| Security | Command approval, DM pairing, container isolation|

---
## Conversation continuity và /compact
Mỗi message vẫn tạo một **run** mới nhưng tiếp tục trong cùng **session**.
Lịch sử user/assistant của các lượt trước được nạp vào run tiếp theo; tool output
được lưu để hiển thị, không phát lại toàn bộ làm ngữ cảnh model.
`/compact` chỉ thu gọn tool output/args đã lưu, không tạo bản tóm tắt hội thoại bằng LLM.

---
## Roadmap đề xuất

1. ✅ Auth + RBAC, DB/chat ownership; anh đã test login/logout/create user
2. ✅ Docker sandbox và file isolation — pilot offline; chờ anh test frontend
3. ✅ Project cá nhân + upload/download + lifecycle — chờ anh test frontend
4. ✅ Định mức AI/tài nguyên, LiteLLM identity — pilot; chờ anh test frontend, còn release gates host/HTTPS
4. Team bậc 1 (sau mốc platform; chưa thuộc v1)
5. ~~Browser tool + persist step #~~ ✅ đã xong (browser thật vẫn là tuỳ chọn)
6. ~~Observability metrics~~ ✅ đã xong
7. UI panel (Tasks/Teams/Memory)
8. Vector memory
9. Workflow + skill mẫu
10. Langfuse tracing + mở rộng integration tests
