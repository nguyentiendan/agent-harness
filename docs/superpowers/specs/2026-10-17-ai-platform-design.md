# AI Platform nội bộ — Auth, chat/project và Docker sandbox

Ngày: 2026-10-17. Trạng thái: **bản thiết kế để review; chưa triển khai**.

## 1. Mục tiêu và quyết định đã thống nhất

User đăng nhập là có thể New chat, gửi nhiều message trong cùng chat; tùy chọn tự tạo project để tổ chức công việc. Admin không tạo hoặc assign workspace. Chỉ có hai role `admin` và `user`. User chỉ thấy dữ liệu của mình; Admin xem/quản lý tất cả. Không chia sẻ giữa user, không chat giữa người với người trong v1.

Project chứa chat, file và hướng dẫn chung cho các chat của chính chủ sở hữu. Chat độc lập có file riêng. Docker sandbox thực thi code/lệnh với dữ liệu của đúng chat/project; folder lưu dữ liệu bền vững, container có vòng đời ngắn. Không có container thường trực cho mỗi tài khoản.

Thành công: A và B đều dùng AI được nhưng A không thể xem session/file, stop run hay approve tool của B, kể cả bằng API trực tiếp hoặc shell trong sandbox. Chat không tự nhân bản khi gửi message mới. Restart không mất file hay hội thoại.

## 2. Mặc định đề xuất, chưa phải quyết định riêng của người dùng

- Tài khoản username/password do Admin tạo; không signup, SSO hoặc reset qua email ở v1. Admin cấp mật khẩu tạm, bắt đổi ở lần đăng nhập đầu. Đây là giả định để lập plan, có thể thay bằng SSO trước khi thực hiện mốc Auth.
- PostgreSQL riêng cho platform, SQLAlchemy và Alembic. Compose thêm `platform-db` với volume/credentials riêng để không tác động schema/vòng đời LiteLLM DB hiện có.
- Một backend worker ở v1; không tuyên bố phục hồi run đang chạy sau restart.
- Sandbox mặc định: 1 CPU, RAM 1 GiB, 128 PID, timeout lệnh 120 giây, timeout run 15 phút, tối đa 1 run/user và 4 run toàn hệ thống. Đây là giá trị ban đầu để đo, Admin có thể điều chỉnh trong giới hạn server.
- Định mức AI theo user, chu kỳ tháng UTC, Admin đặt ngân sách; không chọn một số tiền mặc định tùy tiện. User chưa được cấu hình ngân sách thì chưa được chạy AI khi bật enforcement.

## 3. Phạm vi và phương án

Chọn Docker theo run, mount dữ liệu chat/project. Folder-only không đáp ứng cách ly shell. Container thường trực theo user đơn giản lúc đầu nhưng giữ tài nguyên và trộn môi trường nhiều project. Container theo run dễ cleanup và giới hạn; đổi lại package cài ngoài thư mục bền vững sẽ mất khi kết thúc.

Chưa làm: team, project chia sẻ, marketplace, public signup, SSO, browser preview/public port, GPU, Kubernetes, môi trường tùy ý do user chỉ định. Project không tự đọc lịch sử chat khác; chỉ file và project instructions được dùng chung. Không RAG tự động ở mốc này.

## 4. Mô hình dữ liệu và quyền

`session` trong API/code hiện tại tiếp tục là chat; `auth_session` là phiên đăng nhập; `run` là một lượt xử lý message. Không đổi tất cả tên session chỉ để đổi nhãn UI.

