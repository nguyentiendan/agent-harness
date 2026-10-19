# Mốc 4 — định mức, usage và pilot nội bộ

## Trạng thái và thao tác frontend

Mốc 4 bổ sung budget USD theo tháng UTC, model allowlist, concurrency 1–4 và output cap 1–4096. Mặc định tài khoản chưa được cấp budget/models sẽ bị chặn AI, kể cả Admin. Đăng nhập Admin → **AI limits** → chọn tài khoản → nhập budget, chọn model → Save limits. Refresh trang chat sau thay đổi model. **My usage** chỉ hiện tài khoản đang đăng nhập; Admin xem tài khoản khác qua AI limits.

Ngân sách 0 hoặc danh sách model trống chặn AI. Stop run trước khi đổi định mức hoặc rotate key. Key chỉ được tạo khi có call hợp lệ; không hiển thị trên browser, events hoặc sandbox. Disable user chặn call mới ngay và xếp hàng block key. Worker thử thu hồi mỗi phút; gateway lỗi thì giữ queue, không cấp key thay thế trước khi xử lý queue của user.

Usage gồm Confirmed, Pending reserve, Available. Reserve bảo thủ bằng chi phí toàn bộ context/output tối đa do gateway công bố; không phải ước lượng tiền cho prompt hiện tại. Model thiếu giá/context bị chặn. Giá provider thay đổi hoặc phí ngoài bảng giá có thể làm actual cost vượt reserve: ghi đủ chi phí thực tế và chặn call tiếp theo, không tuyên bố hard budget tuyệt đối.

Call timeout/cancel sau dispatch giữ pending. Không tự retry ở client/router. Gateway spend log được match bằng **response body id + platform user ID**, không phải `x-litellm-call-id`. Chi phí header cập nhật ngay; thiếu header thì đối soát mỗi phút. Có cursor để các lượt cũ không làm nghẽn lượt mới. Admin có **Unresolved calls · all months**, phân trang 100 lượt; đối soát thủ công cần cost và bằng chứng/reason, run đã kết thúc, lưu audit. Không coi thiếu log là cost 0.

## Phạm vi pilot và điều kiện mở rộng

- Một backend worker/replica. Concurrency toàn server tối đa 4; project/file scope vẫn dùng lease độc quyền.
- Sandbox offline, 1 CPU / 1 GiB / 128 PIDs, root readonly, thời gian run tối đa 900 giây. Admin không chỉnh Docker options/path.
- **Chưa có hard disk quota theo user/scope trên host này.** Có low-disk admission/watchdog nhưng shell vẫn có thể làm đầy volume. Chỉ pilot có giám sát; chưa rollout rộng. Phải xác minh filesystem quota trên host/VM dành riêng trước khi mở rộng.
- Compose local chỉ bind frontend 3000, backend debug 8000, LiteLLM 4000 vào loopback; DB/runner không publish. Khi lên nội bộ: ingress HTTPS cùng origin, chỉ nginx/ingress public; đóng port debug backend và gateway, APP_ORIGIN đúng HTTPS. Cookie Secure lấy từ APP_ORIGIN. Chưa cấu hình domain/certificate công ty hoặc kiểm tra HTTPS trên host production.
- Giữ nguyên gateway credentials cũ trong lần triển khai để tránh hỏng khóa mã hóa DB. Audit và thay các secret mặc định trước triển khai rộng; **không đổi LITELLM_SALT_KEY mù**, phải re-encrypt credentials theo quy trình LiteLLM. Không coi `.env.example` là secret production.
- Public platform không bật legacy Harness/reviewer/subagent/MCP. Nếu mở sau này phải truyền cùng user/run identity qua billing; wrapper không có identity sẽ từ chối khi enforcement bật. CLI legacy cần explicit scoped key, không fallback master.

## Cấu hình và deployment

`PLATFORM_KEY_ENCRYPTION_KEY` là Fernet key riêng, giữ ổn định ngoài Git. Mất/đổi key làm key ciphertext không đọc được; dừng AI, thu hồi key cũ bằng hash và cấp lại có kiểm soát. `LITELLM_MASTER_KEY` chỉ dùng management API, không completion. `LITELLM_SALT_KEY` thuộc gateway, khác Fernet key platform.

LiteLLM đã pin image digest phiên bản 1.105.1 trong Compose; tắt lưu prompt/response trong spend logs, tắt router retry. Thay config chỉ ảnh hưởng log mới, không xóa lịch sử cũ.

```sh
docker compose --profile build build sandbox-image
docker compose build sandbox-runner backend frontend
docker compose up -d sandbox-runner litellm backend frontend
docker compose exec -T backend alembic current
```

