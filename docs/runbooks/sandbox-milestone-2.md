# Mốc 2 — sandbox runner và file gateway

## Phạm vi

Mỗi chat độc lập có một `ExecutionScope` và Docker named volume riêng. Container chỉ tạo khi model gọi tool lần đầu trong run, bị xóa khi run kết thúc; volume giữ qua các message/restart. Project dùng chung scope triển khai ở mốc 3. Admin đọc file qua gateway theo owner thật; User không được truy cập chat/file của người khác.

Tool đang bật: `bash`, `read_file`, `write_file`, `edit_file`, `list_dir`. Python 3, Node 22, Git có sẵn. Network bị tắt: chưa tải package, gọi web hoặc clone repo từ Internet. MCP, subagent, browser, scheduler, workflow, tasks/memory/skills và host-shell legacy không đăng ký trong loop mới. Công cụ trong sandbox được chạy theo yêu cầu chat, chưa có approval riêng từng command.

## Cấu trúc và giới hạn

- `backend/app/execution/`: tool loop, authenticated client, durable lease và cleanup.
- `sandbox_runner/`: service nội bộ duy nhất có Docker socket, registry SQLite giữ owner binding và tombstone. Không publish port; token riêng ≥32 ký tự. Service này là thành phần tin cậy có quyền Docker host.
- `sandbox/`: image không có backend code, provider key, token runner hoặc Docker socket. UID/GID 10001, readonly root, no-new-privileges, drop ALL caps, network none. Chỉ volume của scope được mount `/workspace`; `/tmp` tmpfs 64 MiB.
- Mỗi container: 1 CPU, RAM/swap tối đa 1 GiB, 128 PIDs. Mỗi run tối đa 15 phút; command 120 giây; 15 model turns. 1 run/user, 4 run toàn app; 2 file helper đồng thời. Một backend worker/replica.
- Shell output giữ tối đa 64 KiB; preview UTF-8 tối đa 1 MiB, không binary; write/edit tool tối đa 60000 bytes; list tối đa 500 file. File gateway chỉ đọc, chống traversal/symlink/hardlink/FIFO bằng descriptor tương đối và kiểm tra regular file.
- Tag sandbox được resolve thành immutable image ID khi cấp run. Docker Desktop Linux containers đã test; Docker Linux cần smoke lại trên host đích.
- **Pilot-only:** volume chưa có hard disk quota theo user. Reserve guard chặn cấp run khi runner state filesystem còn <1 GiB, sweep dừng container khi <256 MiB; đây không ngăn được burst ghi đầy ổ và không thay thế filesystem quota. Không rollout rộng trước mốc quota/host validation.

## Cleanup và sự cố

DB lease được lưu trước tool execution. Khi stop/timeout/logout-disable liên quan run hoặc kết thúc, backend yêu cầu runner xóa cả container/process tree rồi mới release lease. Nếu runner lỗi, run vẫn giữ trạng thái đang kết thúc/running và lease; retry mỗi 5 giây. Stop có thể chờ runner timeout, không đảm bảo tức thời khi Docker lỗi.

Backend restart đánh dấu run dang dở failed sau cleanup, không replay tool. Runner restart sweep container do chính namespace quản lý; watchdog 5 giây kiểm tra deadline/exited. Container mất không được tự tạo lại với cùng run ID. File volume giữ nguyên.

Chat delete vẫn soft-delete 7 ngày; purge chạy startup, mỗi giờ và CLI `python -m app.cli.cleanup`. Scope từng cấp sandbox phải xác nhận purge volume, kể cả khi tắt tool flag; lỗi runner giữ metadata để retry. Không dùng `docker compose down -v` hoặc prune volumes khi vận hành.

## Build và vận hành

Tạo `RUNNER_TOKEN` ngẫu nhiên trong `.env` (không commit hoặc hiển thị). Không đổi `.env.admin`/password đã dùng.

```sh
docker compose --profile build build sandbox-image
docker compose build sandbox-runner backend frontend
docker compose up -d sandbox-runner backend frontend
```

Backend tự chạy Alembic upgrade thêm `execution_scopes`/`run_leases`, không reset users/chat. Runner healthcheck phải đạt trước backend startup. Image sandbox phải build trước khi gọi tool.

## Kiểm thử

```sh
cd backend
.venv/bin/python -m unittest discover -s tests
```

Ở repo root:

```sh
python3 -m unittest discover -s sandbox_runner/tests -p test_files.py
docker compose run --rm --no-deps sandbox-runner python -m unittest discover -s tests -p test_runtime.py
docker compose run --rm --no-deps -e RUN_DOCKER_TESTS=1 sandbox-runner python -m unittest discover -s tests -p test_docker.py
```

Docker tests dùng namespace UUID riêng và chỉ xóa resource test. `backend/tests/smoke_sandbox_restart.py` cần `PLATFORM_DATABASE_URL` trỏ **platform_test** và runner đang chạy: tạo worker subprocess, SIGKILL, reconcile, kiểm tra file còn rồi purge; script từ chối production database. Bộ unittest backend tạo/xóa schema: chỉ dùng SQLite test hoặc `TEST_DATABASE_URL` riêng, tuyệt đối không database platform đang dùng.

## Anh test frontend

1. Trong chat mới, yêu cầu tạo `hello.py`, chạy Python và kiểm tra output/Files.
2. Gửi message thứ hai yêu cầu sửa cùng file; vẫn cùng chat/title, nội dung file được giữ.
3. New chat hoặc user khác không thấy file chat cũ. Admin chọn chat của user đọc được file nhưng không gửi message dưới danh tính user.
4. Yêu cầu chạy lệnh dài rồi Stop; gửi message mới sau cleanup. File đã ghi vẫn còn.
5. Thử đọc file binary hoặc path không hợp lệ: UI báo lỗi, không treo. Preview lớn có thông báo cắt ở 1 MiB.
6. Login/logout/create user kiểm tra hồi quy như mốc 1. Frontend interactions và provider thật do anh kiểm tra; tests tự động dùng fake model.