| Entity | Trường chính / ràng buộc |
|---|---|
| users | UUID, username normalized unique, display_name, password_hash, role, active, must_change_password, created_at |
| auth_sessions | hash token, user_id, csrf hash, expires_at, revoked_at; không lưu token thô |
| projects | UUID, owner_id, name, instructions, created_at, deleted_at |
| sessions | ID, owner_id, project_id nullable, title/goal, status, timestamps, deleted_at |
| runs | UUID, session_id, status, requested_model, effective_model, started/finished, metrics, error |
| events | session_id, sequence, run_id nullable, payload JSON; unique(session_id, sequence) |
| execution_scopes | UUID, owner_id, project_id hoặc standalone_session_id (đúng một), storage_key unique |
| run_leases | scope_id unique, run_id unique, acquired_at; khóa cả project và chat độc lập |
| user_limits | user_id unique, model allowlist, monthly_budget, concurrent_runs, sandbox profile |
| usage_records | idempotency key, user_id, run_id, model, tokens, cost, status, occurred_at |
| audit_events | actor_id, action, target ID, outcome, timestamp; không lưu password/token/prompt mặc định |
| migration_imports | source key/checksum, target ID, import batch; chống import lặp |

DB giữ hội thoại/metadata; filesystem giữ file user, không mount DB, auth, transcript hoặc config backend vào sandbox. Runtime state/checkpoint theo run, không dùng chung `state.json` giữa chat trong project. Memory/tasks/skills của project nếu bật phải nằm trong scope đó; không dùng dữ liệu global của user khác.

| Hành động | User | Admin |
|---|---|---|
| New chat/project; gửi message | Của mình | Của mình |
| Xem file/session/project | Của mình | Tất cả, có audit khi xem dữ liệu người khác |
| Stop/delete; quản lý file | Của mình | Tất cả, có audit |
| Approve tool | Run của mình | Tất cả, ghi rõ actor |
| Quản lý tài khoản/model/budget | Không | Có |

Admin không tiếp tục chat của người khác dưới danh tính người đó trong v1; toàn quyền quản trị không đồng nghĩa impersonation. Owner được lấy từ login, tuyệt đối không lấy từ payload. Query list lọc tại DB; lookup không có quyền trả 404; chưa login trả 401; admin-only trả 403. Mọi routes kể cả SSE, approvals, slash commands, config và file phải dùng policy chung. Endpoint workspace cũ bị gỡ khỏi public API sau migration, không để đường vòng.

## 5. Auth và UX

Cookie opaque ngẫu nhiên, HttpOnly, SameSite=Lax, Secure khi deploy HTTPS; phiên tối đa 12 giờ, không refresh vô hạn. Không lưu token ở localStorage. Mutation kiểm tra CSRF token và Origin; login cũng kiểm tra Origin. Throttle login theo account + IP, ban đầu 5 lần thất bại/15 phút; thông báo sai tài khoản/mật khẩu thống nhất. Password hash Argon2id qua thư viện được duy trì; không tự viết crypto. Không log credential.

Logout thu hồi phiên hiện tại; đổi/reset mật khẩu hoặc disable user thu hồi mọi phiên. Disable user đồng thời stop run, deny approvals, chặn SSE đang mở ở lần kiểm tra kế tiếp (tối đa 5 giây). Logout thường không stop run: user có thể đăng nhập lại xem kết quả. Không cho disable/hạ quyền Admin hoạt động cuối cùng. Bootstrap Admin bằng CLI đọc secret từ stdin, không password mặc định/command-line args.

Frontend dùng cùng origin `/api`; bỏ API URL tùy chỉnh khỏi Settings người dùng. Login, đổi mật khẩu, menu tài khoản/logout; 401 xóa state nhạy cảm và đóng SSE. Sidebar có New chat, Projects, chat cá nhân; giữ rail collapse và modal hiện có. Admin có khu Users, Usage/Limits và All chats với owner rõ ràng. Config user chỉ trả model/limit được phép, không trả toàn bộ YAML/path/secret.

## 6. Project và file lifecycle

User tạo/đổi tên project, sửa instructions, tạo chat bên trong. V1 không chuyển chat đã có giữa project để tránh copy/merge file ngầm. Một project chỉ có một run đang chạy; request cạnh tranh nhận 409, không xếp hàng ẩn. Chat vẫn có lịch sử riêng và nhiều message.

