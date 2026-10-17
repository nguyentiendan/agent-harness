# Agent Harness — Status & Roadmap

> Tổng hợp toàn bộ quá trình refactor từ đầu session.
> Snapshot tại thời điểm viết; cập nhật khi có thay đổi.

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
- Auth, Admin/User và cách ly file theo User chưa triển khai; đây là bước tiếp theo.
- State của active runs/approvals vẫn trong RAM một process, chưa hỗ trợ nhiều worker hoặc khôi phục một run đang chạy sau restart.
- Dữ liệu session vẫn lưu JSON; mỗi lượt kế tiếp nhận lịch sử user/assistant, không khôi phục toàn bộ tool transcript cho model.
- Các kiểm thử sử dụng fake LLM/MCP, chưa xác minh gọi provider hoặc MCP server thật trong lần refactor này.

### Lệnh kiểm tra backend
```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v
```

## Tổng quan nhanh (đã xong)

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
| 1 | Auth / đăng nhập | Chưa có user, login, session |
| 2 | Chat/project cá nhân và ownership | Đã có spec/plan; chưa triển khai, bỏ workspace assignment |
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

1. **Auth + RBAC** — Admin/User, login/logout, ownership chat/project và migration DB theo spec/plan mới.
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
| 🔴 Nghiêm trọng | **Không có auth** — start run / đọc-ghi workspace / approve / stop không cần xác thực; CORS `*` |
| 🔴 Nghiêm trọng | **Không tách theo user** — user A đọc được file/session/event của user B |
| 🟠 Cao | **Bash vẫn thoát được** bằng cách lách pattern (vd `python -c "…chr(47)…"`) — static guard, không phải sandbox thật |
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

1. Auth + RBAC, DB/ownership và migration (spec/plan 2026-10-17)
2. Docker sandbox và file isolation
3. Project cá nhân, định mức AI/tài nguyên và pilot
4. Team bậc 1 (sau mốc platform; chưa thuộc v1)
5. ~~Browser tool + persist step #~~ ✅ đã xong (browser thật vẫn là tuỳ chọn)
6. ~~Observability metrics~~ ✅ đã xong
7. UI panel (Tasks/Teams/Memory)
8. Vector memory
9. Workflow + skill mẫu
10. Langfuse tracing + mở rộng integration tests
