"""
Tạo project + task cho stress test và gán cho các tài khoản loadtest (từ accounts.csv).

Luồng backend đã đọc:
  1. POST /api/auth/signin                 -> lấy accessToken (JWT 15 phút), cần role admin
  2. GET  /api/admin/users?page=&limit=     -> map email -> userId (trả {page, limit, total, data})
  3. GET  /api/project                      -> (adminOnly) danh sách project, dùng để chạy lại không tạo trùng
  4. POST /api/project                      -> (adminOnly) body:
         name, description, status(planned|in_progress|completed|cancelled),
         startDate, endDate (startDate <= endDate),
         members: [{userId, role: project_leader|member}]  (ít nhất 1)
     -> project + projectMember tạo trong 1 transaction
  5. POST /api/project/:projectId/tasks     -> admin được coi là project_leader nên được tạo task.
         Không có zod validate ở route này; body: title, description, status, priority,
         assigneeId (BẮT BUỘC là member của project, status active), dueDate (ISO).
         assignee không thuộc project -> backend trả 500.
  6. GET  /api/project/:projectId/tasks     -> kiểm tra task đã có (idempotent)

Script idempotent: project nhận diện theo tên "[LoadTest] ...", task theo title. Chạy lại chỉ bù phần thiếu.
Kết quả ghi ra seed_data.json để locustfile dùng.

Cách chạy:
    source ../locust-venv/bin/activate
    python 02_seed_projects_tasks.py --host http://192.168.64.6 --admin-email <email-admin>
    (mật khẩu admin: nhập khi được hỏi, hoặc đặt biến môi trường ADMIN_PASSWORD)
"""

import argparse
import csv
import getpass
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).parent
ACCOUNTS_FILE = HERE / "accounts.csv"
OUTPUT_FILE = HERE / "seed_data.json"
PROJECT_PREFIX = "[LoadTest]"

PROJECT_THEMES = ["E-commerce Platform", "Mobile Banking App", "HR Management", "CRM System",
                  "Inventory Tracking", "Learning Portal", "Booking Service", "Analytics Dashboard"]
TASK_VERBS = ["Implement", "Fix", "Review", "Design", "Test", "Refactor", "Document", "Deploy"]
TASK_OBJECTS = ["login API", "payment flow", "user profile page", "search filter", "report export",
                "notification service", "database index", "CI pipeline", "dashboard chart", "file upload"]
TASK_STATUSES = ["todo", "todo", "in_progress", "in_progress", "review", "done"]
TASK_PRIORITIES = ["low", "medium", "medium", "high", "urgent"]


class Api:
    def __init__(self, host: str, email: str, password: str):
        self.base = f"{host.rstrip('/')}/api"
        self.email, self.password = email, password
        self.s = requests.Session()
        self.login()

    def login(self):
        r = self.s.post(f"{self.base}/auth/signin",
                        json={"email": self.email, "password": self.password}, timeout=15)
        if r.status_code != 200:
            sys.exit(f"Đăng nhập admin thất bại: {r.status_code} {r.text[:200]}")
        body = r.json()
        if body.get("user", {}).get("role") != "admin":
            sys.exit(f"Tài khoản {self.email} không phải admin (role={body.get('user', {}).get('role')})")
        self.s.headers["Authorization"] = f"Bearer {body['accessToken']}"

    def request(self, method: str, path: str, **kw) -> requests.Response:
        r = self.s.request(method, f"{self.base}{path}", timeout=30, **kw)
        if r.status_code == 401:  # access token 15 phút hết hạn -> đăng nhập lại
            self.login()
            r = self.s.request(method, f"{self.base}{path}", timeout=30, **kw)
        return r


def load_accounts() -> list[str]:
    if not ACCOUNTS_FILE.exists():
        sys.exit(f"Không thấy {ACCOUNTS_FILE}. Chạy 01_create_accounts.py trước.")
    with ACCOUNTS_FILE.open() as f:
        return [row["email"] for row in csv.DictReader(f)]


def fetch_user_ids(api: Api, emails: list[str]) -> dict[str, int]:
    wanted, found, page = set(emails), {}, 1
    while True:
        r = api.request("GET", "/admin/users", params={"page": page, "limit": 100, "sort": "id"})
        r.raise_for_status()
        body = r.json()
        for u in body["data"]:
            if u["email"] in wanted:
                if u["status"] != "active":
                    print(f"[WARN] {u['email']} status={u['status']} -> không gán task được")
                    continue
                found[u["email"]] = u["id"]
        if page * body["limit"] >= body["total"]:
            break
        page += 1
    missing = wanted - found.keys()
    if missing:
        print(f"[WARN] {len(missing)} tài khoản không tìm thấy/không active: {sorted(missing)[:5]}...")
    return found


def plan_projects(user_ids: list[int], n_projects: int, overlap: int) -> list[dict]:
    """Chia user thành n nhóm; người đầu nhóm là project_leader.
    Thêm `overlap` user của nhóm kế tiếp làm member -> một số user thuộc 2 project (giống thực tế)."""
    groups = [user_ids[i::n_projects] for i in range(n_projects)]
    plans = []
    for i, group in enumerate(groups):
        if not group:
            continue
        extra = [u for u in groups[(i + 1) % n_projects][:overlap] if u not in group]
        members = [{"userId": group[0], "role": "project_leader"}]
        members += [{"userId": u, "role": "member"} for u in group[1:] + extra]
        plans.append({
            "name": f"{PROJECT_PREFIX} {PROJECT_THEMES[i % len(PROJECT_THEMES)]} #{i + 1}",
            "members": members,
        })
    return plans