New chat không tạo container. Khi tool thực thi lần đầu, backend tạo execution scope/container lười. File lưu theo ID bất biến, ví dụ `user_data/<owner_id>/<scope_id>/files`; tên hiển thị không tham gia đường dẫn. Chặn traversal, symlink escape và archive escape nếu thêm giải nén; file access kiểm tra quyền trước khi resolve đường dẫn.

Xóa chat trong project giữ file project. Xóa chat độc lập hoặc project đưa metadata/file vào trash 7 ngày, sau đó cleanup; dialog nói rõ ảnh hưởng, chặn khi còn run/lease. Xóa project đưa các chat thuộc project vào trash. Không xóa user cứng v1: disable để giữ audit/ownership. Trash không truy cập qua API thông thường. Upload/download có streaming, giới hạn 25 MiB/file ban đầu, tránh đọc toàn bộ file lớn vào RAM; preview text tối đa 1 MiB, file nhị phân tải xuống thay vì render HTML tùy ý.

## 7. Ranh giới sandbox

```text
Browser → FastAPI (Auth / policy / orchestration / LLM)
                ├── platform-db: ownership, events, runs, usage
                ├── LiteLLM: model gateway, key riêng theo user
                └── sandbox-runner (dịch vụ nội bộ tin cậy)
                         └── Docker container theo run
                              └── /workspace: duy nhất scope được cấp
```

Agent loop và LLM credentials ở control plane. Tool filesystem/shell chạy qua runner; container không chứa provider key, platform DB password hoặc Docker socket. Runner là thành phần đặc quyền: chỉ nó truy cập Docker daemon, API nội bộ có xác thực, không public port. Backend không nhận Docker options/raw host path từ client. Runner chỉ chấp nhận image digest/profile được quản trị cấu hình và scope ID được cấp, kiểm tra mapping/lease; không API Docker tổng quát. Docker socket không mount vào backend hoặc sandbox.

Container chạy non-root, drop capabilities, no-new-privileges, default seccomp, root filesystem read-only; `/tmp` tmpfs có giới hạn, chỉ scope mount read-write; không host PID/network, privileged, devices hoặc publish ports. CPU/RAM/PID và timeout phải được kiểm tra trên host thực tế. Docker là lớp cách ly dùng chung kernel, không tuyên bố bảo vệ tuyệt đối trước kernel exploit. Runner nên chạy trên host/VM dành riêng; rootless khi môi trường hỗ trợ các giới hạn cần thiết.

Mốc sandbox đầu dùng `network=none`; LLM/web fetch có kiểm soát ở backend. Image chuẩn có Python/Node và công cụ cơ bản. Cài npm/pip từ internet và preview server chưa có ở mốc này; không quảng bá sandbox offline là hỗ trợ mọi workflow vibe-code. Nếu cần mở mạng sau này, phải có thiết kế egress proxy/firewall và test chặn internal services/metadata, không chỉ biến proxy env.

Lifecycle: acquire DB lease → tạo container khi cần → execute → trả tool result → stop/kill mọi process kể cả background → remove container → release lease trong finally. Giữ folder. Timeout/OOM/Docker unavailable trả lỗi rõ ràng; tuyệt đối không fallback chạy shell trong backend. Restart reconcile run dang dở thành interrupted/failed, deny approvals cũ, cleanup container theo label run ID và giải phóng lease. Cleanup idempotent.

Audit toàn bộ tool path: shell, filesystem, background, teams/workflow/subagent, scheduler, memory/tasks/skills và MCP. Tool chưa chuyển được hoặc chưa xác định scope phải tắt khi multi-user; approval Allow không cho phép bypass sandbox. Backend đọc file cho preview/download phải dùng file gateway an toàn trước symlink race, không mở raw path do agent có thể thay đổi.

Giới hạn disk không được suy ra từ RAM limit: cần quota filesystem/volume thực sự trên host để chặn shell ghi đầy ổ. Mốc đầu có kiểm tra dung lượng/admission và host storage riêng, nhưng **không tuyên bố hard disk quota** nếu host chưa hỗ trợ. Trước rollout nhiều user, chọn và kiểm chứng quota host hoặc chấp nhận rõ giới hạn pilot có giám sát; chưa đạt thì giữ chế độ pilot.

