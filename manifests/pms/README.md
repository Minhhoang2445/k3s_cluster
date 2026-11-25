# PMS – Kubernetes manifests

Ứng dụng Project Management System (PMS) được triển khai trên cụm k3s bằng **YAML Kubernetes thuần** (không dùng Helm). Mỗi file chứa đúng **một object**, và các file được nhóm theo thành phần của hệ thống.

## Cấu trúc

```
manifests/pms/
├── config/
│   └── backend-configmap.yaml     ConfigMap   backend-config        cấu hình DB không bí mật
├── database/
│   ├── postgres-pvc.yaml          PVC         postgres-pvc          5Gi, local-path
│   ├── postgres-deployment.yaml   Deployment  postgres              1 pod, Recreate, pg_isready
│   └── postgres-service.yaml      Service     postgres-service      :5432
├── backend/
│   ├── deployment.yaml            Deployment  backend-deployment    Node.js :5000
│   ├── service.yaml               Service     backend-service       :5000
│   ├── hpa.yaml                   HPA         backend-hpa           3–6 pod, CPU 70%
│   └── pdb.yaml                   PDB         backend-pdb           maxUnavailable 1
├── frontend/
│   ├── deployment.yaml            Deployment  frontend-deployment   nginx :80
│   ├── service.yaml               Service     frontend-service      :8080 → 80
│   ├── hpa.yaml                   HPA         frontend-hpa          3–6 pod, CPU 70%
│   └── pdb.yaml                   PDB         frontend-pdb          maxUnavailable 1
└── gateway/
    ├── gatewayclass.yaml          GatewayClass traefik-gateway-class  do Traefik xử lý
    ├── gateway.yaml               Gateway     backend-gateway       listener HTTP :8000
    └── httproute.yaml             HTTPRoute   backend-http-route    /api → backend, / → frontend
```

Mọi object đều nằm trong namespace `project-management-system`, trừ `GatewayClass` là object cấp cụm.

## Luồng một request

```
Client ─▶ HAProxy (Mac) ─▶ node:80 ─▶ Traefik (3 pod trên 3 master)
          ─▶ Gateway backend-gateway ─▶ HTTPRoute backend-http-route
               ├─ /api/... ─▶ backend-service:5000 ─▶ pod backend ─▶ postgres-service:5432 ─▶ pod postgres
               └─ /...     ─▶ frontend-service:8080 ─▶ pod frontend (nginx)
```

## Các cơ chế HA của backend và frontend

| Cơ chế | Nằm ở | Tác dụng |
|---|---|---|
| HPA (min 3, max 6) | `hpa.yaml` | Luôn có ít nhất 3 bản sao, tự tăng khi CPU vượt 70% |
| Không khai báo `replicas` | `deployment.yaml` | Số pod do HPA quản lý; ArgoCD không kéo số pod về giá trị cố định |
| `topologySpreadConstraints` | `deployment.yaml` | Rải pod đều trên các node, nên một node chết chỉ mất một phần pod |
| Probes startup/readiness/liveness | `deployment.yaml` | Không gửi traffic tới pod chưa sẵn sàng; tự khởi động lại pod bị treo |
| `resources.requests` | `deployment.yaml` | Scheduler biết pod cần bao nhiêu tài nguyên; HPA dựa vào đây để tính % CPU |
| RollingUpdate `maxSurge 1 / maxUnavailable 0` | `deployment.yaml` | Deploy phiên bản mới không có downtime |
| Tolerations 30s | `deployment.yaml` | Pod trên node chết được thay sau khoảng 70 giây thay vì hơn 5 phút |
| PodDisruptionBudget | `pdb.yaml` | `kubectl drain` chỉ đuổi một pod mỗi lần |

PostgreSQL hiện chỉ có một instance. Đây là điểm chết duy nhất còn lại (sẽ thay bằng CloudNativePG).

## Secret (không nằm trong thư mục này)

Thông tin bí mật **không được lưu trong Git**. Playbook `ansible-k3s/playbooks/06_deploy_helm.yml` đọc Ansible Vault rồi tạo các Secret sau:

- `backend-secret`: `DATABASE_URL`, `ACCESS_TOKEN_SECRET`
- `postgres-secret`: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`

Namespace cũng không nằm trong thư mục này. Nó được tạo bởi playbook 06 hoặc bởi ArgoCD (`CreateNamespace=true`). Nhờ vậy, khi xoá ArgoCD Application, namespace và các Secret ở trên không bị xoá theo.

## Triển khai

**GitOps (mặc định):** ArgoCD Application `argoCD/pms-application.yaml` đọc thư mục này (`directory.recurse: true`). Mỗi lần push lên nhánh `main`, ArgoCD tự đồng bộ cụm theo Git: `prune` xoá các object bị bỏ khỏi Git, `selfHeal` hoàn tác các sửa tay trên cụm.

**Thủ công / bootstrap (playbook 06):**

```bash
kubectl apply --recursive -f manifests/pms
```

Kiểm tra trước khi apply:

```bash
kubectl apply --dry-run=server --recursive -f manifests/pms
```

```bash
kubectl diff --recursive -f manifests/pms
```

## Thay đổi thường gặp

- **Đổi phiên bản image:** sửa trường `image:` trong `backend/deployment.yaml` hoặc `frontend/deployment.yaml`.
- **Tắt autoscaling:** xoá file `hpa.yaml` và thêm `replicas: 3` vào `spec` của Deployment tương ứng.
- **Đổi giới hạn scale:** sửa `minReplicas` / `maxReplicas` trong `hpa.yaml`.
