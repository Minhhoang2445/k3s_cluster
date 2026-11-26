# Kịch bản test HA của cụm k3s

Hai kịch bản ngắn, chỉ kiểm tra tầng k3s (không test GitOps/ArgoCD).

Chuẩn bị chung:

- Chạy `kubectl` từ **một master không bị tắt**. Ví dụ, nếu tắt k3s-1 thì dùng `ssh k3s-2@172.31.9.68`.
- Mở sẵn 4 cửa sổ: Locust UI (http://localhost:8089), Grafana (http://grafana.pms.local), HAProxy stats (http://127.0.0.1:8404) và terminal trên master.
- Ghi lại giờ của từng bước (`date +%T`). Báo cáo cần **số giây**, không chỉ "hệ thống tự phục hồi".

---

## Kịch bản 1: Sập master đang là leader etcd

Mục tiêu là chứng minh 2 việc:

1. etcd bầu leader mới, và cụm vẫn hoạt động khi còn 2/3 master.
2. Các pod trên node chết được dựng lại ở node khác.

### Bước 0: Ghi trạng thái ban đầu

```bash
kubectl get nodes -o wide
kubectl -n project-management-system get pods -o wide
kubectl -n project-management-system get cluster.postgresql.cnpg.io pms-db   # primary DB đang ở đâu
```

**Tìm leader etcd.** Có 2 cách, chọn một:

- **Grafana → Explore**, chạy query `max by (instance) (etcd_server_is_leader)`. Node có giá trị 1 là leader.
- Trên một master (cần `sudo`):

  ```bash
  sudo journalctl -u k3s --no-pager | grep -E "elected leader|became leader" | tail -3
  ```

Lưu ý: tắt node leader có thể làm mất **primary DB** nếu primary DB cũng nằm trên node đó. Nếu trùng như vậy thì ghi lại cả quá trình failover của CNPG.

**Bật tải nhẹ để đo ảnh hưởng.** Trong Locust UI, đặt 20 user, spawn rate 5, rồi bấm Start.

### Bước 1: Tắt node leader (T0)

**Rút điện** Pi đó, hoặc chạy `sudo poweroff` trên node đó. **Không** dùng `systemctl stop k3s`, vì lệnh này để container tiếp tục chạy.

Theo dõi trên một master còn sống:

```bash
kubectl get nodes -w
```
```bash
kubectl -n project-management-system get pods -o wide -w
```
```bash
sudo journalctl -u k3s -f | grep -iE "leader|election|term"
```

Diễn biến kỳ vọng:

| Mốc | Kỳ vọng | Đo thực tế |
|---|---|---|
| etcd bầu leader mới (log `elected leader ... at term N`) | khoảng 5–6 giây (election-timeout 5000 ms) | |
| `kubectl` bị lỗi hoặc treo | khoảng 5–6 giây, rồi chạy lại bình thường | |
| HAProxy đánh dấu node `DOWN` | khoảng 6 giây | |
| Node chuyển `NotReady` | khoảng 40 giây | |
| Pod backend và frontend trên node chết bị đuổi, pod mới `Running` ở node khác | khoảng 70 giây (40 giây + toleration 30 giây) | |
| Locust: tổng số request lỗi, thời gian có lỗi | ít lỗi, tập trung trong vài giây đầu | |

Các pod sau sẽ **không** chạy lại ở node khác, và đây là đúng thiết kế:

- **Traefik:** pod thứ 3 ở trạng thái `Pending`. Traefik chỉ được chạy trên master, mỗi master tối đa 1 pod.
- **Instance Postgres:** instance trên node chết nằm chờ node đó, vì PVC `local-path` gắn với đĩa của node.

Grafana: `etcd_server_leader_changes_seen_total` tăng thêm 1, và `etcd_server_is_leader` chuyển sang node khác.

### Bước 2: Tắt thêm master thứ hai (mất quorum)

Lúc này chỉ còn 1/3 master.

| Kỳ vọng | Đo thực tế |
|---|---|
| `kubectl` lỗi: API server không trả lời vì etcd không còn leader | |
| Không có pod nào được dựng lại, HPA và CNPG không làm gì được | |
| Pod đang chạy vẫn tiếp tục chạy. Locust vẫn chạy được nếu Traefik, backend và primary DB còn sống | |

### Bước 3: Bật lại các node

| Mốc | Đo thực tế |
|---|---|
| Từ lúc bật node tới khi `kubectl` dùng lại được | |
| Tất cả node `Ready` | |
| Traefik đủ 3/3 pod, Postgres đủ 3/3 instance | |

Kubernetes **không tự dàn đều lại** các pod đã dời sang node khác. Muốn dàn lại thì chạy `kubectl rollout restart deployment/backend-deployment`.

---

## Kịch bản 2: Tăng tải để kiểm tra HPA

Cấu hình HPA hiện tại (`manifests/pms/*/hpa.yaml`):

| | backend | frontend |
|---|---|---|
| min / max | 3 / 8 | 3 / 4 |
| Ngưỡng | 70% của `requests.cpu` (250m), tức trung bình **175m mỗi pod** | 70% của 50m, tức **35m mỗi pod** |
| Scale up | ngay lập tức (`stabilizationWindowSeconds: 0`) | |
| Scale down | sau 5 phút dưới ngưỡng | |

Số pod HPA muốn có được tính bằng `ceil(số pod hiện tại × CPU hiện tại / CPU mục tiêu)`.

Đây là mức khởi đầu. Việc tìm ngưỡng tối ưu cần một lần test riêng, giữ số pod cố định (xem README).

### Bước 0: Mở các cửa sổ theo dõi

```bash
kubectl -n project-management-system get hpa -w
```
```bash
kubectl -n project-management-system get pods -l app=backend -o wide -w
```
```bash
watch -n 5 kubectl -n project-management-system top pods
```

Trên Grafana, mở dashboard **Kubernetes / Compute Resources / Namespace (Pods)** với namespace `project-management-system` và refresh 10 giây.

### Bước 1: Tăng tải dần

Chạy với bậc ngắn hơn mặc định để test nhanh: mỗi 2 phút thêm 10 user, tối đa 150 user, tổng khoảng 30 phút.

```bash
cd ~/Desktop/k3s_cluster/stress-test && source ../locust-venv/bin/activate
STEP_USERS=10 STEP_DURATION=120 MAX_USERS=150 locust -f step_load.py --host http://localhost
```

Mở http://localhost:8089 rồi bấm **Start**.

### Bước 2: Quan sát và ghi lại

| Mốc | Kỳ vọng | Đo thực tế |
|---|---|---|
| Bậc user mà cột `TARGETS` của HPA vượt `70%` | | |
| Thời gian từ khi vượt ngưỡng tới khi `REPLICAS` tăng | khoảng 15–30 giây | |
| Thời gian từ khi tạo pod mới tới khi pod `Ready` | khoảng 30–90 giây trên Pi (startup probe) | |
| Pod mới được trải sang các node (cột NODE) | trải đều nhờ topologySpread | |
| Lỗi trên Locust trong lúc scale | gần 0 | |
| Số pod tối đa đạt được | 8 (backend) | |
| Bậc user mà lỗi bắt đầu tăng sau khi đã đủ 8 pod | giới hạn của cụm | |

### Bước 3: Dừng tải

Bấm **Stop** trên Locust. Sau khoảng 5 phút, HPA phải giảm số pod về 3.

Ghi lại thời điểm bắt đầu giảm. Nếu đúng như kỳ vọng, kịch bản thành công.
