# Mốc 3 — Project cá nhân và upload/download

## Hành vi đã triển khai

- Sidebar có Projects và Standalone chats; tạo project, đổi tên, sửa instructions, xóa project. Giữ sidebar rail, modal, mobile và dark/light.
- Chat trong cùng project dùng chung file và instructions. Lịch sử message/tool event vẫn thuộc từng chat; không tự đọc hội thoại khác. Không chuyển chat đã có sang project khác.
- Admin chọn All users’ chats để xem/quản lý project/file của user, có audit; không tạo/gửi chat dưới danh tính user khác.
- Files dùng được ngay từ project chưa có chat. Với standalone chưa có chat, nút “Create a chat to upload files” tạo metadata trước; không tạo agent container.
- Upload/download nhị phân tối đa **25 MiB/file**, text preview tối đa 1 MiB. Download là attachment/octet-stream, không render HTML; file binary chỉ tải xuống. Upload cùng tên có xác nhận thay thế. Không tự giải nén archive hay thực thi file upload.
- Upload/delete/download chặn khi scope đang có run/file transfer, tối đa 2 transfer toàn backend. Run cạnh tranh nhận 409, UI giữ draft. Một backend worker/replica như mốc 2.
- Xóa chat trong project giữ file project. Xóa standalone/project đưa metadata vào trash 7 ngày rồi purge; project delete gồm mọi chat. File delete riêng là **xóa vĩnh viễn**, có xác nhận rõ, không áp dụng trash 7 ngày.

## Cách truyền file

Browser gửi body binary qua backend có Auth/CSRF/ownership; backend đọc chunk và spool có giới hạn trên disk, chuyển tiếp sang runner nội bộ. Runner dùng helper Docker với readonly rootfs, volume đúng scope và `/tmp` tmpfs. Upload truyền qua exec stdin vào staging cố định rồi worker dùng descriptor tương đối để thay file atomically. Download tạo snapshot trong helper rồi truyền stdout theo chunk. Không sử dụng Docker archive API vì API đó không hỗ trợ trường hợp readonly rootfs/tmpfs này. Không nạp trọn file 25 MiB vào RAM backend/runner.

Guard traversal/symlink/hardlink/FIFO giữ nguyên. Helper không có provider key/socket/backend config. Request bị hủy phải đợi worker copy kết thúc trước đóng spool. Network sandbox vẫn offline; chưa có hard disk quota, chỉ pilot nội bộ.

## Schema và nâng cấp

Alembic `20261019_projects` thêm projects, sessions.project_id, scope parent FK/check. Scope/volume ID cũ giữ nguyên; chat chưa có scope được backfill. Không reset users/chat/password hoặc DB LiteLLM. Schema downgrade tự động bị chặn vì chuyển ngược project sang standalone cần export/thiết kế bảo toàn dữ liệu.

```sh
docker compose --profile build build sandbox-image
docker compose build sandbox-runner backend frontend
docker compose up -d sandbox-runner backend frontend
```

Không restart runner giữa run đang hoạt động. Không `down -v`/volume prune. Cleanup vẫn startup + mỗi giờ + `python -m app.cli.cleanup`, retry runner failure giữ metadata để dọn tiếp.

## Kiểm chứng và giới hạn

- 47 backend tests: ownership, CSRF, busy project, instructions/history, stream size cap, cleanup, small upload transaction/regression, Admin update.
- 4 worker file tests, 3 runner unit/cancellation tests, 6 Docker integration tests; binary exact 25 MiB round-trip, >25 MiB reject, readonly profile, cross-owner, timeout/OOM/background, path safety.
- `backend/tests/smoke_projects_files.py`: HTTP TestClient + PostgreSQL test + runner/Docker thật; fake model duy nhất, tool bash thật tạo file project. Upload/download binary, hai chat chung file/lịch sử riêng, standalone riêng, Admin/User và trash purge.
- `backend/tests/smoke_project_migration.py`: schema tạm riêng trong platform_test, seed dữ liệu mốc 2, upgrade lặp và kiểm tra owner/scope ID/has_files/counts giữ nguyên. Từ chối production DB.
- Docker SDK vẫn có ResourceWarning socket đã đóng khi test, không có test failure. Thao tác frontend trực tiếp và model/provider thật do anh test.

## Checklist frontend cho anh

1. New project “Marketing”, thêm instructions. Tạo hai chat; xác nhận tên project/header và các chat được lọc đúng.
2. Files → upload CSV/text và file binary. Mở preview text; tải binary về so sánh. File cùng tên phải hỏi Replace; xóa file phải hỏi xác nhận vĩnh viễn.
3. Nhờ agent đọc/sửa file ở chat A; chat B thấy file mới nhưng không có message của A. Standalone chat không thấy file project.
4. Khi project đang chạy, thử gửi ở tab/chat thứ hai hoặc upload/delete: nhận thông báo bận, message draft còn. Stop rồi thử lại.
5. Xóa một chat giữ file; xóa project có cảnh báo xóa cả chat và trash 7 ngày. Refresh không hiện lại metadata đã xóa.
6. Logout A/login B không thấy project/file A. Admin All users’ chats có tên owner, sửa project và quản lý file được, nhưng không gửi message của user.
7. Thu sidebar còn rail, mở lại, thử mobile/light/dark. Kiểm tra login/logout/create user hồi quy.