Schema head `20261019_billing`: user_limits, gateway_keys, usage_records. Không import/reset dữ liệu cũ hoặc đổi Admin password. Usage giữ run ID không FK để accounting tồn tại sau trash purge. Alembic vẫn quản lý schema, dù user đã miễn import/migration dữ liệu test cũ.

## Kiểm thử tái lập

Từ repo root:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests
npm --prefix frontend run build -- --webpack
PYTHONPATH=sandbox_runner backend/.venv/bin/python -m unittest discover -s sandbox_runner/tests -p test_files.py
docker compose run --rm --no-deps -T sandbox-runner python -m unittest discover -s tests -p test_runtime.py
docker compose run --rm --no-deps -T -e RUN_DOCKER_TESTS=1 sandbox-runner python -m unittest discover -s tests -p test_docker.py
docker compose run --rm --no-deps -T backend python tests/smoke_gateway_contract.py
docker compose run --rm --no-deps -T backend python tests/smoke_platform_billing.py
```

**DB tests phá schema:** tạo `platform_test` riêng; `TEST_DATABASE_URL` phải trỏ DB test. Dùng `make_url(os.environ['PLATFORM_DATABASE_URL']).set(database='platform_test').render_as_string(hide_password=False)` để set env trong process, không in password. `smoke_platform_billing.py` tự đổi sang platform_test trước import; không chạy đối với DB có dữ liệu cần giữ. Migration rehearsal `tests/smoke_project_migration.py` yêu cầu PLATFORM_DATABASE_URL trỏ platform_test, tạo schema UUID rồi dọn.

Hai gateway smoke tạo model tạm cấu hình **mock_response ở model config**, explicit invalid test key và `api_base=http://127.0.0.1:9`, assert nội dung mock chính xác, rồi xóa model/key. Không dùng mock_response trong request: gateway này bỏ qua tham số đó. Những thử nghiệm ban đầu đã gọi provider thật với cap 16 tokens trước khi phát hiện; bộ cuối đã chuyển sang mock model không thể truy cập provider.

Kết quả 2026-10-19: 59 backend tests đạt local và PostgreSQL; 4 worker + 3 runner unit + 6 Docker isolation đạt. Gateway contract và integration encrypted key → completion → reconciliation → rotation → disable đạt. Mock cost 0.00001335 USD, overspend 0; test mô phỏng cost 0.11 với budget 0.1 ghi overspend 0.01 và chặn lượt sau. Cancellation giữ reserve, concurrent reserve không vượt budget, UTC rollover, pending khác tháng/ngoài 100 lượt và fair sweep đã có regression. Schema upgrade lặp đạt. Browser thao tác để anh test; không đánh dấu đã test frontend interaction.

## Recovery / maintenance

Backup vận hành cần đồng bộ platform DB, LiteLLM DB, `.env`/Fernet/salt trong secret store, runner registry và named scope volumes. Dừng admission/runs và file transfers trước snapshot để giữ mapping/lease nhất quán. Không gửi DB dump hoặc env vào Git. Khôi phục vào DB/volumes riêng, xác minh ownership, ciphertext decrypt, usage totals, scope mapping và sandbox isolation trước mở traffic. Không chạy `docker compose down -v` hoặc volume prune.

Đã diễn tập `pg_dump -Fc platform_test` → tạo `platform_restore_test` → `pg_restore` → đọc users/usage → drop database restore test. Bản ghi mẫu cost `0.0123456789` được restore đúng đủ precision. Đây là DB restore rehearsal, **không phải** disaster recovery đầy đủ cho scope volumes và gateway secrets. Chưa diễn tập failover host.

Rollback ứng dụng trước traffic có thể dùng image trước nhưng phải dừng AI: bản mốc 3 chưa enforce budget. Không downgrade billing schema khi có usage thật vì sẽ xóa accounting/key metadata. Ưu tiên forward fix; nếu rollback toàn trạng thái, chặn traffic và restore đồng bộ backup đã kiểm chứng, reconcile chi phí gateway phát sinh sau backup trước khi mở lại.

Cleanup leases mỗi 5 giây, key/usage mỗi phút, trash mỗi giờ và startup. Theo dõi pending lâu, revoke_pending, disk free, run stuck và sai lệch gateway/provider invoice. Có thể chạy `python -m app.cli.cleanup` trong backend để retry purge; không tự xóa metadata khi runner chưa xác nhận volume purge.

Tham chiếu API: [Virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys), [cost tracking](https://docs.litellm.ai/docs/proxy/cost_tracking). Contract test với image pin là bằng chứng cho hành vi đang dùng.
