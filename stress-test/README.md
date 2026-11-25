# Stress test PMS (Locust)

Mô phỏng nhiều nhân viên dùng PMS cùng lúc, chia làm 2 luồng:

| Luồng | Class | Mặc định | Request |
|---|---|---|---|
| **Đọc**: nhân viên vào xem project | `EmployeeReader` | 4 phần | `GET /` (frontend), `GET /api/project/user/me/projects`, `GET /api/project/[id]`, `GET /api/project/[id]/tasks`, `GET /api/project/[id]/tasks/[taskId]`, `GET /api/project/user/me/tasks`, `GET /api/user/me` |
| **Ghi**: cập nhật trạng thái task | `TaskUpdater` | 1 phần | `PATCH /api/project/[id]/tasks/[taskId]` với `{status}`, theo vòng `todo → in_progress → review → done`; kèm `GET` lại danh sách task của mình |

Mỗi user ảo nghỉ 1–3 giây giữa hai thao tác, giống người dùng thật. Request bị tính là **lỗi** khi trả về mã khác mong đợi, hoặc khi quá `REQUEST_TIMEOUT` (mặc định 10 giây).

## Chuẩn bị (một lần)

```bash
cd stress-test
source ../locust-venv/bin/activate
python 01_create_accounts.py --host http://localhost          # tạo accounts.csv (50 tài khoản)
python 02_seed_projects_tasks.py --host http://localhost --admin-email <email-admin>
```

`accounts.csv` chứa mật khẩu nên đã nằm trong `.gitignore` (`*.csv`).

Token đăng nhập được cache theo tài khoản: 50 tài khoản chỉ đăng nhập 50 lần, dù có hàng trăm user ảo. Nhờ vậy tải đo được là tải của hai luồng đọc/ghi, không phải tải bcrypt của `signin`.

## Chạy

**Giao diện web** (chỉnh số user trực tiếp, xem biểu đồ realtime tại http://localhost:8089):

```bash
locust -f locustfile.py --host http://localhost
```

**Tăng tải theo bậc** (tìm ngưỡng chịu tải, không cần ngồi canh):

```bash
mkdir -p results && STEP_USERS=20 STEP_DURATION=180 MAX_USERS=200 locust -f step_load.py --host http://localhost --headless --csv results/run1 --html results/run1.html
```

Lệnh trên tăng thêm 20 user mỗi 3 phút, từ 20 lên 200 user, tổng cộng khoảng 30 phút. Thời điểm bắt đầu mỗi bậc được in ra màn hình và ghi vào `step_load_timeline.csv`.

**Chỉ chạy một luồng:** thêm tên class vào cuối lệnh, ví dụ `locust -f locustfile.py --host http://localhost TaskUpdater` để chỉ test luồng ghi.

`--host` là lối vào cần test: `http://localhost` đi qua HAProxy trên Mac, `http://<IP node>` đi thẳng vào một node.

### Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `READ_WEIGHT` / `WRITE_WEIGHT` | 4 / 1 | Tỉ lệ người đọc : người ghi |
| `WAIT_MIN` / `WAIT_MAX` | 1 / 3 | Thời gian nghỉ (giây) giữa hai thao tác của một user |
| `REQUEST_TIMEOUT` | 10 | Quá thời gian này thì tính là lỗi |
| `LOGIN_TIMEOUT` / `LOGIN_RETRY_DELAY` | 30 / 5 | Timeout của `signin`, và thời gian nghỉ trước khi đăng nhập lại khi lỗi |
| `STEP_USERS` / `STEP_DURATION` / `MAX_USERS` / `SPAWN_RATE` | 20 / 180 / 200 / 5 | Cấu hình tăng tải theo bậc (`step_load.py`) |

## Đọc kết quả cùng Grafana

Kết quả ghi trong `results/run1_stats.csv`, `results/run1_failures.csv` và báo cáo HTML `results/run1.html`. Đối chiếu từng bậc trong `step_load_timeline.csv` với các dashboard trên Grafana (http://grafana.pms.local), chọn cùng khoảng thời gian:

- **Kubernetes / Compute Resources / Namespace (Pods)**, namespace `project-management-system`: CPU và RAM của backend, frontend, pms-db.
- **Kubernetes / Compute Resources / Node (Pods)** và **Node Exporter / Nodes**: CPU, RAM, tải đĩa của từng node.
- `kubectl -n project-management-system get hpa -w`: thời điểm HPA thêm pod.

Ngưỡng cần tìm là **bậc đầu tiên mà tỉ lệ lỗi hoặc p95 bắt đầu tăng rõ rệt**. Mức CPU và RAM tại bậc đó là căn cứ để đặt mức kích hoạt HPA (`averageUtilization`), `requests` và `limits`.

## Lưu ý

- **Locust chạy trên cùng máy Mac với HAProxy**, nên chúng tranh CPU của nhau. Khi số user lớn, hãy để mắt tới CPU của Mac. Nếu Mac quá tải, kết quả đo sẽ không còn chính xác.
- **Đo khi cụm đang rảnh.** Nếu có thành phần khác đang ngốn tài nguyên (ví dụ Rancher liên tục khởi động lại trên cụm VM), kết quả sẽ bị nhiễu.
- **Luồng ghi thay đổi dữ liệu thật** (trạng thái các task `[LoadTest]`). Chỉ chạy trên dữ liệu test.
