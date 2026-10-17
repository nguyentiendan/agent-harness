# Agent Harness — Status & Roadmap

> Tổng hợp toàn bộ quá trình refactor từ đầu session.
> Snapshot tại thời điểm viết; cập nhật khi có thay đổi.

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
- Kiểm chứng: **10 unittest đạt**, Docker frontend build + TypeScript đạt. Chưa cập nhật container cho mốc này; sẽ triển khai cùng mốc refactor.
- Mốc 2 đang làm: chuyển backend sang package `app/`, tests sang `tests/`, giữ URL API.

## Tổng quan nhanh (đã xong)

- **Frontend**: Next.js 16.3.3 chat UI (Enter-to-send, settings modal, sidebar workspace + sessions, Files, approval Allow/Deny) + Nginx.
- **Backend**: FastAPI + agent loop thật (`AgentSession`) + `ToolRegistry` + `PermissionManager` (3 gates) + `HookSystem` + `GoalController`/`PromptGoalEvaluator` (goal loop) + context compaction.
- **MCP** (`mcp_manager.py`) + **7 cơ chế port từ learn-claude-code**: skills, memory, tasks, background, scheduler, teams, workflow.
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
| | – time taken? | ⚠️ | Có `created_at/updated_at` theo session; chưa có latency từng bước |
| | – tools used? | ✅ | events + `progress.md` (chưa tổng hợp/đếm) |
| | – model used? | ⚠️ | LiteLLM gateway log model/token/request; harness chưa ghi model vào session |

**Kết luận:** chưa đầy đủ. Thiếu rõ **4 điểm** (browser tool, vector memory, rate limit tầng app, observability metric) + **1 lỗi nhỏ** (không persist step #).

---

## 1. Chức năng CHƯA LÀM

| # | Mục | Ghi chú |
|---|---|---|
| 1 | Auth / đăng nhập | Chưa có user, login, session |
| 2 | Tách workspace/session theo user | Chưa có ownership; mọi người dùng chung |
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
| 13 | Unit test | Có 10 unittest hồi quy; chưa dùng pytest |
| 14 | **Browser tool** | 🟡 Một phần — có `web_fetch` (đọc URL + chặn SSRF); browser thật (JS) cần bật MCP Playwright + cài Node |
| 15 | **Vector DB / semantic memory** | ❌ Memory hiện là file + keyword search, chưa có embedding/semantic retrieval (component 3) |
| 16 | ~~**Observability metrics**~~ | ✅ **Đã xong** — metric mỗi run (duration / turns / tool calls / tools / approvals / model) + hiển thị trên UI |
| 17 | **Rate limit ở tầng app** | ❌ Chỉ có ở LiteLLM gateway theo virtual key (component 5) |

---

## 2. Chức năng CẦN LÀM (ưu tiên)

1. **Auth + RBAC** — login, user, ownership workspace/session, phân quyền admin/member.
2. **Sandbox thật** — container riêng mỗi session (hoặc bubblewrap/seccomp), giới hạn network/CPU/mem/disk.
3. **Team bậc 1** — `Team` + `Teammate` persistent, isolate worktree thật, UI tạo team/task/xem tiến độ.
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
| ~~Redaction~~ | ✅ **Đã xong** — mask secret trước khi lưu session (`redact.py`) |
| ~~Persist step #~~ | ✅ **Đã xong** — `turns` ghi vào `state.json` mỗi turn qua `_checkpoint()` |
| Isolation worktree | `run_subagent(workdir)` đang bỏ qua `workdir` → teammate dùng chung workspace |
| Cron | Chỉ chạy lúc bắt đầu run; nên có scheduler nền nếu muốn assistant luôn-on |
| ~~Observability metric~~ | ✅ **Đã xong** — metric mỗi run + hiển thị trên UI (chỉ còn tuỳ chọn Langfuse trace) |
| Testing | Thêm pytest cho `check_bash_paths`, `TaskStore`, `PermissionManager`, path guard |
| Storage | Sessions/tasks/memory đang file JSON; có thể dùng chung Postgres |
| Code | `harness_core.py` ~1000 dòng, nên tách tiếp; docstring lẫn Anh/Việt |
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

1. Auth + RBAC
2. Sandbox thật (tham khảo terminal backends của hermes-agent)
3. Team bậc 1 (team + worktree isolation + UI)
4. ~~Browser tool + persist step #~~ ✅ đã xong (browser thật vẫn là tuỳ chọn)
5. ~~Observability metrics~~ ✅ đã xong
6. UI panel (Tasks/Teams/Memory)
7. Vector memory
8. Workflow + skill mẫu
9. Langfuse tracing + pytest
