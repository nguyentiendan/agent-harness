# Mốc 1 — Auth / ownership / DB

## Phạm vi đã triển khai

- Admin/User, login/logout, mật khẩu tạm bắt đổi, đổi/reset mật khẩu, enable/disable, bảo vệ Admin cuối.
- Chat độc lập có nhiều message, DB ownership và event history; Admin xem/stop/delete mọi chat nhưng không gửi thay owner.
- Text-only qua LiteLLM hiện có. Không file tools, shell, MCP, subagent, project hoặc budget per user ở mốc 1.
- Legacy harness packages giữ để tích hợp lại qua sandbox sau; không mount các legacy API vào FastAPI.
- Một backend worker duy nhất. Không scale replicas/workers khi admission và task registry còn trong process.

## Cấu hình/chạy

`.env` (không commit): `PLATFORM_DB_PASSWORD` ngẫu nhiên và `APP_ORIGIN=http://localhost:3000` cho máy dev. Khi đổi hostname/port phải đổi APP_ORIGIN tương ứng; khi deploy HTTPS cookie tự bật Secure. Origin phải khớp chính xác, không wildcard. Frontend chỉ gọi `/api` cùng origin.

```bash
docker compose up -d platform-db
docker compose build backend frontend
docker compose up -d --no-deps backend frontend
```

Backend startup chạy `alembic upgrade head`. DB platform và volume `platform_db` riêng, không dùng hoặc reset DB LiteLLM. Không còn mount workspace/data cũ vào backend web.

Tạo Admin đầu tiên, nhập mật khẩu bằng terminal (không đưa secret vào argv):

```bash
docker compose exec backend python -m app.cli.bootstrap_admin admin
```

Hoặc dùng `--password-stdin --temporary` cho mật khẩu cấp tạm; CLI không ghi đè nếu đã có active Admin. Bản bàn giao local dùng `.env.admin` (gitignored, mode 0600) chứa tài khoản Admin tạm. Đổi mật khẩu ngay lần login đầu. Không có signup hoặc endpoint bootstrap public.

## Kiểm thử backend

Local từ `backend/`:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Mặc định tests tạo SQLite tạm; override `TEST_DATABASE_URL` cho PostgreSQL riêng có tên chứa `test`. Test support luôn thay `PLATFORM_DATABASE_URL`, không dùng DB production kế thừa từ Compose. Tests reset schema; không chỉ định DB thật.

Tạo DB test một lần:

```bash
docker compose exec -T platform-db createdb -U platform platform_test
```

Chạy trong backend image với `TEST_DATABASE_URL` trỏ `platform_test`. Khi cần construct URL từ env Compose, dùng SQLAlchemy `make_url(...).set(database="platform_test")`; không in password. Đã chạy Alembic upgrade hai lần trên PostgreSQL test để chứng minh idempotency, rồi full unittest. Model calls trong test dùng fake provider; không coi đó là kiểm thử provider thật.

## Anh test frontend

1. Mở http://localhost:3000, login Admin tạm và đổi password; login lại.
2. Manage users: tạo A/B, đổi role, disable/enable, reset password. Tài khoản mới bắt đổi mật khẩu.
3. A tạo chat, gửi hai message; sidebar chỉ có một chat và giữ title message đầu. Reload vẫn còn history.
4. Login B: không thấy chat A. Admin chọn All users’ chats: thấy owner, đọc được nhưng composer bị khóa cho chat người khác.
5. A đang login, Admin disable A ở browser khác: A bị logout khi kiểm tra account (tối đa khoảng 5 giây), run bị dừng.
6. Logout rồi Back/refresh: không hiện hội thoại cũ. Kiểm tra rail sidebar, light/dark, modal trên mobile.
7. Settings chỉ có model được server bật; Files thông báo chờ sandbox. Không có workspace assignment.

## Persistence và bảo trì

Chat delete là soft-delete; không đọc lại qua API. CLI dưới đây purge sau 7 ngày, xóa events/runs trước chat, đồng thời dọn expired login sessions/throttle buckets:

```bash
docker compose exec -T backend python -m app.cli.cleanup
```

Chạy định kỳ hằng ngày bằng scheduler host khi đưa vào vận hành. Startup cũng chạy cleanup. Không có UI restore trong mốc 1.

Restart backend đánh dấu run đang chạy thành failed với thông báo restart, giữ conversation. Logout chỉ thu hồi login session hiện tại, không stop run; disable user stop run. Reset/đổi mật khẩu thu hồi mọi login session.

Account throttle: 5 lần sai/15 phút ở backend; IP throttle ở nginx (10 request/phút, burst 5). Không dùng IP container proxy để khóa toàn công ty, không tin X-Forwarded-For do client gửi.

## Dữ liệu cũ và giới hạn

Theo yêu cầu 2026-10-18, chỉ xóa dữ liệu test tại `data/` và `workspace_data/`, không backup/import, không xóa Docker volume LiteLLM. Giữ migration schema Alembic cho DB platform mới. Không chạy import legacy từ plan cũ.

Quotas/identity LiteLLM per user, Docker sandbox, project/shared project files và hard disk quotas thuộc các mốc tiếp theo. Gateway hiện dùng credential server hiện hữu; không gửi xuống browser. Local HTTP/port cấu hình hiện có phục vụ test nội bộ; TLS/đóng port là rollout gate trước vận hành rộng.