## 8. Định mức và LiteLLM

Platform là nguồn user/role; LiteLLM là gateway, không tái sử dụng bảng nội bộ của nó. Provision key riêng per user server-side, model allowlist và budget; không gửi key xuống browser/sandbox. Mọi call primary/reviewer/subagent đều phải đi qua cùng identity, không fallback master key. Đối chiếu API/budget semantics với phiên bản LiteLLM đang chạy khi triển khai và pin image thay vì dựa vào tag thay đổi.

Admission kiểm tra budget/model/concurrency trước run và trước từng LLM call. Ghi usage idempotent, tính cả reviewer/subagent; reconcile số liệu gateway. Khi cost chưa biết phải hiện pending, không ghi 0 như đã xác nhận. Không hứa budget chính xác tuyệt đối với token đang stream/in-flight: reserve theo upper bound khi có bảng giá, ngừng call mới khi hết hạn mức; xác minh overshoot với gateway trong test. Admin thấy chi phí theo user, user thấy của mình.

## 9. Migration dữ liệu hiện có

Không tự đoán chủ sở hữu. Dữ liệu hiện tại không có user; CLI yêu cầu `--owner <admin-id>` cho lần chuyển và dry-run báo mapping. Mỗi workspace cũ thành một project `Imported: <name>` của Admin được chỉ định; các session cùng workspace cùng project, giữ ID/title/events để bảo toàn quan hệ file hiện có. Workspace không có chat vẫn có project. User mới không thấy dữ liệu import.

Maintenance mode chặn run mới; đợi/stop run đang chạy trước snapshot. Backup JSON, workspace_data và DB; copy sang layout mới, không move/delete bản gốc. Dry-run kiểm kê counts/checksum, báo symlink/path bất hợp lệ để xử lý thủ công, không theo symlink ra ngoài root. Import transaction + manifest/checksum có thể chạy lại, không nhân đôi. So sánh counts, history, file checksum trước cutover. Không dual-write JSON/DB lâu dài. Rollback trước mở traffic mới: khôi phục DB/config và dùng nguyên nguồn cũ; sau có ghi mới phải export/reconcile trước rollback, không ghi đè mù.

## 10. Mốc triển khai và release gate

1. Auth + ownership + DB/migration. Trong thời gian chưa sandbox, tắt tool execution cho multi-user; chỉ text chat hoặc môi trường dev một Admin.
2. Runner/sandbox + file gateway + cleanup. Chứng minh cross-user/cross-scope isolation bằng Docker integration tests; không chỉ mock.
3. Project cá nhân + sidebar + upload/download + lifecycle. Dữ liệu import có thể tồn tại từ mốc 1; UI project hoàn thiện ở mốc 3.
4. Limits/usage + LiteLLM identity + hardening + pilot. Không mở nhiều user với budget chưa enforce nếu Admin đã đặt budget.

Acceptance: login/logout/reset/disable đúng; không truy cập chéo mọi API/SSE/tools; stable session qua follow-up/restart; concurrent project trả 409; container bị kill không mất file; backend crash không để process mồ côi; migration repeat không đổi số lượng; owner không sửa qua request; key không xuất hiện trong UI/events/sandbox; last Admin được bảo vệ; frontend production build và regression hiện có đạt.

## 11. Tham chiếu

- [Docker Engine security](https://docs.docker.com/engine/security/): ranh giới daemon, mount và capabilities.
- [Docker seccomp](https://docs.docker.com/engine/security/seccomp/): giữ profile mặc định, không dùng unconfined.
- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/): kiểm chứng giới hạn trên môi trường chạy thực tế.

Các giá trị và API trong tài liệu là hợp đồng đề xuất của platform, không phải mô tả tính năng đã có.