def ensure_project(api: Api, plan: dict, existing: dict[str, int], start: datetime, end: datetime) -> int:
    if plan["name"] in existing:
        pid = existing[plan["name"]]
        # bù member còn thiếu (PATCH chỉ thêm member chưa có, không xoá)
        r = api.request("PATCH", f"/project/{pid}", json={"members": plan["members"]})
        if r.status_code != 200:
            print(f"[WARN] cập nhật member project {pid}: {r.status_code} {r.text[:200]}")
        print(f"[SKIP] project #{pid} '{plan['name']}' đã có")
        return pid

    payload = {
        "name": plan["name"],
        "description": f"Project giả lập cho stress test - {len(plan['members'])} thành viên",
        "status": "in_progress",
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "members": plan["members"],
    }
    r = api.request("POST", "/project", json=payload)
    if r.status_code != 201:
        sys.exit(f"Tạo project '{plan['name']}' lỗi: {r.status_code} {r.text[:300]}")
    pid = r.json()["project"]["id"]
    print(f"[NEW ] project #{pid} '{plan['name']}' ({len(plan['members'])} members)")
    return pid


def ensure_tasks(api: Api, pid: int, project_no: int, members: list[dict], per_member: int,
                 start: datetime, end: datetime) -> tuple[list[dict], int, int]:
    r = api.request("GET", f"/project/{pid}/tasks")
    r.raise_for_status()
    existing = {t["title"]: t for t in r.json()}

    tasks, created, failed = [], 0, 0
    for m in members:
        uid = m["userId"]
        for k in range(1, per_member + 1):
            title = f"[LT] P{project_no}-U{uid}-T{k}"
            if title in existing:
                tasks.append({"id": existing[title]["id"], "assigneeId": uid})
                continue
            rnd = random.Random(f"{pid}-{uid}-{k}")
            due = start + timedelta(days=rnd.randint(1, (end - start).days))
            payload = {
                "title": title,
                "description": f"{rnd.choice(TASK_VERBS)} {rnd.choice(TASK_OBJECTS)}",
                "status": rnd.choice(TASK_STATUSES),
                "priority": rnd.choice(TASK_PRIORITIES),
                "assigneeId": uid,
                "dueDate": due.isoformat(),
            }
            r = api.request("POST", f"/project/{pid}/tasks", json=payload)
            if r.status_code == 201:
                created += 1
                tasks.append({"id": r.json()["task"]["id"], "assigneeId": uid})
            else:
                failed += 1
                print(f"[FAIL] task '{title}': {r.status_code} {r.text[:200]}")
    return tasks, created, failed


def main() -> int:
    p = argparse.ArgumentParser(description="Tạo project + task cho stress test")
    p.add_argument("--host", required=True, help="VD: http://192.168.64.6 (không kèm /api)")
    p.add_argument("--admin-email", required=True)
    p.add_argument("--projects", type=int, default=5)
    p.add_argument("--tasks-per-member", type=int, default=3)
    p.add_argument("--overlap", type=int, default=2, help="số user của nhóm kế tiếp được thêm vào mỗi project")
    args = p.parse_args()

    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass(f"Mật khẩu của {args.admin_email}: ")
    api = Api(args.host, args.admin_email, password)

    emails = load_accounts()
    id_by_email = fetch_user_ids(api, emails)
    user_ids = [id_by_email[e] for e in emails if e in id_by_email]
    if not user_ids:
        sys.exit("Không có user nào để gán.")
    print(f"Tìm thấy {len(user_ids)} tài khoản loadtest active")

    r = api.request("GET", "/project")
    r.raise_for_status()
    existing = {pr["name"]: pr["id"] for pr in r.json() if pr["name"].startswith(PROJECT_PREFIX)}

    now = datetime.now(timezone.utc).replace(microsecond=0)
    start, end = now - timedelta(days=30), now + timedelta(days=90)

    output, total_created, total_failed = [], 0, 0
    for i, plan in enumerate(plan_projects(user_ids, args.projects, args.overlap), start=1):
        pid = ensure_project(api, plan, existing, start, end)
        tasks, created, failed = ensure_tasks(api, pid, i, plan["members"], args.tasks_per_member, start, end)
        total_created += created
        total_failed += failed
        print(f"       task: tạo mới {created}, lỗi {failed}, tổng {len(tasks)}")
        output.append({"projectId": pid, "name": plan["name"], "members": plan["members"], "tasks": tasks})

    email_by_id = {v: k for k, v in id_by_email.items()}
    OUTPUT_FILE.write_text(json.dumps({
        "projects": output,
        "users": {email_by_id[u]: u for u in user_ids},
    }, indent=2, ensure_ascii=False))

    print(f"\nProject: {len(output)} | Task tạo mới: {total_created} | Task lỗi: {total_failed}")
    print(f"Đã ghi {OUTPUT_FILE}")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
